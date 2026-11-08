# Local football data MCP

`research/football_data_mcp.py` exposes the existing football research API through
the installed Python MCP SDK. It creates no new football observations, metric
calculations, grades, probabilities, availability claims or betting authority.

## Run

Start the existing research terminal separately, under the parent's snapshot and
server integration workflow. The connector only reads `http://127.0.0.1:8766`.
It does not start, restart or configure the terminal and never opens a database.

From the repository root, using Python 3.11 or later with `mcp` installed:

```powershell
python -m research.football_data_mcp
```

The development environment was verified with MCP SDK 1.28.1. The SDK handles
newline-delimited JSON-RPC stdio, initialization/version negotiation, initialized
notification, ping, discovery and tool invocation. Stdout is reserved for MCP
messages. Close stdin to stop. There is no HTTP listener in the connector.
An API outage produces a tool error, never a fabricated empty profile.

## Tools and inputs

All schemas reject extra arguments. Catalog calls take no arguments. Player IDs
come from `football_players`; metric IDs come from the relevant catalog/profile.

| Tool | Upstream GET | Required arguments |
| --- | --- | --- |
| `football_overview` | `/api/overview` | none |
| `football_players` | `/api/players` | none; optional q, position, team, limit (1–200) |
| `football_player_profile` | `/api/profile` | player |
| `football_player_metric` | `/api/metric` | player, metric |
| `football_player_coverage` | `/api/profile` | player |
| `football_teams` | `/api/teams` | none |
| `football_team_profile` | `/api/team/profile` | team |
| `football_team_metric` | `/api/team/metric` | team, metric |
| `football_team_coverage` | `/api/team/profile` | team |
| `football_matchup` | `/api/team/matchup` | team, opponent |
| `football_team_evidence` | `/api/team/evidence` | team, metric |

Profile, metric, coverage, matchup and evidence reads accept optional `season`
(integer 2006–2100), `view` (enumerated in discovery), and timezone-bearing RFC3339
`as_of`. Player reads additionally accept an enumerated `role`. Omitted season and
view retain the API's defaults (currently 2026 and season); pass explicit values
for reproducible analysis. `as_of` filters existing source vintages according to
the API's rules; it cannot establish historical availability of a later fetch.
Matchup semantics and calculations are passed through unchanged.

## Output and provenance

Successful calls include identical `data` in JSON text content and SDK
`structuredContent`. The original API object or array is nested under `data`,
without editing metric definitions/formulas, source records, raw-provider hashes,
source timestamps, statuses, coverage, confidence or null reasons. Source IDs
remain scoped to their upstream store; do not join player/team IDs as if they
were globally unique source identifiers.

`connector_provenance` records the fixed API address, allowlisted route, query,
retrieval time, raw HTTP-response byte count and SHA256. This hash identifies the
JSON response bytes; provider artifact hashes remain in `data.sources`. The
connector does not fetch provider URLs or archive response bytes during normal
operation. The verification artifacts archive those bytes separately.

Coverage tools keep the full profile and add counts at the entity/season/view/role
metric-slot grain. Numeric zero counts as populated, null remains null, and only
explicit `INAPPLICABLE`/`NOT_APPLICABLE` statuses count as inapplicable. The null
count includes those slots; counts are not mutually exclusive. No status is
inferred from prose or a missing field. Empty catalogs have a null populated
rate. This is returned-profile coverage, not a census of all players or teams.

The connector only issues GETs, bypasses environment proxies, rejects redirects,
limits HTTP reads to 16 MiB and applies a 45-second socket timeout. It accepts no
URL, host, port, filesystem, shell, authentication, purchase or execution inputs.
Its read-only tool annotations describe connector operations; they do not certify
upstream source quality. Normal local API reads may use the API's existing caches.

## Sample client configuration (not installed)

Use a Python executable that has the MCP SDK installed. This example is a
standalone stdio-client configuration, not a change to any global configuration:

```json
{
  "mcpServers": {
    "football-data-local": {
      "command": "python",
      "args": [
        "-c",
        "import sys; sys.path.insert(0, r'C:\\Users\\tcase\\OneDrive\\Documents\\Case Cap\\CaseCapitalFootballBetting'); from research.football_data_mcp import main; import asyncio; asyncio.run(main())"
      ]
    }
  }
}
```

Example tool call after the client's normal SDK initialization:

```json
{"name":"football_matchup","arguments":{"team":"CHI","opponent":"SEA","season":2026,"view":"season"}}
```

## Verification

```powershell
python -m pytest tests/test_football_data_mcp.py -q
$env:FOOTBALL_MCP_LIVE_SMOKE = '1'
python -m pytest tests/test_football_data_mcp.py -q
```

The opt-in test performs actual stdio initialization, discovery, ping and a player
read against the running fixed loopback API. Other tests cover schema rejection,
invalid protocol envelopes, URL encoding, GET-only transport, redirects, proxies,
response limits, invalid upstream data, preservation of source/definition/null
fields and zero-versus-null coverage semantics.

Extended verification artifacts live under the task root's
`outputs/metric-depth-expansion/mcp`: `api-proposal.md`, `live_smoke.py`, raw API
JSON responses and their manifest, the SDK transcript, base snapshot hash checks,
and `live-coverage.json`. This verifies source connectivity and unchanged metric
coverage. It does not recover new statistics or validate every upstream formula.

Protocol references: [MCP lifecycle](https://modelcontextprotocol.io/specification/2025-03-26/basic/lifecycle)
and [MCP tools](https://modelcontextprotocol.io/specification/2025-03-26/server/tools).
