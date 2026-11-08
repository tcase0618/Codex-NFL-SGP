"""Bounded evidence recovery; never emits validator-ready or wager evidence.

Reads existing archives without changing them. New HTTP bodies (including error
responses) and discovery times are saved in a new output directory. Source-stated
publication times are claims, not independently verified historical versions.
"""
import argparse
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
# One attempt per URL, no retries, API keys, gateway/betslip links or purchases.
PROBES = (
    ("bookmaker_past_event", "https://sportsbook.draftkings.com/event/34118042"),
    ("bookmaker_current_listing", "https://sportsbook.fanduel.com/navigation/nfl"),
    ("historical_provider_documentation", "https://the-odds-api.com/historical-odds-data/"),
    ("club_game_specific_article", "https://www.seahawks.com/news/nick-emmanwori-tory-horton-inactive-for-seahawks-opener-vs-patriots"),
    ("league_game_specific_inactives", "https://www.nfl.com/news/nfl-kickoff-game-inactives-new-england-patriots-at-seattle-seahawks"),
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def file_evidence(path, expected=None):
    path = Path(path).resolve()
    try:
        actual = sha(path.read_bytes())
        return {"path": str(path), "sha256": actual,
                "expected_sha256": expected,
                "integrity": "MATCH" if expected == actual else
                "MISMATCH" if expected is not None else "HASHED_NOW_NO_PRIOR_CONTENT_HASH"}
    except OSError as error:
        return {"path": str(path), "integrity": "UNREADABLE", "error": str(error)}


def article_claims(raw):
    soup = BeautifulSoup(raw, "html.parser")
    articles = []

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list) and any(t in types for t in ("NewsArticle", "Article")):
                articles.append(value)
            visit(value.get("@graph", []))

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            visit(json.loads(script.get_text()))
        except (ValueError, TypeError):
            continue
    article = articles[0] if articles else {}
    parts = soup.select(".story-part-rich-text-editor-wrapper, .nfl-c-body-part--text")
    paragraphs = [p.get_text(" ", strip=True) for part in parts for p in part.find_all("p")]
    # These are textual leads, never parsed active=True flags or stable identities.
    mentions = [p for p in paragraphs if "active" in p.lower() and "inactive" not in p.lower()]
    return {"title": article.get("headline"),
            "source_claimed_published_at": article.get("datePublished"),
            "source_claimed_modified_at": article.get("dateModified"),
            "explicit_active_text_leads": mentions,
            "historical_version_verified": False,
            "complete_active_universe_verified": False}


def fetch_probe(probe, output, get=requests.get):
    kind, url = probe
    record = {"kind": kind, "requested_url": url, "request_started_at": now(),
              "historical_version_verified": False, "recovered_historical_player_quotes": False}
    try:
        response = get(url, timeout=(6, 18), headers={"User-Agent": "CaseCapitalResearch/0.1"})
        raw = response.content
        body = output / (sha(url.encode()) + ".body")
        with body.open("xb") as stream:
            stream.write(raw)
        record.update({"discovered_at": now(), "resolved_url": response.url,
                       "http_status": response.status_code, "path": str(body.resolve()),
                       "sha256": sha(raw), "bytes": len(raw),
                       "response_headers": dict(response.headers),
                       "redirects": [{"url": r.url, "http_status": r.status_code,
                                      "location": r.headers.get("Location")} for r in response.history],
                       "response_status": "HTTP_SUCCESS_CONTENT_REQUIRES_REVIEW" if
                       200 <= response.status_code < 300 else "HTTP_ERROR_BODY_ARCHIVED"})
        if kind in {"club_game_specific_article", "league_game_specific_inactives"} and response.status_code == 200:
            record["article_claims"] = article_claims(raw)
    except requests.RequestException as error:
        record.update({"discovered_at": now(), "response_status": "TRANSPORT_ERROR_NO_BODY",
                       "error": str(error)})
    return record


def team_markets(data, game_id):
    if str(data.get("header", {}).get("id")) != game_id:
        raise ValueError("cached summary game ID mismatch")
    markets = []
    for row in data.get("pickcenter", []):
        if not isinstance(row, dict):
            continue
        values = {key: row[key] for key in ("details", "spread", "overUnder", "overOdds", "underOdds") if key in row}
        for side in ("homeTeamOdds", "awayTeamOdds"):
            value = row.get(side)
            if isinstance(value, dict):
                values[side] = {k: value[k] for k in ("teamId", "moneyLine", "spreadOdds") if k in value}
        if any(isinstance(values.get(key), (int, float)) and not isinstance(values[key], bool)
               and math.isfinite(values[key]) for key in ("spread", "overUnder")):
            markets.append({"provider_as_reported": row.get("provider"), "values_as_reported": values,
                            "scope": "TEAM_MARKETS_ONLY_PROVIDER_ATTRIBUTED_NOT_BOOKMAKER_ARCHIVE",
                            "quote_published_at": None, "historical_version_verified": False})
    return markets


