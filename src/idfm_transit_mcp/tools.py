"""Transport operations independent of any assistant, calendar, or personal profile."""

import re
import unicodedata
from typing import Any
from urllib.parse import quote

from .client import NAVITIA, TRAFFIC, PrimClient
from .models import Result, TransitError
from .timeutils import navitia_datetime, now_paris, output_datetime, parse_datetime


def normalized(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c)
    ).strip()


def text(value: str, name: str, maximum: int = 300) -> str:
    if not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(f"{name} must be nonempty text of at most {maximum} characters.")
    return value.strip()


def identity(value: str, prefixes: tuple[str, ...]) -> str:
    value = text(value, "identifier")
    if not value.startswith(prefixes) or any(c in value for c in '/\\?&#"'):
        raise ValueError("Use an identifier returned by PRIM for this object type.")
    return value


def brief(obj: dict[str, Any]) -> dict[str, Any]:
    return {k: obj[k] for k in ("id", "name", "label", "coord", "code") if k in obj}


def line_brief(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        **brief(obj),
        "commercial_mode": obj.get("commercial_mode"),
        "physical_modes": obj.get("physical_modes", []),
        "network": brief(obj.get("network", {})),
    }


def monitoring_refs(obj: dict[str, Any]) -> list[str]:
    """Use explicit IDFM source codes, not an arbitrary Navitia ID suffix."""
    refs = set()
    for code in obj.get("codes", []):
        if code.get("type") == "source":
            match = re.fullmatch(
                r"FR::(multimodalStopPlace|monomodalStopPlace|Quay):(\d+):FR1",
                code.get("value", ""),
            )
            if match:
                family = "StopPoint:Q" if match[1] == "Quay" else "StopArea:SP"
                refs.add(f"STIF:{family}:{match[2]}:")
        if code.get("type") == "netex_monomodal_stopplace":
            match = re.fullmatch(r"monomodalStopPlace:(\d+)", code.get("value", ""))
            if match:
                refs.add(f"STIF:StopArea:SP:{match[1]}:")
    return sorted(refs)


def linked_disruptions(obj: Any) -> set[str]:
    if isinstance(obj, list):
        return set().union(*(linked_disruptions(x) for x in obj)) if obj else set()
    if isinstance(obj, dict):
        ids = {
            x["id"] for x in obj.get("links", []) if x.get("type") == "disruption" and x.get("id")
        }
        for k, value in obj.items():
            if k != "links":
                ids.update(linked_disruptions(value))
        return ids
    return set()


