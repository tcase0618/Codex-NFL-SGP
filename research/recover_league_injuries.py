"""Recover official weekly report contents, never backdate their availability."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from football_engine.team_profiles import content_hash, timestamp
from .collect_coaching import collect_evidence


def parse_week(raw, resolved_reference, season, week):
    path = f"/injuries/league/{season}/reg{week}"
    parsed = urlparse(resolved_reference)
    if parsed.scheme != "https" or parsed.netloc != "www.nfl.com" or parsed.path != path:
        raise ValueError("weekly injury route redirected or has incorrect scope")
    soup = BeautifulSoup(raw, "html.parser")
    scopes = {urlparse(urljoin(resolved_reference, o["value"])).path
              for o in soup.select("option[selected][value]")
              if "/injuries/league/" in o["value"]}
    if scopes != {path}:
        raise ValueError("weekly injury selected scope differs from requested season/week")
    records, errors, seen = [], [], set()
    for unit in soup.select("section.nfl-o-injury-report__unit"):
        teams = unit.select(".nfl-c-matchup-strip__team")
        tables = unit.find_all("table")
        if len(teams) != 2 or len(tables) != 2:
            errors.append("weekly matchup team/table coverage incomplete")
            continue
        identities = []
        for team in teams:
            abbr = team.select_one(".nfl-c-matchup-strip__team-abbreviation")
            name = team.select_one(".nfl-c-matchup-strip__team-fullname")
            logos = set(re.findall(r"/clubs/logos/([A-Z]+)(?=[/.,?\s\"<]|$)", str(team)))
            code = abbr.get_text(strip=True) if abbr else None
            link = urlparse(urljoin(resolved_reference, name.get("href", ""))) if name else None
            expected_logos = {"LA"} if code == "LAR" and link and link.path.rstrip("/") == "/teams/los-angeles-rams" else {code}
            if (not code or logos != expected_logos or not link or link.netloc != "www.nfl.com"
                    or not re.fullmatch(r"/teams/[^/]+/?", link.path)):
                identities = []
                break
            aliases = {("AZ", "/teams/arizona-cardinals"): "ARI",
                       ("WAS", "/teams/washington-commanders"): "WSH"}
            if code in ("AZ", "WAS") and (code, link.path.rstrip("/")) not in aliases:
                identities = []
                break
            canonical = aliases.get((code, link.path.rstrip("/")), code)
            identities.append((canonical, name.get_text(" ", strip=True), link.geturl()))
        if len(identities) != 2 or identities[0][0] == identities[1][0]:
            errors.append("weekly team ownership unverified")
            continue
        for table, (code, name, reference) in zip(tables, identities):
            title = table.find_previous(class_="nfl-t-stats__title")
            if not title or title.get_text(" ", strip=True) != name or title.find_parent("section") is not unit:
                errors.append("weekly table/team heading mismatch")
                continue
            headers = [t.get_text(" ", strip=True) for t in table.select("thead th")]
            if headers != ["Player", "Position", "Injuries", "Practice Status", "Game Status"]:
                errors.append("weekly injury table schema changed")
                continue
            rows = []
            for tr in table.select("tbody tr"):
                cells = tr.find_all("td", recursive=False)
                anchors = cells[0].find_all("a", href=True) if cells else []
                if len(cells) != 5 or len(anchors) != 1:
                    errors.append("weekly injury row shape invalid")
                    continue
                player_ref = urlparse(urljoin(resolved_reference, anchors[0]["href"]))
                values = [c.get_text(" ", strip=True) for c in cells]
                key = (code, player_ref.geturl())
                if (player_ref.scheme != "https" or player_ref.netloc != "www.nfl.com"
                        or not re.fullmatch(r"/players/[^/]+/?", player_ref.path)
                        or not values[0] or not values[1] or key in seen):
                    errors.append("weekly player identity missing, ambiguous or duplicated")
                    continue
                seen.add(key)
                rows.append(dict(player_reference=player_ref.geturl(), columns=dict(zip(headers, values))))
            if not rows:
                errors.append("weekly report rows unavailable")
                continue
            if any(r["team_id"] == code for r in records):
                errors.append("weekly team appears in multiple matchups")
                continue
            opponent = next(t[0] for t in identities if t[0] != code)
            records.append(dict(team_id=code, team_reference=reference, opponent_id=opponent,
                                season=season, week=week, rows=rows,
                                current_eligibility_usable=False,
                                historical_pregame_version_verified=False))
    if not records:
        errors.append("no verified weekly injury tables")
    return dict(status="RECOVERED_REPORT_CONTENTS" if not errors else "PARTIAL_REPORT_CONTENTS",
                reports=records, errors=errors, game_day_active_proof=False)


def recover(output, archive, season, weeks, fetch=collect_evidence):
    output, archive = Path(output), Path(archive)
    output.mkdir(parents=True, exist_ok=False)
    archive.mkdir(parents=True, exist_ok=True)
    def one(week):
        requested = f"https://www.nfl.com/injuries/league/{season}/reg{week}"
        source = fetch(requested, archive)
        try:
            if source.get("error"):
                raise ValueError(source["error"])
            raw = Path(source["archive_file"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != source["sha256"]:
                raise ValueError("weekly injury archive hash mismatch")
            result = parse_week(raw, source.get("resolved_reference", source["reference"]), season, week)
        except (KeyError, OSError, ValueError) as exc:
            result = dict(status="UNAVAILABLE", reports=[], errors=[str(exc)])
        return dict(requested_reference=requested, source=source, **result)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, weeks))
    result = dict(generated_at=datetime.now(timezone.utc).isoformat(), season=season,
                  report_count=sum(len(r["reports"]) for r in results), weeks=results,
                  scope="Actual official injury-report contents retrieved now; no reconstructed pregame availability or active status",
                  historical_pregame_certified=False)
    (output / "recovered-injuries.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def verified_historical_reports(path, expected_sha256, as_of):
    """Reparse official raw archives rather than trust stored derived rows."""
    path = Path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("historical injury bundle hash mismatch")
    bundle = json.loads(path.read_text())
    cutoff = timestamp(as_of)
    if timestamp(bundle["generated_at"]) > cutoff:
        raise ValueError("historical injury bundle generated after cutoff")
    reports, inventory = [], set()
    for item in bundle["weeks"]:
        source = item["source"]
        if timestamp(source["observed_at"]) > cutoff:
            raise ValueError("historical injury source observed after cutoff")
        raw = Path(source["archive_file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError("historical injury raw archive hash mismatch")
        requested = urlparse(item["requested_reference"])
        match = re.fullmatch(r"/injuries/league/(20\d{2})/reg(\d+)", requested.path)
        if not match or int(match[1]) != bundle["season"]:
            raise ValueError("historical injury source scope invalid")
        parsed = parse_week(raw, source["reference"], int(match[1]), int(match[2]))
        if parsed["errors"]:
            raise ValueError("historical injury source incomplete")
        for report in parsed["reports"]:
            key = (report["team_id"], report["season"], report["week"])
            if key in inventory:
                raise ValueError("duplicate historical injury scope")
            inventory.add(key)
            reports.append({**report, "source": source})
    if len(reports) != bundle["report_count"]:
        raise ValueError("historical injury inventory differs from source")
    return reports


def attach_historical_reports(profile, reports):
    """Attach matched game context without changing current active eligibility."""
    attached = []
    for report in reports:
        if report["team_id"] != profile["team_id"]:
            continue
        matches = [g for g in profile["game_observations"] if
                   g["season"] == report["season"] and g["week"] == report["week"]
                   and g["opponent"] == report["opponent_id"]]
        if len(matches) != 1:
            raise ValueError("historical injury game identity is absent or ambiguous")
        if timestamp(report["source"]["observed_at"]) > timestamp(profile["as_of"]):
            raise ValueError("historical injury source after profile cutoff")
        attached.append({**report, "game_id": matches[0]["game_id"]})
    updated = {**profile, "personnel": {**profile.get("personnel", {}),
               "historical_official_injury_reports": attached}}
    updated["version"] = content_hash({k:v for k,v in updated.items() if k != "version"})
    return updated


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--weeks", type=int, nargs="+", required=True)
    args = p.parse_args()
    result = recover(args.output, args.archive, args.season, args.weeks)
    print(json.dumps(dict(reports=result["report_count"], weeks=[dict(reference=w["requested_reference"], status=w["status"], errors=w["errors"]) for w in result["weeks"]])))
