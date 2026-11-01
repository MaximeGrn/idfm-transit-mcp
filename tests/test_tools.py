from datetime import timedelta

import httpx
import pytest
from conftest import page

from idfm_transit_mcp.timeutils import now_paris, parse_datetime
from idfm_transit_mcp.tools import monitoring_refs


def place(name="Station", identifier="stop_area:IDFM:1"):
    return {
        "id": identifier,
        "name": name,
        "embedded_type": "stop_area",
        "stop_area": {"id": identifier, "name": name, "coord": {"lon": "2.3", "lat": "48.8"}},
    }


async def test_search_preserves_ambiguous_places(make_tools):
    t = make_tools(lambda req: httpx.Response(200, json={"places": [place(), place("Other")]}))
    search = await t.search_places("République")
    assert len(search.data["places"]) == 2
    assert search.data["places"][0]["coord"]["lon"] == "2.3"
    result = await t.plan_journey("Télégraphe", "Gare de l'Est")
    assert result.status == "ambiguous"
    assert result.data["unresolved_place"] == "Télégraphe"


async def test_empty_places_and_journey(make_tools):
    t = make_tools(lambda req: httpx.Response(200, json={"places": []}))
    assert (await t.search_places("no match")).status == "empty"
    assert (await t.plan_journey("no match", "other")).status == "empty"


async def test_journey_arrival_modes_and_normalization(make_tools):
    def handler(req):
        assert req.url.params["datetime"] == "20261101T200000"
        assert req.url.params["datetime_represents"] == "arrival"
        assert req.url.params.get_list("allowed_id[]") == ["physical_mode:Metro"]
        assert req.url.params["forbidden_uris[]"] == "line:IDFM:avoid"
        assert req.url.params["max_nb_transfers"] == "2"
        return httpx.Response(
            200,
            json={
                "journeys": [
                    {
                        "duration": 900,
                        "nb_transfers": 1,
                        "durations": {"walking": 120},
                        "departure_date_time": "20261101T194500",
                        "arrival_date_time": "20261101T200000",
                        "sections": [
                            {
                                "type": "public_transport",
                                "links": [{"type": "line", "id": "line:IDFM:example"}],
                                "display_informations": {"code": "11", "direction": "Destination"},
                                "from": {"name": "Origin"},
                                "to": {"name": "Destination"},
                            }
                        ],
                    }
                ]
            },
        )

    t = make_tools(handler)
    r = await t.plan_journey(
        "2.3;48.8",
        "stop_area:IDFM:2",
        "2026-11-01T19:00:00Z",
        "arrival",
        allowed_ids=["physical_mode:Metro"],
        excluded_ids=["line:IDFM:avoid"],
        max_transfers=2,
    )
    j = r.data["journeys"][0]
    assert j["duration_seconds"] == 900 and j["walking_seconds"] == 120
    assert j["arrival"] == "2026-11-01T20:00:00+01:00"
    assert j["sections"][0]["line_ids"] == ["line:IDFM:example"]


@pytest.mark.parametrize(
    "query,code,mode",
    [
        ("Métro 11", "11", "Metro"),
        ("Métro 5", "5", "Metro"),
        ("RER A", "A", "RER"),
        ("T3b", "T3b", "Tramway"),
    ],
)
async def test_line_names_resolve_without_hardcoded_ids(make_tools, query, code, mode):
    def handler(req):
        if req.url.path.endswith("pt_objects"):
            return httpx.Response(
                200,
                json={
                    "pt_objects": [
                        {
                            "embedded_type": "line",
                            "line": {
                                "id": "line:IDFM:chosen",
                                "code": code,
                                "commercial_mode": {"name": mode},
                            },
                        },
                        {
                            "embedded_type": "line",
                            "line": {
                                "id": "line:IDFM:bus",
                                "code": code,
                                "commercial_mode": {"name": "Bus"},
                                "network": {"name": "RER"},
                            },
                        },
                    ]
                },
            )
        assert "/line_reports/lines/line:IDFM:chosen/line_reports" in req.url.path
        return httpx.Response(200, json=page("line_reports", []))

    r = await make_tools(handler).get_line_status(query)
    assert r.data["line"]["id"] == "line:IDFM:chosen"
    assert r.data["assessment"] == "no_known_disruption"


async def test_line_ambiguity_is_not_guessed(make_tools):
    t = make_tools(
        lambda req: httpx.Response(
            200,
            json={
                "pt_objects": [
                    {"embedded_type": "line", "line": {"id": str(i), "code": "5"}} for i in range(2)
                ]
            },
        )
    )
    assert (await t.get_line_status("5")).status == "ambiguous"