def run(workspace, output):
    workspace, output = Path(workspace).resolve(), Path(output).resolve()
    # Reserve a new folder first. Never resume or overwrite an evidence run.
    output.mkdir(parents=True, exist_ok=False)
    http = output / "http"
    http.mkdir()
    selection_path = workspace / "outputs/reconstructed-replay-20261103-v4/selection-manifest.json"
    inventory_path = workspace / "outputs/pregame-source-inventory.json"
    selection = json.loads(selection_path.read_bytes())
    inventory = json.loads(inventory_path.read_bytes())
    games = {}
    for ticket in selection["tickets"]:
        row = {k: ticket[k] for k in ("game_id", "game", "week", "kickoff", "decision_cutoff")}
        if row["game_id"] in games and games[row["game_id"]] != row:
            raise ValueError("inconsistent game metadata")
        games[row["game_id"]] = row
    source_records = {row["reference"]: row for row in inventory["sources"]}
    checked_sources = {}
    coverage_by_id = {str(row["game_id"]): row for row in inventory["game_coverage"]}
    coverage = []
    for game in sorted(games.values(), key=lambda x: (x["week"], x["game_id"])):
        game_id = game["game_id"]
        url = f"{BASE}/summary?event={game_id}"
        cache = workspace / "work/espn-cache" / (sha(url.encode()) + ".json")
        cached = file_evidence(cache)
        try:
            markets = team_markets(json.loads(cache.read_bytes()), game_id)
            cache_error = None
        except (OSError, ValueError, TypeError, AttributeError) as error:
            markets, cache_error = [], str(error)
        inactive = coverage_by_id.get(game_id, {})
        source_urls = inactive.get("sources", [])
        for source_url in source_urls:
            if source_url not in checked_sources:
                source = source_records.get(source_url, {})
                checked = file_evidence(source["html_file"], source.get("content_sha256")) if source.get("html_file") else {"integrity": "MISSING_ARCHIVE"}
                checked_sources[source_url] = {**checked, "reference": source_url,
                    "original_retrieved_at_as_recorded": source.get("retrieved_at"),
                    "source_claimed_published_at": source.get("published_at"),
                    "source_claimed_modified_at": source.get("modified_at"),
                    "historical_version_verified": False}
        coverage.append({**game, "cached_summary": cached, "summary_error": cache_error,
                         "cached_summary_reference": url, "team_market_observations": markets,
                         "inactive_sections_as_recorded": inactive.get("inactive_team_sections", 0),
                         "inactive_source_urls": source_urls,
                         "inactive_source_hashes_match": bool(source_urls) and all(checked_sources[u]["integrity"] == "MATCH" for u in source_urls),
                         "complete_pregame_active_universe_verified": False,
                         "historical_player_prop_quotes_verified": False,
                         "exact_combined_ticket_quotes_verified": False})
    with ThreadPoolExecutor(max_workers=5) as pool:
        probes = list(pool.map(lambda probe: fetch_probe(probe, http), PROBES))
    summary = {"games": len(coverage), "weeks": sorted({g["week"] for g in coverage}),
               "games_with_cached_team_market_observations": sum(bool(g["team_market_observations"]) for g in coverage),
               "games_with_hash_matched_inactive_sources": sum(g["inactive_source_hashes_match"] for g in coverage),
               "verified_player_prop_quote_games": 0, "verified_combined_quote_games": 0,
               "verified_complete_pregame_active_universe_games": 0,
               "network_attempts": len(probes), "price_search_cycles": 1,
               "valid_pregame_backtest": False, "roi_available": False, "readiness": "BLOCKED"}
    manifest = {"kind": "PUBLIC_EVIDENCE_DISCOVERY_NOT_BACKTEST_INPUT", "created_at": now(),
                "summary": summary, "inputs": [file_evidence(selection_path), file_evidence(inventory_path)],
                "prior_download_manifest": file_evidence(workspace / "outputs/reconstructed-replay-20261103-v3/download-source-manifest.json"),
                "scope": "Existing replay's 2026 regular-season Weeks 1-8; not a newly inferred date range",
                "games": coverage, "existing_inactive_sources": list(checked_sources.values()), "http_probes": probes,
                "limits": ["Cache filename hashes identify URLs, not original content or observation time; content hashed now.",
                           "Team market observations are ESPN's DraftKings attribution; no historical bookmaker version is certified.",
                           "No offered player lines, player prices or exact same-game combined quotes recovered by this bounded task.",
                           "HTTP success, Date/Last-Modified headers and article publication dates do not prove an immutable pregame version.",
                           "Inactive exclusion, weekly ACT and final participation do not establish a complete game-specific pregame active list.",
                           "Named active statements are text leads without stable ID reconciliation or complete team coverage.",
                           "Current-vintage model inputs, frozen forecasts, untouched-period validation, calibration and prospective settlement evidence remain required."]}
    save_json(output / "coverage-manifest.json", manifest)
    lines = ["# Bounded public market and eligibility evidence recovery", "", "Readiness: **BLOCKED**. No historical evidence gate changed.", "",
             "## Coverage", "", "```json", json.dumps(summary, indent=2), "```", "",
             "## Bounded HTTP results", ""]
    for probe in probes:
        lines.append(f"- {probe['requested_url']} — {probe.get('http_status', 'no HTTP response')}; {probe['response_status']}; discovered {probe['discovered_at']}; SHA-256 {probe.get('sha256', 'unavailable')}.")
    lines.extend(["", "One fixed pass: two bookmaker pages, one provider documentation page, two official eligibility articles. No retries or paid access. HTTP bodies and response headers are archived; errors remain evidence of failed access, not empty markets.", "",
                  "## Recoverable inputs", "", "Existing per-game team odds can support a separate descriptive team-market study after provider and time verification. They cannot price the fixed 150/20/25-yard player targets. Hash-matched official inactive articles can support exclusion research. Club active mentions can guide manual game/player reconciliation. No complete active universe or historical article version is certified.", "",
                  "## Exact remaining limits", ""])
    lines.extend("- " + limit for limit in manifest["limits"])
    lines.extend(["", "## Files and verification", "", "New code: research/recover_public_market_evidence.py; tests/test_recover_public_market_evidence.py. All other new files are in this output folder. Existing engine, validators, runtime configuration, state and source archives are untouched by this collector. See verification.json for measured test results.", ""])
    with (output / "report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.workspace, args.output), indent=2))
