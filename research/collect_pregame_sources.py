"""Collect official historical source leads without backdating current pages."""

import argparse
import hashlib
import json
import re
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .complete_parlays import load_games

BASE = "https://www.nfl.com"
SEEDS = ["https://www.newyorkjets.com/news/jets-vs-raiders-nfl-game-inactives-11-1-2026",
         BASE + "/news/inactive-reports-sunday-week-1-2026-nfl-season"]
SEEDS += [BASE + f"/news/nfl-week-{week}-inactives-players-ruled-out-sunday-14-games-2026" for week in (2, 3, 4)]
SEEDS += [BASE + f"/news/nfl-week-{week}-inactives-players-ruled-out-sunday-2026-season" for week in (5, 6, 7, 8)]
SEEDS += [BASE + f"/news/nfl-week-{week}-injury-report-2026-season" for week in range(1, 9)]
SEEDS += [BASE + "/news/" + slug for slug in (
    "week-1-monday-night-inactives-denver-broncos-at-kansas-city-chiefs",
    "week-3-thursday-night-inactives-atlanta-falcons-at-green-bay-packers",
    "week-4-thursday-night-inactives-pittsburgh-steelers-at-cleveland-browns",
    "week-4-monday-night-inactives-atlanta-falcons-at-new-orleans-saints",
    "week-6-monday-night-inactives-washington-commanders-at-san-francisco-49ers",
    "week-7-monday-night-inactives-dallas-cowboys-at-philadelphia-eagles",
    "week-8-monday-night-inactives-chicago-bears-at-seattle-seahawks")]