def disruption(identifier, begin, end):
    return {
        "id": identifier,
        "application_periods": [
            {"begin": begin.strftime("%Y%m%dT%H%M%S"), "end": end.strftime("%Y%m%dT%H%M%S")}
        ],
        "messages": [{"text": "<p>Works</p>"}],
    }


async def test_pagination_collects_alerts_on_every_page(make_tools):
    now = now_paris()
    current = disruption("current", now - timedelta(hours=1), now + timedelta(hours=1))
    future = disruption("future", now + timedelta(days=1), now + timedelta(days=2))
    calls = []

    def handler(req):
        p = int(req.url.params["start_page"])
        calls.append(p)
        d = current if p == 0 else future
        report = {"line": {"id": f"line:{p}", "links": [{"type": "disruption", "id": d["id"]}]}}
        return httpx.Response(200, json=page("line_reports", [report], 2, [d]))

    r = await make_tools(handler).get_network_disruptions()
    assert calls == [0, 1]
    assert r.data["complete"] is True
    assert [d["timing"] for d in r.data["disruptions"]] == ["current", "scheduled"]
    assert r.data["disruptions"][1]["line_ids"] == ["line:1"]


async def test_incomplete_pagination_is_never_all_clear(make_tools):
    t = make_tools(lambda req: httpx.Response(200, json=page("line_reports", [], 20)))
    r = await t.get_network_disruptions()
    assert r.status == "partial"
    assert r.data["assessment"] == "information_unavailable"


async def test_max_pages_is_explicit(make_tools):
    t = make_tools(
        lambda req: httpx.Response(
            200, json=page("line_reports", [{"line": {"id": "line:1"}}], 100)
        ),
        max_pages=1,
    )
    assert (await t.get_network_disruptions()).data["complete"] is False


def siri(visits=None, status="true"):
    return {
        "Siri": {
            "ServiceDelivery": {
                "StopMonitoringDelivery": [{"Status": status, "MonitoredStopVisit": visits or []}]
            }
        }
    }


def visit(line="STIF:Line::C1:", expected=True):
    call = {"AimedDepartureTime": "2026-11-01T12:00:00Z"}
    if expected:
        call["ExpectedDepartureTime"] = "2026-11-01T12:02:00Z"
    return {
        "MonitoredVehicleJourney": {
            "LineRef": {"value": line},
            "DestinationName": [{"value": "Destination"}],
            "MonitoredCall": call,
        }
    }


async def test_siri_estimated_and_theoretical_and_filters(make_tools):
    t = make_tools(
        lambda req: httpx.Response(
            200, json=siri([visit(), visit(expected=False), visit("STIF:Line::C2:")])
        )
    )
    r = await t.get_next_departures("STIF:StopPoint:Q:1:", "STIF:Line::C1:", "destination")
    departures = r.data["departures"]
    assert [d["data_freshness"] for d in departures] == ["base_schedule", "realtime"]
    assert departures[1]["expected"] == "2026-11-01T13:02:00+01:00"


@pytest.mark.parametrize(
    "payload,status", [(siri(), "empty"), (siri(status="false"), "unavailable")]
)
async def test_siri_absent_data_is_honest(make_tools, payload, status):
    r = await make_tools(lambda req: httpx.Response(200, json=payload)).get_next_departures(
        "STIF:StopArea:SP:1:"
    )
    assert r.status == status and r.data["coverage"] == "unknown"


async def test_automatic_siri_mapping_uses_source_codes(make_tools):
    def handler(req):
        if req.url.path.endswith("stop-monitoring"):
            assert req.url.params["MonitoringRef"] == "STIF:StopArea:SP:123:"
            return httpx.Response(200, json=siri([visit()]))
        return httpx.Response(
            200,
            json={
                "stop_areas": [
                    {"codes": [{"type": "source", "value": "FR::multimodalStopPlace:123:FR1"}]}
                ]
            },
        )

    r = await make_tools(handler).get_next_departures("stop_area:IDFM:not-the-source-id")
    assert r.data["departures"][0]["data_freshness"] == "realtime"
    assert monitoring_refs({"id": "stop_area:IDFM:999"}) == []


