"""Read-only MCP adapter for the local football research HTTP API.

Run from the repository: python -m research.football_data_mcp
Requires the optional `mcp` Python package; never changes client configuration.
"""
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import jsonschema
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server


BASE_URL = "http://127.0.0.1:8766"
TIMEOUT_SECONDS = 45
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
PLAYER_VIEWS = "season last3 last5 career blitz pressure clean man zone home away first_half second_half third_down opponent_adjusted percentile yoy confidence".split()
TEAM_VIEWS = "season last3 last5 last8 home away first_half second_half leading trailing tied top10 bottom10 man zone blitz four_man short_rest long_rest opponent_adjusted percentile three_year yoy confidence".split()
ROLES = "QB RB WR TE EDGE DI LB CB S OT OG C K P LS KR PR FB".split()
TEAMS = "ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LAC LAR LV MIA MIN NE NO NYG NYJ PHI PIT SEA SF TB TEN WSH".split()


def text_schema(max_length=100):
    return {"type": "string", "minLength": 1, "maxLength": max_length}


SEASON = {"type": "integer", "minimum": 2006, "maximum": 2100}
TEAM = {"type": "string", "enum": TEAMS}
AS_OF = {"type": "string", "format": "date-time", "maxLength": 40,
         "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"}
PLAYER_SCOPE = {"player": text_schema(), "season": SEASON,
                "view": {"type": "string", "enum": PLAYER_VIEWS},
                "role": {"type": "string", "enum": ROLES}, "as_of": AS_OF}
TEAM_SCOPE = {"team": TEAM, "season": SEASON,
              "view": {"type": "string", "enum": TEAM_VIEWS}, "as_of": AS_OF}

