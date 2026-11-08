"""Archive all 32 current official roster/depth pages without backdating them."""
import argparse
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .source_based_parlays import DOMAINS


def parse_tables(html: bytes, url: str, kind: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for table_index, table in enumerate(soup.find_all("table")):
        headers = [cell.get_text(" ", strip=True) for cell in table.select("thead th")]
        if not headers:
            row = table.find("tr")
            headers = [cell.get_text(" ", strip=True) for cell in row.find_all("th")] if row else []
        heading = table.find_previous(["h2", "h3", "h4"])
        section = heading.get_text(" ", strip=True) if heading else "unknown"
        for row_index, row in enumerate(table.find_all("tr")):
            cells = row.find_all("td", recursive=False)
            if not cells:
                continue
            values = [cell.get_text(" ", strip=True) for cell in cells]
            if kind == "roster":
                if not any(value.lower() in ("pos", "position") for value in headers):
                    continue
                position_index = next(i for i, value in enumerate(headers) if value.lower() in ("pos", "position"))
                if position_index >= len(values):
                    continue
                anchor = cells[0].find("a")
                name = (anchor.get("title") or anchor.get_text(" ", strip=True)) if anchor else values[0]
                result.append({"player": name, "position": values[position_index], "section": section,
                               "player_reference": urljoin(url, anchor["href"]) if anchor and anchor.get("href") else None,
                               "columns": dict(zip(headers, values)), "table_index": table_index})
            elif kind == "injury":
                if any("injury" in h.lower() for h in headers):
                    result.append({"section": section, "columns": dict(zip(headers, values)), "table_index": table_index})
            elif headers and headers[0].lower() in ("position", "pos"):
                for depth, cell in enumerate(cells[1:], 1):
                    anchors = cell.find_all("a")
                    names = [(a.get("title") or a.get_text(" ", strip=True), a.get("href")) for a in anchors]
                    if not names and cell.get_text(strip=True):
                        names = [(cell.get_text(" ", strip=True), None)]
                    for name, href in names:
                        result.append({"player": re.sub(r"^\d+\s+", "", name), "position": values[0], "depth": depth,
                                       "alignment_row": f"{table_index}:{row_index}",
                                       "section": section, "table_index": table_index,
                                       "player_reference": urljoin(url, href) if href else None})
    return result


def fetch_page(task, archive):
    team, kind, url = task
    observed = datetime.now(timezone.utc).isoformat()
    try:
        response = requests.get(url, timeout=25, headers={"User-Agent": "CaseCapitalFootballResearch/0.1"})
        response.raise_for_status()
        raw = response.content
        sha = hashlib.sha256(raw).hexdigest()
        path = archive / (sha + ".html")
        if not path.exists():
            path.write_bytes(raw)
        rows = parse_tables(raw, response.url, kind)
        images = []
        if kind == "depth" and not rows:
            soup = BeautifulSoup(raw, "html.parser")
            for img in soup.select(".__image-centered img[src]"):
                image_url = urljoin(response.url, img["src"])
                image_response = requests.get(image_url, timeout=25)
                image_response.raise_for_status()
                image_sha = hashlib.sha256(image_response.content).hexdigest()
                image_path = archive / (image_sha + ".png")
                if not image_path.exists():
                    image_path.write_bytes(image_response.content)
                images.append({"reference": image_url, "content_sha256": image_sha,
                               "file": str(image_path), "status": "REQUIRES_VISUAL_TRANSCRIPTION"})
        return {"team": team, "kind": kind, "reference": url, "resolved_reference": response.url,
                "retrieved_at": observed, "content_sha256": sha, "html_file": str(path),
                "historical_version_verified": False, "status": "parsed" if rows else "UNPARSED",
                "rows": rows, "image_sources": images}
    except (requests.RequestException, ValueError) as exc:
        return {"team": team, "kind": kind, "reference": url, "retrieved_at": observed,
                "status": "FAILED", "error": str(exc), "rows": []}


def run(output: Path, archive: Path):
    archive.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    tasks = [(team, kind, f"https://www.{domain}.com/team/{suffix}/")
             for team, domain in DOMAINS.items()
             for kind, suffix in (("roster", "players-roster"), ("depth", "depth-chart"), ("injury", "injury-report"))]
    with ThreadPoolExecutor(max_workers=8) as pool:
        sources = list(pool.map(lambda task: fetch_page(task, archive), tasks))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    artifact = {"kind": "CURRENT_OFFICIAL_TEAM_CONTEXT", "historical_pregame_eligible": False,
                "warning": "Current retrieval only. Depth charts are unofficial; roster section is not game-day eligibility. Table parsing is not a completeness certificate.",
                "sources": sources}
    path = output / f"team-context-{stamp}.json"
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    lines = ["# Official roster and depth-chart collection", "", artifact["warning"], "",
             "| Team | Roster rows | Depth entries | Injury rows | Collection status |", "|---|---:|---:|---:|---|"]
    for team in DOMAINS:
        pair = [s for s in sources if s["team"] == team]
        lines.append(f"| {team} | {len(pair[0]['rows'])} | {len(pair[1]['rows'])} | {len(pair[2]['rows'])} | {' / '.join(s['status'] for s in pair)} |")
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": str(path), "pages": len(sources),
                      "parsed": sum(s["status"] == "parsed" for s in sources),
                      "missing": [{"team": s["team"], "kind": s["kind"], "status": s["status"]}
                                  for s in sources if s["status"] != "parsed"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    run(args.output, args.archive)
