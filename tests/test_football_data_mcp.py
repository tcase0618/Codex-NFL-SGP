"""Connector boundary tests, SDK stdio protocol tests and opt-in live smoke."""
import asyncio
import hashlib
from io import BytesIO
import json
from pathlib import Path
import os
import subprocess
import sys
from email.message import Message
from unittest.mock import patch

import jsonschema
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from research import football_data_mcp as mcp

ROOT = Path(__file__).resolve().parents[1]


class Response(BytesIO):
    status = 200

    def __init__(self, raw, content_type="application/json"):
        super().__init__(raw)
        self.headers = Message()
        self.headers["Content-Type"] = content_type


class Opener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.response


@pytest.mark.parametrize("name,args", [
    ("execute_bet", {}),
    ("football_overview", {"url": "http://example.org"}),
    ("football_overview", {"path": "C:/secret"}),
    ("football_overview", {"port": 8000}),
    ("football_players", {"limit": True}),
    ("football_players", {"limit": 201}),
    ("football_players", {"q": "a" * 101}),
    ("football_player_profile", {}),
    ("football_player_profile", {"player": "x", "season": "2026"}),
    ("football_player_profile", {"player": "x", "season": 2005}),
    ("football_player_profile", {"player": "x", "view": "invented"}),
    ("football_player_profile", {"player": "x", "as_of": "2026-01-01"}),
    ("football_player_profile", {"player": "x", "as_of": "2026-01-01T00:00:00"}),
    ("football_team_profile", {"team": "http://evil"}),
    ("football_matchup", {"team": "CHI"}),
    ("football_team_evidence", {"team": "CHI", "metric": ""}),
])
def test_rejects_invalid_arguments_before_network(name, args):
    with patch.object(mcp, "build_opener") as opener:
        with pytest.raises((ValueError, jsonschema.ValidationError)):
            mcp.read_tool(name, args)
        opener.assert_not_called()


def test_fixed_get_preserves_source_null_zero_and_definition():
    data = {"metrics": [{"value": 0, "formula": "sum", "source_ids": ["s1"]},
                        {"value": None, "status": "UNAVAILABLE", "reason": "No source"},
                        {"value": None, "status": "INAPPLICABLE", "reason": "Role"}],
            "sources": [{"id": "s1", "sha256": "provider-hash", "reference": "raw source"}]}
    raw = json.dumps(data).encode()
    opener = Opener(Response(raw))
    with patch.object(mcp, "build_opener", return_value=opener):
        result = mcp.read_tool("football_player_coverage", {"player": "a&url=http://example.org", "season": 2026})
    assert result["data"] == data
    assert result["connector_provenance"]["response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["connector_provenance"]["response_bytes"] == len(raw)
    counts = result["coverage_counts"]
    assert (counts["true_zero"], counts["null"], counts["explicit_inapplicable"], counts["numeric_populated"]) == (1, 2, 1, 1)
    request, timeout = opener.requests[0]
    assert request.get_method() == "GET"
    assert request.full_url == mcp.BASE_URL + "/api/profile?player=a%26url%3Dhttp%3A%2F%2Fexample.org&season=2026"
    assert timeout == 45


def test_redirects_cannot_leave_loopback():
    with pytest.raises(mcp.ConnectorError, match="redirect refused"):
        mcp.NoRedirects().redirect_request(None, None, 302, "Found", {}, "https://example.org")


def test_proxy_environment_is_not_used():
    with patch.object(mcp, "build_opener", return_value=Opener(Response(b"{}"))) as build:
        mcp.read_tool("football_overview", {})
    assert build.call_args.args[0].proxies == {}
    assert isinstance(build.call_args.args[1], mcp.NoRedirects)


@pytest.mark.parametrize("raw,content_type", [
    (b"not json", "application/json"),
    (b"{\"value\":NaN}", "application/json"),
    (b"null", "application/json"),
    (b"{}", "text/html"),
])
def test_invalid_upstream_response_is_failure(raw, content_type):
    with patch.object(mcp, "build_opener", return_value=Opener(Response(raw, content_type))):
        with pytest.raises(mcp.ConnectorError):
            mcp.read_tool("football_overview", {})


def test_response_size_is_bounded():
    with patch.object(mcp, "MAX_RESPONSE_BYTES", 2), patch.object(mcp, "build_opener", return_value=Opener(Response(b"{} "))):
        with pytest.raises(mcp.ConnectorError, match="size limit"):
            mcp.read_tool("football_overview", {})


def test_empty_coverage_is_not_zero_percent():
    assert mcp.coverage_counts({"metrics": []})["numeric_populated_rate"] is None


def test_all_tools_have_closed_readonly_schemas():
    definitions = mcp.tool_definitions()
    assert len(definitions) == 11
    for tool in definitions:
        assert tool.inputSchema["additionalProperties"] is False
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert not any(key in tool.inputSchema["properties"] for key in ("url", "path", "command", "token", "port"))


async def protocol_smoke(live=False):
    params = StdioServerParameters(command=sys.executable,
                                  args=["-m", "research.football_data_mcp"], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=__import__("datetime").timedelta(seconds=60)) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "case-capital-football-data"
            await session.send_ping()
            listed = await session.list_tools()
            assert len(listed.tools) == 11
            invalid = await session.call_tool("football_overview", {"url": "https://example.org"})
            assert invalid.isError
            unknown = await session.call_tool("bet", {})
            assert unknown.isError
            if live:
                result = await session.call_tool("football_players", {"q": "Caleb Williams", "limit": 1})
                assert not result.isError
                assert result.structuredContent["data"][0]["name"] == "Caleb Williams"
                assert result.structuredContent["connector_provenance"]["api"] == mcp.BASE_URL


def test_actual_stdio_initialize_ping_list_and_invalid_calls():
    asyncio.run(protocol_smoke())


def test_sdk_protocol_rejects_invalid_envelope():
    request = {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": 123, "arguments": []}}
    completed = subprocess.run([sys.executable, "-m", "research.football_data_mcp"],
                               cwd=ROOT, input=json.dumps(request) + "\n", capture_output=True,
                               text=True, timeout=30)
    responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert any("error" in response for response in responses)
    assert completed.returncode == 0


@pytest.mark.skipif(os.environ.get("FOOTBALL_MCP_LIVE_SMOKE") != "1", reason="opt-in read of the running fixed loopback API")
def test_live_api_stdio_smoke():
    asyncio.run(protocol_smoke(live=True))
