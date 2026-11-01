# IDFM Transit MCP

[![CI](https://github.com/MaximeGrn/idfm-transit-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/MaximeGrn/idfm-transit-mcp/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![MIT](https://img.shields.io/badge/code-MIT-green)](LICENSE)
[![MCP stdio](https://img.shields.io/badge/MCP-stdio-purple)](https://modelcontextprotocol.io/)

An MCP server for Île-de-France public transport: journey planning, real-time
departures, service disruptions and station search using official IDFM PRIM APIs.
Supports modes present in PRIM, including métro, RER, Transilien, tram and bus.
No stations, routes or personal preferences are hardcoded in the server.

**Unofficial project — not affiliated with Île-de-France Mobilités.**

## Install

Requires Python 3.11+ and a [PRIM token](#get-a-prim-token). Linux ARM64, including
Raspberry Pi, is supported. Docker Desktop is not required.

Install a tagged version into a dedicated environment; no development checkout is needed:

```sh
python3 -m venv ~/.local/share/idfm-transit-mcp/venv
~/.local/share/idfm-transit-mcp/venv/bin/pip install \
  'idfm-transit-mcp @ git+https://github.com/MaximeGrn/idfm-transit-mcp.git@v0.1.1'
```

Alternatively, use the locked source environment:

```sh
git clone https://github.com/MaximeGrn/idfm-transit-mcp.git
cd idfm-transit-mcp
uv sync --locked --no-dev
IDFM_API_KEY_FILE=/absolute/path/to/private/prim-api-key uv run --no-sync idfm-transit-mcp
```

This command speaks **stdio MCP**; a process waiting quietly for input is normal.
It opens no listening port, creates no database and sends no personal telemetry.

## Get a PRIM token

1. Sign in or create an account on [PRIM](https://prim.iledefrance-mobilites.fr/).
2. Open your personal menu → **Mes jetons d'authentification** → **API**.
3. Keep your existing token if available. Generating a replacement invalidates it;
   the full new token is shown only once.
4. Store it in a private file outside the repository, with permissions 0600.

This prompt hides input and creates the private file without putting the token in shell history:

```sh
python3 - <<'PY'
import getpass, os
from pathlib import Path
directory = Path.home() / '.config' / 'idfm-transit-mcp'
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
token = getpass.getpass('PRIM API token: ').strip()
fd = os.open(directory / 'api-key', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.fchmod(fd, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write(token + '\n')
PY
```

Set `IDFM_API_KEY_FILE` to that absolute path. `IDFM_API_KEY` is also supported for
hosts with secure environment injection. The file takes precedence. `.env` files
are not loaded implicitly. Never put a token in URLs, command arguments, issues
or committed configuration. See the [official instructions](https://prim.iledefrance-mobilites.fr/fr/aide-et-contact/documentation/donnees-disponibles-2/explorer-les-api/generer-un-jeton-pour-acceder-aux-api).

## Connect an MCP host

For Claude Desktop, merge this into `claude_desktop_config.json`. Other MCP hosts
accepting `command`, `args` and `env` can use the same entry. Replace both absolute
paths; not every host expands `~`.

```json
{
  "mcpServers": {
    "idfm-transit": {
      "command": "/absolute/path/to/idfm-transit-mcp/venv/bin/idfm-transit-mcp",
      "args": [],
      "env": {"IDFM_API_KEY_FILE": "/absolute/path/to/private/prim-api-key"}
    }
  }
}
```

A TOML equivalent for compatible MCP clients:

```toml
[mcp_servers.idfm_transit]
command = "/absolute/path/to/idfm-transit-mcp/venv/bin/idfm-transit-mcp"

[mcp_servers.idfm_transit.env]
IDFM_API_KEY_FILE = "/absolute/path/to/private/prim-api-key"
```

Reload the host's MCP connections and discover the tools. Missing credentials
do not prevent discovery; API calls return `missing_api_key` until configured.

## Tools

| Tool | Purpose |
| --- | --- |
| `search_places` | Stations, addresses and POIs with IDs, types, labels and coordinates. |
| `plan_journey` | Itineraries, departure/arrival deadlines, allowed/excluded modes or lines, wheelchair preference and transfer limits. |
| `get_line_status` | Resolve names such as `Métro 11`, `RER A`, `T3b` and retrieve alerts. |
| `get_network_disruptions` | Paginated alerts filtered by line, physical mode and period. |
| `get_next_departures` | SIRI estimates or clearly labelled theoretical timetables. |
| `get_stop_info` | Coordinates, serving lines, quays, codes and available SIRI references. |
| `check_journey_status` | Flag potentially affected public transport sections of a planned journey. |

Results contain `status`, `data`, `warnings`, optional `error`, source attribution,
`retrieved_at` and `timezone`. Times include offsets; durations use seconds.
Cached results preserve the oldest underlying fetch timestamp.

Examples to ask an assistant:

- “Find République and list the next departures on métro 5.”
- “How do I get from Télégraphe to Gare de l'Est tomorrow morning?”
- “Compare routes to this address arriving at 20:00, avoiding buses.”
- “Which segments of this itinerary could be affected by planned works?”

Ambiguous names return candidates. Select an exact place ID before planning.
Journey endpoints also accept `longitude;latitude`. Dates without offsets mean
Europe/Paris; ambiguous or nonexistent DST clock times require an explicit offset.
See [tool parameters](docs/tools.md) and [API evidence](docs/api.md).

## Real-time limitations

SIRI coverage is incomplete. With `timetable="auto"` (default), Navitia stop IDs
are resolved through explicit source codes returned by PRIM. If SIRI has no
matching passages, a theoretical timetable is returned **with a warning**.
`timetable="realtime"` never silently falls back. An empty SIRI response does not
prove the absence of service. Direct `STIF:StopArea:SP:...:` and
`STIF:StopPoint:Q:...:` references are accepted. SNCF generally requires a stop-area
reference; a quay may not be covered.

Assessments distinguish `disruption_detected`, `no_known_disruption` and
`information_unavailable`. Line alerts may not affect your specific segment.
No known disruption is **never a guarantee**. Traffic traversal is bounded at 20
pages of 100 reports; `complete=false` marks incomplete traversal explicitly.

## Docker

```sh
docker build -t idfm-transit-mcp:0.1.1 .
docker run --rm -i --read-only --cap-drop ALL --user "$(id -u):$(id -g)" \
  --mount type=bind,src=/absolute/path/to/private/prim-api-key,dst=/run/secrets/prim_key,readonly \
  -e IDFM_API_KEY_FILE=/run/secrets/prim_key idfm-transit-mcp:0.1.1
```

The image defaults to non-root UID 10001. The command above uses your host UID/GID
to read a private 0600 secret without making it publicly readable. Account for UID
mapping with rootless Docker. With Compose:

```sh
IDFM_UID="$(id -u)" IDFM_GID="$(id -g)" \
  IDFM_SECRET_FILE=/absolute/path/to/private/prim-api-key \
  docker compose run --rm -T idfm
```

No daemon or published port is needed.

## Develop and validate

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -m 'not live'
```

Normal tests use synthetic API responses and a real MCP stdio client, with no key.
Live checks are explicit and consume quota:

```sh
IDFM_RUN_LIVE=1 IDFM_API_KEY_FILE=/absolute/path/to/private/prim-api-key \
  uv run pytest tests/test_live.py -q
```

See [validation](docs/validation.md), [contributing](CONTRIBUTING.md) and
[security](SECURITY.md). Runtime installations and personal assistant profiles
belong outside the development checkout; the wheel requires no tests or Git history.

## Code and data licences

Original server code is [MIT](LICENSE). Transport data are **not** MIT: PRIM
product licences, attribution and platform terms apply. Navitia products specify
the **Licence Mobilités**. The SIRI catalogue has inconsistent metadata: its
header states Licence Mobilité, while its terms section links ODbL. See
[data licensing](docs/data-licensing.md) before redistributing data publicly.
Fixtures are synthetic; this repository bundles no PRIM database or IDFM logos.
