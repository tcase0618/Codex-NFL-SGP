"""Archive official current coaching directories and principal coach biographies."""
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


def archive_url(url, archive):
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    digest = hashlib.sha256(response.content).hexdigest()
    path = archive / (digest + ".html")
    if not path.exists():
        path.write_bytes(response.content)
    return {"reference": response.url, "sha256": digest, "observed_at": datetime.now(timezone.utc).isoformat(),
            "archive_file": str(path), "historical_version_verified": False}, BeautifulSoup(response.content, "html.parser")


def staff_cards(soup, url):
    staff = []
    for heading in soup.select("h3"):
        parent = heading.parent
        roof = parent.select_one("h5, .d3-o-media-object__roofline")
        if not roof:
            continue
        name, title = heading.get_text(" ", strip=True), roof.get_text(" ", strip=True)
        card = heading.find_parent(class_=lambda v: v and "d3-o-media-object" in v.split()) or parent
        anchors = ([card] if card.name == "a" and card.get("href") else []) + card.find_all("a", href=True)
        link = next((a["href"] for a in anchors if "/coaches" in a["href"] and not a["href"].rstrip("/").endswith(("coaches", "coaches-roster"))), None)
        if not link:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            link = next((a["href"] for a in soup.find_all("a", href=True) if a["href"].rstrip("/").endswith("/" + slug)), None)
        normalized_title = title.lower().removeprefix("interim ")
        role = "head_coach" if normalized_title == "head coach" else "offensive_coordinator" if normalized_title == "offensive coordinator" else "defensive_coordinator" if normalized_title == "defensive coordinator" else None
        if role:
            staff.append({"name": name, "title": title, "role": role, "bio_reference": urljoin(url, link) if link else None})
    return staff


def collect_team(task, archive):
    team, domain = task
    errors = []
    for suffix in ("coaches-roster/", "coaches/", "coaches-roster/index-app"):
        try:
            source, soup = archive_url(f"https://www.{domain}.com/team/{suffix}", archive)
            staff = staff_cards(soup, source["reference"])
            if staff:
                return {"team_id": team, "directory_source": source, "staff": staff}
            errors.append(suffix + ": no staff parsed")
        except requests.RequestException as exc:
            errors.append(str(exc))
    return {"team_id": team, "staff": [], "errors": errors}


def collect_bio(task, archive):
    team, coach = task
    if not coach["bio_reference"]:
        return team, coach
    try:
        source, soup = archive_url(coach["bio_reference"], archive)
        parts = soup.select(".nfl-c-body-part")
        body = soup.select_one("article") or soup
        text = "\n".join(part.get_text(" ", strip=True) for part in parts) if parts else body.get_text(" ", strip=True)
        text_path = archive / (source["sha256"] + ".txt")
        text_path.write_text(text, encoding="utf-8")
        source["text_file"] = str(text_path)
        coach = {**coach, "bio_source": source}
    except requests.RequestException as exc:
        coach = {**coach, "bio_error": str(exc)}
    return team, coach


def collect_evidence(url, archive):
    try:
        source, soup = archive_url(url, archive)
        parts = soup.select(".nfl-c-body-part")
        body = soup.select_one("article") or soup
        text = "\n".join(p.get_text(" ", strip=True) for p in parts) if parts else body.get_text(" ", strip=True)
        path = archive / (source["sha256"] + ".txt")
        path.write_text(text, encoding="utf-8")
        return {**source, "text_file": str(path), "title": soup.title.get_text(strip=True) if soup.title else ""}
    except requests.RequestException as exc:
        return {"reference": url, "error": str(exc)}


def run(output, archive):
    archive.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        teams = list(pool.map(lambda t: collect_team(t, archive), DOMAINS.items()))
        bios = list(pool.map(lambda t: collect_bio(t, archive), [(t["team_id"], c) for t in teams for c in t["staff"]]))
    for team in teams:
        team["staff"] = [coach for key, coach in bios if key == team["team_id"]]
    artifact = {"kind": "CURRENT_OFFICIAL_COACHING_DIRECTORY", "teams": teams}
    with output.open("x", encoding="utf-8") as handle:
        json.dump(artifact, handle, indent=2)
    print(json.dumps({"output": str(output), "teams": len(teams),
                      "roles": sum(len(t["staff"]) for t in teams),
                      "missing": [t["team_id"] for t in teams if len(t["staff"]) != 3]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    run(args.output, args.archive)
