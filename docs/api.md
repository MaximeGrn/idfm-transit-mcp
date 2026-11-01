# API evidence and operating limits

Reviewed and exercised on 2026-11-01. PRIM remains the source of truth.

| Product | Paths used below `https://prim.iledefrance-mobilites.fr/marketplace` |
| --- | --- |
| Navitia generic v2 | `/v2/navitia/places`, `/pt_objects`, `/journeys`, `/lines/{id}`, `/stop_areas/{id}`, `/stop_points/{id}` and related `/lines`, `/stop_points`, `/departures` paths |
| Navitia traffic v2 | `/v2/navitia/line_reports/line_reports`, including `/lines/{id}` or `/physical_modes/{id}` before the final `line_reports` |
| SIRI next passages | `/stop-monitoring?MonitoringRef=...` |

All requests carry the PRIM token in the `apiKey` HTTP header. Navitia's generic
documentation describes other authentication methods for other deployments;
this server uses PRIM's method and does not send an Authorization token to Navitia.io.

Official references:

- [Generic v2 product](https://prim.iledefrance-mobilites.fr/apis/idfm-navitia-general-v2)
- [Traffic v2 product](https://prim.iledefrance-mobilites.fr/fr/apis/idfm-navitia-line_reports-v2)
- [Next passages product](https://prim.iledefrance-mobilites.fr/apis/idfm-ivtr-requete_unitaire)
- [PRIM playground instructions](https://prim.iledefrance-mobilites.fr/aide-et-contact/documentation/prise-en-main-des-api/prise-en-main-des-api-calculateur-ile-de-france-mobilites/utilisation-du-playground)
- [Official playground examples](https://prim.iledefrance-mobilites.fr/playground/index.html)
- [Navitia schema and parameter documentation](https://doc.navitia.io/)
- [SIRI object identifiers](https://prim.iledefrance-mobilites.fr/aide-et-contact/documentation/prise-en-main-des-api/prise-en-main-des-api-prochains-passages/identification-des-objets)
- [SIRI request parameters](https://prim.iledefrance-mobilites.fr/fr/aide-et-contact/documentation/prise-en-main-des-api/prise-en-main-des-api-prochains-passages/structure-des-requetes-parametres-dappel)

The playground explicitly requires `line_reports` twice in traffic URLs. Its
examples confirm the generic v2 paths without an added coverage segment.
Older third-party claims of Navitia deprecation are contradicted by authenticated
successful requests. The reference project [HeshamWM/idfm-mcp-server](https://github.com/HeshamWM/idfm-mcp-server)
was reviewed for context; no implementation code was copied from it.

## Quotas, caching, retries

Default catalogue quotas are 5 requests/second and 1,000/day per API; raised
limits depend on product and account (generic: 10/second, 20,000/day; SIRI:
100/second, 1,000,000/day). Do not assume raised quotas. Check your PRIM account.

The client paces at 4 requests/second per process, with a 20-second timeout and
at most two retries for transport failures, HTTP 429 and 5xx. `Retry-After` accepts
seconds or an HTTP date. Waits longer than 30 seconds are returned to the host
as a cooldown instead of being shortened. 401, 403 and 404 are not retried.
There is no cross-process or daily quota ledger; all consumers of a token share
the upstream quota. A large network query consumes multiple calls.

Cache TTLs: places/line metadata/stops 1 hour; traffic 60 seconds; journeys
20 seconds; SIRI passages 15 seconds; theoretical departures 30 seconds. Cache
is memory-only, bounded to 256 entries, discarded with the MCP process. No location
history or persistent transport database is created.

## Pagination and uncertainty

Traffic pages use `count=100` (the documented traffic maximum), `start_page` and
the response's `total_result`. Normal generic collection limits are higher;
using 100 remains valid. Up to 20 pages are fetched by default. Missing pagination,
an early empty page or the safety bound returns `complete=false`, never all clear.
Disruptions from every fetched page are merged by ID. Output defaults to 20 alerts;
`next_offset` and `total_disruptions` make the remaining normalized results accessible.
`complete` describes API traversal, independently of this output window.
Returned alerts are concise
normalizations; original application periods, severity and impacted objects remain.

SIRI responses are nested under `Siri.ServiceDelivery.StopMonitoringDelivery`.
Delivery errors remain unavailable. Expected and aimed times are kept separate.
The official catalogue explicitly states partial realtime coverage. Since March
2025, SNCF requires stop-area references for its next passages. Navitia timetable
data do not establish realtime coverage.

Public transport modes, routing dates and accessibility depend on the production
data. An HTTP 200 alone does not prove useful data exists: tests check collections,
IDs, normalized schedules and tool output. Alert timestamps without offsets are
interpreted in Europe/Paris; SIRI ISO timestamps preserve their actual UTC instant.