# The API's default season is retained if no season is supplied. Tools never
# silently select a newer season or change the upstream matchup calculation.
SPECS = {
    "football_overview": ("/api/overview", {}, [], "Player metric catalog, definitions and snapshot summary."),
    "football_players": ("/api/players", {
        "q": {"type": "string", "maxLength": 100},
        "position": {"type": "string", "enum": ROLES}, "team": TEAM,
        "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, [], "Search current roster identities; roster membership does not confirm game-day availability."),
    "football_player_profile": ("/api/profile", PLAYER_SCOPE, ["player"], "Player metrics, definitions, source hashes, coverage and null reasons."),
    "football_player_metric": ("/api/metric", {**PLAYER_SCOPE, "metric": text_schema()}, ["player", "metric"], "One player metric across upstream analytical views; metric IDs come from the catalog."),
    "football_player_coverage": ("/api/profile", PLAYER_SCOPE, ["player"], "Full player profile plus counts of numeric values, zeros, nulls and explicit inapplicable statuses."),
    "football_teams": ("/api/teams", {}, [], "Team catalog, metric definitions and snapshot source-coverage summary."),
    "football_team_profile": ("/api/team/profile", TEAM_SCOPE, ["team"], "Team metrics, source records, definitions, coverage and null reasons."),
    "football_team_metric": ("/api/team/metric", {**TEAM_SCOPE, "metric": text_schema()}, ["team", "metric"], "One team metric across upstream views and its confidence evidence."),
    "football_team_coverage": ("/api/team/profile", TEAM_SCOPE, ["team"], "Full team profile plus counts of numeric values, zeros, nulls and explicit inapplicable statuses."),
    "football_matchup": ("/api/team/matchup", {**TEAM_SCOPE, "opponent": TEAM}, ["team", "opponent"], "Existing team matchup research semantics, including source limitations; no bet or execution action."),
    "football_team_evidence": ("/api/team/evidence", {**TEAM_SCOPE, "metric": text_schema()}, ["team", "metric"], "Up to 80 upstream play evidence rows and eligible-row count. Not historical pregame certification."),
}


class ConnectorError(Exception):
    """Bounded, non-sensitive upstream failure."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ConnectorError("Upstream redirect refused; only the fixed loopback API is allowed.")


def schema_for(name):
    _, properties, required, _ = SPECS[name]
    return {"type": "object", "properties": properties,
            "required": required, "additionalProperties": False}


def tool_definitions():
    return [types.Tool(name=name, description=spec[3], inputSchema=schema_for(name),
                       annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                                         idempotentHint=True, openWorldHint=False))
            for name, spec in SPECS.items()]


def validate_arguments(name, arguments):
    if name not in SPECS:
        raise ValueError("Unknown football research tool.")
    jsonschema.Draft202012Validator(schema_for(name), format_checker=jsonschema.FormatChecker()).validate(arguments)
    if "as_of" in arguments:
        # jsonschema date-time checks depend on optional format packages. Keep
        # calendar validity and timezone validation effective without them.
        parsed = datetime.fromisoformat(arguments["as_of"].replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("as_of requires a timezone")


def coverage_counts(data):
    """Count the returned metric grain, never treat a null as zero."""
    metrics = data.get("metrics", [])
    populated = sum(isinstance(m.get("value"), (int, float)) and not isinstance(m.get("value"), bool) for m in metrics)
    return {"grain": "one metric slot in the returned entity/season/view/role profile",
            "defined": len(metrics), "numeric_populated": populated,
            "numeric_populated_rate": populated / len(metrics) if metrics else None,
            "true_zero": sum(m.get("value") == 0 and not isinstance(m.get("value"), bool) for m in metrics),
            "null": sum(m.get("value") is None for m in metrics),
            "explicit_inapplicable": sum(m.get("status") in ("INAPPLICABLE", "NOT_APPLICABLE") for m in metrics),
            "status_counts": dict(Counter(str(m.get("status", "UNSPECIFIED")) for m in metrics)),
            "null_reason_counts": dict(Counter(m.get("reason") or "No explicit upstream reason" for m in metrics if m.get("value") is None)),
            "scope_note": "Nulls include explicit inapplicable slots; inapplicability is counted only when the upstream status says so. No imputation or source expansion."}


def reject_nonfinite(value):
    raise ValueError("Non-finite JSON number")


def read_tool(name, arguments):
    validate_arguments(name, arguments)
    route = SPECS[name][0]
    query = urlencode(arguments)
    url = BASE_URL + route + ("?" + query if query else "")
    # Ignore machine proxy settings: no credentials or remote destinations.
    opener = build_opener(ProxyHandler({}), NoRedirects())
    try:
        with opener.open(Request(url, method="GET", headers={"Accept": "application/json"}), timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise ConnectorError("Local football API returned an unsuccessful status.")
            if response.headers.get_content_type() != "application/json":
                raise ConnectorError("Local football API did not return JSON.")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ConnectorError("Local football API response exceeded the size limit.")
        data = json.loads(raw, parse_constant=reject_nonfinite)
        if not isinstance(data, (dict, list)):
            raise ConnectorError("Local football API returned an unexpected JSON shape.")
    except HTTPError as exc:
        raise ConnectorError(f"Local football API returned HTTP {exc.code}; no missing values were synthesized.") from None
    except (URLError, OSError, ValueError) as exc:
        raise ConnectorError("Local football API unavailable or invalid JSON; no missing values were synthesized.") from None
    result = {"data": data, "connector_provenance": {
        "transport": "read-only HTTP GET", "api": BASE_URL, "route": route, "query": arguments,
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "response_sha256": hashlib.sha256(raw).hexdigest(),
        "response_bytes": len(raw), "source_namespace": "existing_local_football_research_api",
        "grain": "unchanged upstream response; source and metric grain remain in data",
        "source_note": "The connector is not a new NFL source. Source IDs are scoped to the upstream store. A response hash is not a raw-provider source hash or pregame availability certificate."}}
    if name.endswith("_coverage"):
        result["coverage_counts"] = coverage_counts(data)
    return result


def create_server():
    server = Server("case-capital-football-data", version="0.1.0",
                    instructions="Read-only NFL research from the fixed local API. Preserve source limitations, null reasons and unconfirmed availability. No betting or execution authority.")

    @server.list_tools()
    async def list_tools():
        return tool_definitions()

    @server.call_tool()
    async def call_tool(name, arguments):
        try:
            # Validate again here so direct handler calls and date-time formats
            # receive the same restrictions as SDK-dispatched requests.
            return await asyncio.to_thread(read_tool, name, arguments)
        except (ValueError, jsonschema.ValidationError):
            return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text="Unknown tool or invalid arguments; use tools/list for the allowed schema.")])
        except ConnectorError as exc:
            return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text=str(exc))])

    return server


async def main():
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