class TransitTools:
    def __init__(self, client: PrimClient):
        self.client = client

    async def search_places(self, query: str, limit: int = 10) -> Result:
        query = text(query, "query")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        data = await self.client.get(NAVITIA + "/places", {"q": query, "count": limit}, 3600)
        if "places" not in data:
            raise TransitError("invalid_response", "PRIM response lacks places.")
        places = []
        for place in data["places"]:
            kind = place.get("embedded_type", "unknown")
            places.append(
                {
                    **brief(place),
                    "type": kind,
                    **brief(place.get(kind, {})),
                    "quality": place.get("quality"),
                }
            )
        return Result(
            status="ok" if places else "empty",
            data={"places": places},
            warnings=["Multiple matches; select an explicit identifier before planning."]
            if len(places) > 1
            else [],
        )

    async def _place(self, value: str) -> str | Result:
        value = text(value, "place")
        if value.startswith(("stop_area:", "stop_point:", "address:", "poi:")):
            return identity(value, ("stop_area:", "stop_point:", "address:", "poi:"))
        if ";" in value:
            try:
                lon, lat = map(float, value.split(";"))
                if not -180 <= lon <= 180 or not -90 <= lat <= 90:
                    raise ValueError
            except ValueError:
                raise ValueError("Coordinates must be longitude;latitude in WGS84.") from None
            return value
        result = await self.search_places(value)
        places = result.data["places"]
        if len(places) == 1:
            return places[0]["id"]
        return Result(
            status="ambiguous" if places else "empty",
            data={"unresolved_place": value, "candidates": places},
            warnings=["Select a place ID; no location was chosen automatically."],
        )

    async def plan_journey(
        self,
        origin: str,
        destination: str,
        datetime: str | None = None,
        datetime_represents: str = "departure",
        limit: int = 3,
        allowed_ids: list[str] | None = None,
        excluded_ids: list[str] | None = None,
        wheelchair: bool = False,
        max_transfers: int | None = None,
    ) -> Result:
        if datetime_represents not in ("departure", "arrival") or not 1 <= limit <= 5:
            raise ValueError("datetime_represents must be departure/arrival; limit must be 1..5.")
        if max_transfers is not None and not 0 <= max_transfers <= 10:
            raise ValueError("max_transfers must be 0..10.")
        params: dict[str, Any] = {
            "datetime": navitia_datetime(datetime)
            if datetime
            else now_paris().strftime("%Y%m%dT%H%M%S"),
            "datetime_represents": datetime_represents,
            "count": limit,
            "wheelchair": str(wheelchair).lower(),
            "disable_geojson": "true",
        }
        for key, values in (("allowed_id[]", allowed_ids), ("forbidden_uris[]", excluded_ids)):
            if values:
                if len(values) > 20:
                    raise ValueError("At most 20 allowed/excluded IDs are accepted.")
                params[key] = [
                    identity(
                        v,
                        (
                            "line:",
                            "physical_mode:",
                            "commercial_mode:",
                            "network:",
                            "stop_area:",
                            "stop_point:",
                        ),
                    )
                    for v in values
                ]
        if max_transfers is not None:
            params["max_nb_transfers"] = max_transfers
        for key, value in (("from", origin), ("to", destination)):
            place = await self._place(value)
            if isinstance(place, Result):
                return place
            params[key] = place
        data = await self.client.get(NAVITIA + "/journeys", params, 20)
        if "journeys" not in data:
            raise TransitError("invalid_response", "PRIM response lacks journeys.")
        journeys = []
        for journey in data["journeys"][:limit]:
            sections = []
            for section in journey.get("sections", []):
                info = section.get("display_informations", {})
                line_ids = [
                    link["id"] for link in section.get("links", []) if link.get("type") == "line"
                ]
                sections.append(
                    {
                        "id": section.get("id"),
                        "type": section.get("type"),
                        "duration_seconds": section.get("duration"),
                        "from": brief(section.get("from", {})),
                        "to": brief(section.get("to", {})),
                        "departure": output_datetime(section.get("departure_date_time")),
                        "arrival": output_datetime(section.get("arrival_date_time")),
                        "line_ids": line_ids,
                        "line": info.get("code"),
                        "mode": info.get("physical_mode"),
                        "direction": info.get("direction"),
                        "data_freshness": section.get("data_freshness", "unknown"),
                        "stops": [
                            {
                                "stop": brief(s.get("stop_point", {})),
                                "arrival": output_datetime(s.get("arrival_date_time")),
                                "departure": output_datetime(s.get("departure_date_time")),
                            }
                            for s in section.get("stop_date_times", [])
                        ],
                        "disruption_ids": sorted(linked_disruptions(section)),
                    }
                )
            journeys.append(
                {
                    "duration_seconds": journey.get("duration"),
                    "walking_seconds": journey.get("durations", {}).get("walking"),
                    "transfers": journey.get("nb_transfers"),
                    "departure": output_datetime(journey.get("departure_date_time")),
                    "arrival": output_datetime(journey.get("arrival_date_time")),
                    "sections": sections,
                }
            )
        return Result(
            status="ok" if journeys else "empty",
            data={"journeys": journeys, "provider_notice": data.get("provider_notice")},
            warnings=["Schedules and accessibility are provider estimates; recheck before travel."],
        )

    async def _line(self, query: str) -> dict[str, Any] | Result:
        query = text(query, "line")
        if query.startswith("line:"):
            line_id = identity(query, ("line:",))
            data = await self.client.get(NAVITIA + "/lines/" + quote(line_id, safe=""), ttl=3600)
            return data["lines"][0] if data.get("lines") else Result(status="empty")
        q = normalized(query)
        mode = next(
            (m for m in ("metro", "rer", "tram", "transilien", "bus") if q.startswith(m + " ")),
            None,
        )
        code = q[len(mode) :].strip() if mode else q
        if code.startswith("t") and code[1:2].isdigit() and not mode:
            mode = "tram"
        data = await self.client.get(
            NAVITIA + "/pt_objects",
            {
                "q": code,
                "type[]": ["line"],
                "count": 100,
                "depth": 2,
            },
            3600,
        )
        lines = [
            obj.get("line", {})
            for obj in data.get("pt_objects", [])
            if obj.get("embedded_type") == "line"
        ]
        matches = []
        for line in lines:
            line_code = normalized(line.get("code", ""))
            mode_text = normalized(
                str(line.get("commercial_mode", {})) + str(line.get("physical_modes", []))
            )
            mode_words = {
                "metro": ("metro",),
                "rer": ("rer", "rapidtransit"),
                "tram": ("tram",),
                "transilien": ("transilien", "train", "localtrain"),
                "bus": ("bus",),
            }
            if line_code == code and (not mode or any(w in mode_text for w in mode_words[mode])):
                matches.append(line)
        if len(matches) == 1 and not (len(lines) >= 100 and mode in (None, "bus")):
            return matches[0]
        return Result(
            status="ambiguous" if matches or lines else "empty",
            data={"candidates": [line_brief(x) for x in matches or lines]},
            warnings=["Select an exact line ID; line names can be ambiguous."],
        )

    async def get_line_status(
        self, line: str, since: str | None = None, until: str | None = None
    ) -> Result:
        resolved = await self._line(line)
        if isinstance(resolved, Result):
            return resolved
        result = await self.get_network_disruptions(
            line_id=resolved["id"], since=since, until=until
        )
        result.data["line"] = line_brief(resolved)
        return result

    async def get_network_disruptions(
        self,
        line_id: str | None = None,
        physical_mode_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> Result:
        if not 0 <= offset <= 100000 or not 1 <= limit <= 100:
            raise ValueError("offset must be 0..100000 and limit must be 1..100.")
        path = TRAFFIC
        if line_id:
            path += "/lines/" + quote(identity(line_id, ("line:",)), safe="")
        if physical_mode_id:
            path += "/physical_modes/" + quote(
                identity(physical_mode_id, ("physical_mode:",)), safe=""
            )
        path += "/line_reports"
        params = {"depth": 3, "disable_geojson": "true"}
        for key, value in (("since", since), ("until", until)):
            if value:
                params[key] = navitia_datetime(value)
        if since and until and parse_datetime(since) > parse_datetime(until):
            raise ValueError("since must be before until")
        reports, disruptions, complete = await self.client.pages(path, "line_reports", params, 60)
        lines_by_disruption: dict[str, set[str]] = {}
        for report in reports:
            for disruption_id in linked_disruptions(report):
                lines_by_disruption.setdefault(disruption_id, set()).add(report["line"]["id"])
        now = now_paris()
        result = []
        for disruption in disruptions:
            periods = disruption.get("application_periods", [])
            timing = "unknown"
            try:
                ranges = [(parse_datetime(p["begin"]), parse_datetime(p["end"])) for p in periods]
                if any(begin <= now < end for begin, end in ranges):
                    timing = "current"
                elif any(begin > now for begin, _ in ranges):
                    timing = "scheduled"
                elif ranges:
                    timing = "past"
            except (ValueError, KeyError):
                pass
            result.append(
                {
                    "id": disruption.get("id"),
                    "title": disruption.get("cause"),
                    "timing": timing,
                    "provider_status": disruption.get("status"),
                    "severity": disruption.get("severity"),
                    "updated_at": output_datetime(disruption.get("updated_at")),
                    "application_periods": [
                        {
                            "begin": output_datetime(p.get("begin")),
                            "end": output_datetime(p.get("end")),
                        }
                        for p in periods
                    ],
                    "messages": list(
                        dict.fromkeys(
                            re.sub("<[^>]+>", "", m.get("text", ""))[:1000]
                            for m in disruption.get("messages", [])
                        )
                    ),
                    "messages_truncated": any(
                        len(m.get("text", "")) > 1000 for m in disruption.get("messages", [])
                    ),
                    "affected_objects": [
                        brief(x.get("pt_object", {}))
                        for x in disruption.get("impacted_objects", [])
                    ],
                    "line_ids": sorted(lines_by_disruption.get(disruption.get("id"), set())),
                }
            )
        return Result(
            status=("ok" if result else "empty") if complete else "partial",
            data={
                "disruptions": result[offset : offset + limit],
                "complete": complete,
                "total_disruptions": len(result),
                "offset": offset,
                "next_offset": offset + limit if offset + limit < len(result) else None,
                "assessment": "disruption_detected"
                if result
                else "no_known_disruption"
                if complete
                else "information_unavailable",
            },
            warnings=([] if complete else ["API pagination incomplete; absence is not conclusive."])
            + (
                ["Additional alerts are available; continue with next_offset."]
                if offset + limit < len(result)
                else []
            ),
        )

    async def get_stop_info(self, stop_id: str) -> Result:
        stop_id = identity(stop_id, ("stop_area:", "stop_point:"))
        collection = "stop_areas" if stop_id.startswith("stop_area:") else "stop_points"
        path = NAVITIA + "/" + collection + "/" + quote(stop_id, safe="")
        data = await self.client.get(path, {"depth": 2}, 3600)
        if not data.get(collection):
            return Result(status="empty", data={"stop": None})
        stop = data[collection][0]
        lines, _, complete = await self.client.pages(path + "/lines", "lines", ttl=3600)
        points = stop.get("stop_points", [])
        if collection == "stop_areas":
            points, _, points_complete = await self.client.pages(
                path + "/stop_points", "stop_points", {"depth": 1}, ttl=3600
            )
            complete &= points_complete
        return Result(
            status="ok" if complete else "partial",
            data={
                "stop": {
                    **brief(stop),
                    "codes": stop.get("codes", []),
                    "siri_monitoring_refs": monitoring_refs(stop),
                    "stop_points": [
                        {**brief(s), "siri_monitoring_refs": monitoring_refs(s)} for s in points
                    ],
                    "equipment": stop.get("equipments", []),
                },
                "lines": [line_brief(line) for line in lines],
                "complete": complete,
            },
        )

    async def get_next_departures(
        self,
        stop_id: str,
        line_id: str | None = None,
        direction: str | None = None,
        limit: int = 10,
        timetable: str = "auto",
    ) -> Result:
        if not 1 <= limit <= 50:
            raise ValueError("limit must be 1..50")
        if timetable not in ("auto", "realtime", "theoretical"):
            raise ValueError("timetable must be auto, realtime, or theoretical.")
        if direction:
            direction = text(direction, "direction")
        fallback_reason = None
        if not stop_id.startswith("STIF:") and timetable != "theoretical":
            stop_id = identity(stop_id, ("stop_area:", "stop_point:"))
            collection = "stop_areas" if stop_id.startswith("stop_area:") else "stop_points"
            stop_data = await self.client.get(
                NAVITIA + "/" + collection + "/" + quote(stop_id, safe=""), {"depth": 1}, 3600
            )
            objects = stop_data.get(collection, [])
            refs = monitoring_refs(objects[0]) if objects else []
            siri_line = line_id
            if line_id and (match := re.fullmatch(r"line:IDFM:(C\d+)", line_id)):
                siri_line = f"STIF:Line::{match[1]}:"
            if refs:
                realtime = await self.get_next_departures(
                    refs[0], siri_line, direction, limit, "realtime"
                )
                if realtime.data.get("departures") or timetable == "realtime":
                    return realtime
                fallback_reason = "SIRI returned no matching realtime passages."
            else:
                fallback_reason = "No explicit IDFM source code maps this stop to SIRI."
            if timetable == "realtime":
                return Result(
                    status="unavailable",
                    data={"departures": [], "coverage": "unknown"},
                    warnings=[fallback_reason],
                )
        if stop_id.startswith("STIF:"):
            if timetable == "theoretical":
                raise ValueError("Theoretical timetables require a Navitia stop ID.")
            if not re.fullmatch(r"STIF:Stop(?:Point:Q|Area:SP):[A-Za-z0-9_-]+:", stop_id):
                raise ValueError("Use a documented SIRI MonitoringRef for a quay or stop area.")
            if line_id:
                if not re.fullmatch(r"STIF:Line::[A-Za-z0-9_-]+:", line_id):
                    raise ValueError("SIRI filtering requires a STIF:Line::...: identifier.")
            data = await self.client.get("/stop-monitoring", {"MonitoringRef": stop_id}, 15)
            service = data.get("Siri", {}).get("ServiceDelivery", {})
            deliveries = service.get("StopMonitoringDelivery")
            if not isinstance(deliveries, list) or not deliveries:
                raise TransitError("invalid_response", "PRIM response lacks SIRI stop deliveries.")
            departures, errors = [], []
            for delivery in deliveries:
                if str(delivery.get("Status", True)).lower() == "false" or delivery.get(
                    "ErrorCondition"
                ):
                    errors.append(
                        "SIRI reports that this stop's departure information is unavailable."
                    )
                    continue
                for visit in delivery.get("MonitoredStopVisit", []):
                    journey = visit.get("MonitoredVehicleJourney", {})
                    call = journey.get("MonitoredCall", {})
                    line = journey.get("LineRef", {}).get("value")
                    heads = journey.get("DestinationName") or journey.get("DirectionName") or []
                    head = " / ".join(x.get("value", "") for x in heads)
                    if line_id and line != line_id:
                        continue
                    if direction and normalized(direction) not in normalized(head):
                        continue
                    expected = call.get("ExpectedDepartureTime") or call.get("ExpectedArrivalTime")
                    aimed = call.get("AimedDepartureTime") or call.get("AimedArrivalTime")
                    departures.append(
                        {
                            "line_id": line,
                            "direction": head,
                            "expected": output_datetime(expected),
                            "scheduled": output_datetime(aimed),
                            "time": output_datetime(expected or aimed),
                            "data_freshness": "realtime"
                            if expected
                            else "base_schedule"
                            if aimed
                            else "unknown",
                            "departure_status": call.get("DepartureStatus"),
                            "recorded_at": output_datetime(visit.get("RecordedAtTime")),
                            "stop_ref": visit.get("MonitoringRef", {}).get("value", stop_id),
                        }
                    )
            departures.sort(key=lambda d: d.get("time") or "9999")
            if errors and not departures:
                return Result(
                    status="unavailable",
                    data={"departures": [], "coverage": "unknown"},
                    warnings=errors,
                )
            return Result(
                status="partial" if errors else "ok" if departures else "empty",
                data={
                    "departures": departures[:limit],
                    "coverage": "reported" if departures else "unknown",
                    "truncated": len(departures) > limit,
                },
                warnings=errors
                or (
                    [] if departures else ["No reported passages; coverage/service may be absent."]
                ),
            )
        stop_id = identity(stop_id, ("stop_area:", "stop_point:"))
        collection = "stop_areas" if stop_id.startswith("stop_area:") else "stop_points"
        path = NAVITIA + "/" + collection + "/" + quote(stop_id, safe="")
        if line_id:
            path += "/lines/" + quote(identity(line_id, ("line:",)), safe="")
        # Explicit theoretical mode, or a clearly labelled fallback after an empty SIRI reply.
        data = await self.client.get(
            path + "/departures",
            {
                "count": 100,
                "data_freshness": "base_schedule",
                "depth": 2,
            },
            30,
        )
        if "departures" not in data:
            raise TransitError("invalid_response", "PRIM response lacks departures.")
        departures = []
        for departure in data["departures"]:
            info, times = (
                departure.get("display_informations", {}),
                departure.get("stop_date_time", {}),
            )
            head = info.get("direction", "")
            if direction and normalized(direction) not in normalized(head):
                continue
            departures.append(
                {
                    "line": info.get("code"),
                    "direction": head,
                    "stop": brief(departure.get("stop_point", {})),
                    "time": output_datetime(times.get("departure_date_time")),
                    "data_freshness": times.get("data_freshness", "base_schedule"),
                }
            )
        return Result(
            status="ok" if departures else "empty",
            data={
                "departures": departures[:limit],
                "truncated": len(departures) > limit,
                "provider_notice": data.get("provider_notice"),
            },
            warnings=([fallback_reason] if fallback_reason else [])
            + ["Theoretical timetable; these are not realtime estimates."],
        )

    async def check_journey_status(self, journey: dict[str, Any]) -> Result:
        sections = journey.get("sections")
        if not isinstance(sections, list) or not 1 <= len(sections) <= 50:
            raise ValueError("Pass one journey returned by plan_journey (1..50 sections).")
        segments, states = [], []
        for section in sections:
            if section.get("type") not in ("public_transport", "on_demand_transport"):
                continue
            ids = section.get("line_ids", [])
            if not isinstance(ids, list) or len(ids) > 5:
                raise ValueError("A section must have at most five line IDs.")
            status, disruption_ids = "information_unavailable", []
            has_times = bool(section.get("departure") and section.get("arrival"))
            complete = bool(ids) and has_times
            for line in ids if has_times else []:
                try:
                    result = await self.get_network_disruptions(
                        line_id=line, since=section.get("departure"), until=section.get("arrival")
                    )
                    complete &= result.data["complete"]
                    disruption_ids.extend(d["id"] for d in result.data["disruptions"])
                except TransitError:
                    complete = False
            if disruption_ids:
                status = "disruption_detected"
            elif complete:
                status = "no_known_disruption"
            states.append(status)
            segments.append(
                {
                    "section_id": section.get("id"),
                    "line_ids": ids,
                    "assessment": status,
                    "potentially_affected": None
                    if status == "information_unavailable"
                    else bool(disruption_ids),
                    "disruption_ids": sorted(set(disruption_ids)),
                }
            )
        assessment = (
            "disruption_detected"
            if "disruption_detected" in states
            else "information_unavailable"
            if not states or "information_unavailable" in states
            else "no_known_disruption"
        )
        return Result(
            status="partial" if "information_unavailable" in states or not states else "ok",
            data={"assessment": assessment, "segments": segments},
            warnings=[
                "Line-level alerts flag potentially affected segments; "
                "no known disruption is never a guarantee of an unaffected journey."
            ],
        )
