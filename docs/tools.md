# Tool reference

MCP discovery returns the authoritative JSON schemas. All tools are read-only.
`status` is `ok`, `empty`, `ambiguous`, `partial` or `unavailable`. An API failure
is a structured `unavailable` result with a stable error code; invalid MCP schema
arguments may instead be rejected by the SDK before execution.

## search_places

`query` (nonempty text), `limit` (1–20, default 10). Results include `id`, `type`,
name/label and coordinates when available. Multiple matches carry a warning.
Search then select a candidate; matching a name is not proof of its uniqueness.

```json
{"query": "Télégraphe", "limit": 10}
```

## plan_journey

Required `origin` and `destination`: official place IDs, `longitude;latitude`,
or names/addresses. Text is searched first; ambiguous input returns candidates
without planning. Optional `datetime` (ISO date-time; default now),
`datetime_represents` (`departure` or `arrival`), `limit` (1–5, default 3),
`allowed_ids`, `excluded_ids`, `wheelchair` and `max_transfers` (0–10).

Allowed/excluded IDs are official `line:`, `physical_mode:`, `commercial_mode:`,
`network:`, `stop_area:` or `stop_point:` IDs. For example, PRIM uses
`physical_mode:Metro`, `physical_mode:RapidTransit`, `physical_mode:Train`,
`physical_mode:Tramway`, `physical_mode:Bus`. Availability depends on provider data.
Wheelchair preference is a routing request, not an accessibility certification.

```json
{
  "origin": "2.35;48.86",
  "destination": "2.37;48.88",
  "datetime": "2026-11-02T20:00:00+01:00",
  "datetime_represents": "arrival",
  "excluded_ids": ["physical_mode:Bus"],
  "max_transfers": 2
}
```

Each journey contains total/walking seconds, transfers, departure/arrival, and
sections with stops, line IDs, displayed line, direction, schedules and freshness.
Optional provider fields remain null/absent rather than invented.

## get_line_status

`line` accepts a PRIM line ID or common mode/name (`Métro 11`, `RER A`, `T3b`,
`Transilien N`, `Bus 24`). Optional `since`/`until` restrict the period. Mode is
matched from the line's mode fields; replacement buses belonging to a rail
network are not mistaken for trains. Reused bus numbers can return ambiguity.

## get_network_disruptions

Optional exact `line_id`, `physical_mode_id`, `since`, `until`, `offset` and
`limit` (1–100, default 20). Follow `next_offset` to read further normalized alerts;
`total_disruptions` includes every alert found on the upstream pages. Date values are
ISO date-times. Every report page is traversed up to the configured safety bound;
`complete` records traversal completeness. Alerts are deduplicated by ID.
`timing` distinguishes `current`, `scheduled`, `past` and `unknown`, preserving
application periods and the provider's own status. Messages are deduplicated and
limited to 1,000 characters each; `messages_truncated` explicitly marks shortening.
Link references connect each
alert to its lines and affected objects.

## get_next_departures

Required `stop_id`; optional `line_id`, `direction` (destination substring),
`limit` (1–50, default 10), `timetable` (`auto`, `realtime`, `theoretical`).

- `auto`: resolve Navitia stops through explicit PRIM source codes, query SIRI,
  then clearly label a theoretical fallback if no matching passages are reported.
- `realtime`: SIRI only; never imply an empty response is a theoretical timetable.
- `theoretical`: Navitia timetable only; requires a Navitia stop ID.

SIRI references use `STIF:StopArea:SP:<reference>:` or
`STIF:StopPoint:Q:<reference>:`. Navitia uses `stop_area:...` or `stop_point:...`.
`get_stop_info` returns verified source-derived SIRI references when available;
the server never converts arbitrary identifier suffixes into supposed mappings.
IDFM `line:IDFM:C...` filters are converted to the documented `STIF:Line::C...:`
pattern when a Navitia stop is resolved for SIRI. Direct SIRI calls require SIRI
line IDs. Unknown mappings return unavailable in realtime-only mode.

Each passage distinguishes `expected` from `scheduled` and carries
`data_freshness`: `realtime`, `base_schedule` or `unknown`. SIRI cancellation
statuses are retained. Check them before recommending a departure. `recorded_at`
is preserved when provided; estimates may be stale. `truncated=true` indicates
the result limit omitted additional returned passages. Direction filtering covers
the API's current response window; it does not search indefinitely into the future.

## get_stop_info

`stop_id`: a Navitia station/stop ID. Returns coordinates, codes, serving lines,
quays, available equipment and source-derived SIRI monitoring references. Serving
lines and quays are paginated; `complete=false` indicates a safety bound.

## check_journey_status

Pass `journey`: one object from `plan_journey.data.journeys`. Each public
transport section is checked against line alerts for its departure/arrival period.
The result contains a conservative overall assessment and per-section assessments:

| Assessment | Meaning |
| --- | --- |
| `disruption_detected` | A line alert may affect this segment; inspect it. |
| `no_known_disruption` | A complete query returned no known alert; no guarantee. |
| `information_unavailable` | Missing line information, failed query or incomplete coverage. |

Walking geometry and exact sub-line disruption applicability are not analyzed.
The tool does not send notifications or access calendars.