def fetch(url: str, archive: Path) -> dict:
    key = hashlib.sha256(url.encode()).hexdigest()
    record_path = archive / f"{key}.json"
    previous = None
    if record_path.exists():
        previous = json.loads(record_path.read_text(encoding="utf-8"))
        if previous.get("parser_version") == 4:
            return previous
    last_error = None
    for _ in range(2):
        try:
            html_path = archive / f"{key}.html"
            if previous and html_path.exists():
                raw = html_path.read_bytes()
                response = SimpleNamespace(url=previous["resolved_url"], status_code=200)
            else:
                response = requests.get(url, timeout=(6, 15), headers={"User-Agent": "CaseCapitalResearch/0.1"})
                response.raise_for_status()
                raw = response.content
            html_path.write_bytes(raw)
            soup = BeautifulSoup(raw, "html.parser")
            schemas = []
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.get_text())
                    schemas.extend(data if isinstance(data, list) else [data])
                except (ValueError, TypeError):
                    pass
            article = next((item for item in schemas if isinstance(item, dict) and item.get("@type") in ("NewsArticle", "Article")), {})
            heading = soup.find("h1")
            parts = soup.select(".story-part-rich-text-editor-wrapper")
            text = "\n\n".join(part.get_text("\n", strip=True) for part in parts)
            if not text:
                text = article.get("articleBody", "")
            transaction_rows = []
            if "/team/transactions/" in url or str(article.get("headline", "")).lower().startswith("transactions:"):
                text = "\n\n".join(table.get_text("\n", strip=True) for table in soup.find_all("table"))
                transaction_rows = [[cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
                                    for table in soup.find_all("table") for row in table.find_all("tr")]
            text_path = archive / f"{key}.txt"
            text_path.write_text(text, encoding="utf-8")
            inactive_sections = []
            if "inactive" in str(article.get("headline", "")).lower():
                for part in parts:
                    links = [urljoin(url, a["href"]) for a in part.find_all("a", href=True)
                             if "/games/" in a["href"]]
                    for subhead in part.find_all("h3"):
                        team = subhead.get_text(" ", strip=True)
                        following = subhead.find_next_sibling()
                        while following is not None and following.name not in ("ul", "h3"):
                            following = following.find_next_sibling()
                        if team and following is not None and following.name == "ul":
                            inactive_sections.append({"team_label": team,
                                                      "names_as_published": [li.get_text(" ", strip=True) for li in following.find_all("li")],
                                                      "game_links": links,
                                                      "group_team_labels": [h.get_text(" ", strip=True) for h in part.find_all("h3") if h.get_text(strip=True)]})
            related = sorted({urljoin(url, a["href"]) for a in soup.find_all("a", href=True)
                              if "/news/" in a["href"] and any(token in a["href"].lower() for token in
                                 ("inactive", "injury-report", "roster", "roster-cuts", "transaction"))
                              and urlparse(urljoin(url, a["href"])).hostname in ("www.nfl.com", "www.newyorkjets.com")})
            result = {"parser_version": 4, "reference": url, "resolved_url": response.url, "retrieved_at": previous["retrieved_at"] if previous else datetime.now(timezone.utc).isoformat(),
                      "http_status": response.status_code, "content_sha256": hashlib.sha256(raw).hexdigest(),
                      "published_at": article.get("datePublished"), "modified_at": article.get("dateModified"),
                      "title": article.get("headline") or (heading.get_text(" ", strip=True) if heading else ""),
                      "html_file": str(html_path), "text_file": str(text_path), "text_characters": len(text),
                      "inactive_sections": inactive_sections, "related_source_urls": related,
                      "transaction_rows": transaction_rows,
                      "pregame_version_verified": False}
            record_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result
        except Exception as error:
            last_error = str(error)
    return {"reference": url, "error": last_error}


def run(output: Path, cache: Path, archive: Path, extra_sources: Path | None = None):
    archive.mkdir(parents=True, exist_ok=True)
    records = {}
    discovery_errors = []
    # Sitemaps provide standalone prime-time inactives and roster/transaction
    # articles missed by the weekly Sunday roundup.
    sitemap_urls = [BASE + f"/sitemap/html/articles/2026/{month}" for month in (8, 9, 10, 11)]
    pending = set(SEEDS)
    if extra_sources:
        pending.update(json.loads(extra_sources.read_text(encoding="utf-8")))
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {pool.submit(fetch, url, archive): url for url in sitemap_urls}
        for task in as_completed(tasks):
            record = task.result()
            if "error" in record:
                discovery_errors.append(record)
            else:
                pending.update(record["related_source_urls"])
    # Two bounded discovery rounds. Preserve failures for source follow-up.
    for round_number in range(2):
        if not pending:
            break
        with ThreadPoolExecutor(max_workers=8) as pool:
            tasks = {pool.submit(fetch, url, archive): url for url in sorted(pending)[:200]}
            pending = set()
            for task in as_completed(tasks):
                record = task.result()
                records[record["reference"]] = record
                if "error" not in record:
                    pending.update(url for url in record["related_source_urls"] if url not in records)
        print(f"source round {round_number + 1}: {len(records)} URLs checked", flush=True)
    successful = [r for r in records.values() if "error" not in r]
    sections = [{"source": r["reference"], "title": r["title"], "published_at": r["published_at"], "modified_at": r["modified_at"],
                 "content_sha256": r["content_sha256"], **section} for r in successful for section in r["inactive_sections"]]
    games = [game for game in load_games(cache) if game["season"] == 2026]
    coverage = []
    for game in games:
        # Official game URL includes mascot pair and regular-season week.
        away, home = game["name"].split(" at ")
        mascot = lambda name: name.split()[-1].lower()
        suffix = f"{mascot(away)}-at-{mascot(home)}-2026-reg-{game['week']}"
        def matches(section):
            if not str(section["published_at"]).startswith("2026-"):
                return False
            if any(suffix in url for url in section["game_links"]):
                return True
            week_match = re.search(r"[Ww]eek (\d+)", section["title"])
            teams = {mascot(away), mascot(home)}
            return (week_match is not None and int(week_match.group(1)) == game["week"]
                    and {{"niners": "49ers", "eagls": "eagles"}.get(name.lower(), name.lower())
                         for name in section.get("group_team_labels", [])} == teams)
        matching = [section for section in sections if matches(section)]
        coverage.append({"game_id": game["id"], "week": game["week"], "game": game["name"],
                         "inactive_team_sections": len({s['team_label'] for s in matching}),
                         "sources": sorted({s["source"] for s in matching}),
                         "active_universe_verified": False, "historical_version_verified": False})
    summary = {"urls_checked": len(records), "downloaded": len(successful), "failed": len(records) - len(successful),
               "transaction_pages_with_rows": sum(bool(record.get("transaction_rows")) for record in successful),
               "transaction_rows_as_published": sum(len(record.get("transaction_rows", [])) for record in successful),
               "inactive_team_sections": len(sections), "games_with_linked_inactive_sections": sum(bool(row["sources"]) for row in coverage),
               "games": len(games), "fully_pregame_verified_games": 0}
    index = {"summary": summary, "sources": list(records.values()), "sitemap_failures": discovery_errors,
             "inactive_sections": sections, "game_coverage": coverage,
             "limits": ["Current pages may have been updated after kickoff; publication date alone does not establish historical content.",
                        "Inactive names are preserved as published; not yet reconciled to immutable historical roster IDs.",
                        "No complete historical active universe, versioned model-input bundle, or exact combined sportsbook quote is certified."]}
    (output / "pregame-source-inventory.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    lines = ["# Pregame source collection", "", "Source collection preserves current HTML, extracted text, publication/update metadata and SHA256 hashes. Retrieval time is never substituted for a historical availability timestamp. These pages are source evidence for reconstruction; no clean backtest result is certified yet.", "",
             "```json", json.dumps(summary, indent=2), "```", "", "## Sources", "",
             "| Source | Published | Updated | Extracted characters | Inactive team sections |", "|---|---|---|---:|---:|"]
    for record in successful:
        title = record["title"].replace("|", " ")
        lines.append(f"| [{title}]({record['reference']}) | {record['published_at']} | {record['modified_at']} | {record['text_characters']} | {len(record['inactive_sections'])} |")
    lines.extend(["", "## Game coverage", "", "| Week | Game | Linked inactive team sections | Complete historical active universe |", "|---:|---|---:|---|"])
    for row in coverage:
        lines.append(f"| {row['week']} | {row['game']} | {row['inactive_team_sections']} | Unverified |")
    lines.extend(["", "## Remaining acquisition", "", "Reconcile source names and game links to historical team rosters, obtain dated roster/transaction histories and snapshot/version evidence before each cutoff, and capture historical offered markets/combined quotes for financial testing. An absent name on an inactive list is not proof of roster membership. A merged weekly article updated after early kickoffs needs per-game earlier versions or standalone team announcements.", ""])
    (output / "pregame-source-inventory.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return index


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--extra-sources", type=Path)
    args = parser.parse_args()
    run(args.output, args.cache, args.archive, args.extra_sources)