async def test_empty_realtime_falls_back_only_when_allowed(make_tools):
    def handler(req):
        if req.url.path.endswith("stop-monitoring"):
            return httpx.Response(200, json=siri())
        if req.url.path.endswith("departures"):
            assert req.url.params["data_freshness"] == "base_schedule"
            return httpx.Response(
                200,
                json={
                    "departures": [{"stop_date_time": {"departure_date_time": "20261101T120000"}}]
                },
            )
        return httpx.Response(
            200,
            json={
                "stop_areas": [
                    {"codes": [{"type": "source", "value": "FR::multimodalStopPlace:123:FR1"}]}
                ]
            },
        )

    t = make_tools(handler)
    r = await t.get_next_departures("stop_area:IDFM:123", timetable="auto")
    assert r.data["departures"][0]["data_freshness"] == "base_schedule"
    assert "SIRI returned no matching" in r.warnings[0]
    r = await t.get_next_departures("stop_area:IDFM:123", timetable="realtime")
    assert r.status == "empty" and not r.data["departures"]


async def test_stop_info_returns_lines_quays_and_refs(make_tools):
    def handler(req):
        collection = req.url.path.rsplit("/", 1)[-1]
        if collection == "lines":
            return httpx.Response(200, json=page("lines", [{"id": "line:1", "code": "1"}]))
        if collection == "stop_points":
            return httpx.Response(200, json=page("stop_points", [{"id": "stop_point:1"}]))
        return httpx.Response(
            200,
            json={
                "stop_areas": [
                    {
                        "id": "stop_area:1",
                        "name": "Station",
                        "codes": [{"type": "source", "value": "FR::multimodalStopPlace:1:FR1"}],
                    }
                ]
            },
        )

    r = await make_tools(handler).get_stop_info("stop_area:1")
    assert r.data["stop"]["siri_monitoring_refs"] == ["STIF:StopArea:SP:1:"]
    assert len(r.data["lines"]) == len(r.data["stop"]["stop_points"]) == 1


@pytest.mark.parametrize(
    "payload,assessment",
    [
        (page("line_reports", []), "no_known_disruption"),
        ({"line_reports": []}, "information_unavailable"),
    ],
)
async def test_journey_assessment_is_conservative(make_tools, payload, assessment):
    t = make_tools(lambda req: httpx.Response(200, json=payload))
    r = await t.check_journey_status(
        {
            "sections": [
                {
                    "id": "section",
                    "type": "public_transport",
                    "line_ids": ["line:1"],
                    "departure": "2026-11-01T12:00:00+01:00",
                    "arrival": "2026-11-01T12:30:00+01:00",
                }
            ]
        }
    )
    assert r.data["assessment"] == assessment
    missing = await t.check_journey_status({"sections": [{"type": "public_transport"}]})
    assert missing.data["assessment"] == "information_unavailable"


@pytest.mark.parametrize(
    "operation,args",
    [
        ("search_places", {"query": " "}),
        ("plan_journey", {"origin": "200;48", "destination": "other"}),
        ("plan_journey", {"origin": "A", "destination": "B", "datetime": "bad"}),
        ("get_stop_info", {"stop_id": "../../secret"}),
        ("get_next_departures", {"stop_id": "STIF:bad", "limit": 999}),
        ("get_network_disruptions", {"since": "2026-11-02T12:00", "until": "2026-11-01T12:00"}),
    ],
)
async def test_invalid_inputs_do_not_send_requests(make_tools, operation, args):
    def handler(req):
        pytest.fail("Invalid input should be rejected before HTTP")

    with pytest.raises(ValueError):
        await getattr(make_tools(handler), operation)(**args)


@pytest.mark.parametrize("value", ["2026-03-29T02:30:00", "2026-10-25T02:30:00"])
def test_dst_gaps_and_folds_require_offset(value):
    with pytest.raises(ValueError, match="explicit offset"):
        parse_datetime(value)


def test_explicit_dst_offset():
    assert parse_datetime("2026-10-25T02:30:00+02:00").utcoffset().total_seconds() == 7200


async def test_network_output_window_does_not_omit_alerts_silently(make_tools):
    now = now_paris()
    alerts = [disruption(str(i), now, now + timedelta(hours=1)) for i in range(3)]
    t = make_tools(lambda req: httpx.Response(200, json=page("line_reports", [], 0, alerts)))
    result = await t.get_network_disruptions(offset=1, limit=1)
    assert result.data["total_disruptions"] == 3
    assert result.data["next_offset"] == 2
    assert result.data["disruptions"][0]["id"] == "1"
    assert result.warnings
