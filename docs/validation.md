# Validation

Initial validation: 2026-11-01, Linux ARM64 on Raspberry Pi, Python 3.11 and the
official MCP SDK 2.3.0. Repeat live checks when deploying; schedules change.

The offline suite passed 52 tests; two opt-in integration tests passed against
authenticated PRIM. A Python 3.13 runtime was installed and exercised inside an
existing container without system Python or timezone files; the package includes
`tzdata` so Europe/Paris works there too. The Docker image builds on ARM64.

Offline suite checks authentication errors, forbidden access, missing resources,
quotas, Retry-After (seconds/date), bounded network retries, concurrent pacing,
cache isolation and retrieval times, empty results, ambiguous places/lines,
multi-page disruptions, DST gaps/folds, SIRI estimates versus aimed schedules,
source-code mappings, explicit theoretical fallback, input validation and a real
MCP client discovering seven tools over a subprocess stdio transport.

Authenticated PRIM integration tests cover searches for République, Télégraphe
and Gare de l'Est; a Télégraphe → Gare de l'Est itinerary; métro 5/11, RER A and
tram T3b line resolution; station lines/quays; SIRI next passages; journey alert
assessment; and a compatible MCP client calling the real API. Searches are
compared with raw PRIM identifiers. The suite accepts empty realtime passages
when service is absent, separately from the manual positive-passage check.

Positive realtime passages at République were also observed through its verified
stop-area reference. Individual quay examples returned empty lists during the
night; that was correctly reported without claiming coverage or no service.
The initial midday route check returned métro 11 then métro 5 in approximately
14 minutes. These observations are examples, not fixed expected future schedules.

Failures simulated in unit tests are not presented as intentionally induced live
quota/authentication failures. Normal CI never uses a real token. Private keys,
assistant settings and captured API responses are not test fixtures in this repo.
