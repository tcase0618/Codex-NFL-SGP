"""Unpriced retrospective statistical replay, never a certified pregame ledger.

Dated depth charts establish provisional membership; weekly roster reserve states
and official inactive reports exclude players. Current-game participation never
ranks or filters candidates. Downloaded historical versions remain unverified.
"""
import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .complete_parlays import BASE, box_with_td, candidates, load_games, settle
from .parlay_replay import digest, _save_once
from .source_based_parlays import LABELS, POSITION, choose, instant, norm

TEAM_ALIASES = {"LA": "LAR", "WAS": "WSH"}


def roster_index(path):
    rows = defaultdict(set)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("game_type") == "REG" and row.get("espn_id"):
                team = TEAM_ALIASES.get(row["team"], row["team"])
                rows[(int(row["week"]), team, row["espn_id"])].add(row["status"])
    return rows


def roster_clear(leg, week, rosters):
    # ACT clears only the roster-state screen. It does not prove game-day active
    # status; the separate official inactive exclusion must still pass.
    states = rosters.get((week, leg["team_id"], leg["player_id"]), set())
    return states == {"ACT"}


def history_box(game, cache, refreshed=None):
    url = f"{BASE}/summary?event={game['id']}"
    name = hashlib.sha256(url.encode()).hexdigest() + ".json"
    selected = refreshed if refreshed and (refreshed / name).exists() else cache
    box = box_with_td(game, selected)
    if len(box["teams"]) != 2 or not all(box["teams"].values()):
        raise ValueError(f"incomplete player history: {game['id']}")
    return box, {"game_id": game["id"], "path": str(selected / name),
                 "sha256": hashlib.sha256((selected / name).read_bytes()).hexdigest(),
                 "historical_available_at_verified": False}


def depth_index(path):
    snapshots = defaultdict(lambda: defaultdict(set))
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("espn_id") and row.get("dt"):
                team = TEAM_ALIASES.get(row["team"], row["team"])
                snapshots[team][row["dt"]].add(row["espn_id"])
    return {team: sorted(items.items()) for team, items in snapshots.items()}


def membership(index, team, cutoff):
    available = [(stamp, ids) for stamp, ids in index.get(team, []) if instant(stamp) <= cutoff]
    if not available:
        return None, set()
    stamp, ids = available[-1]
    if cutoff - instant(stamp) > timedelta(hours=48):
        return stamp, set()
    return stamp, ids


def eligibility(leg, cutoff, index, sections):
    stamp, ids = membership(index, leg["team_id"], cutoff)
    if leg["player_id"] not in ids:
        return False, "missing_recent_dated_depth_membership", stamp
    if not sections:
        return False, "missing_official_inactive_section", stamp
    for section in sections:
        if not section.get("published_at") or instant(section["published_at"]) > cutoff:
            return False, "inactive_report_after_cutoff", stamp
        for name in section["names_as_published"]:
            cleaned = norm(re.sub(r"\([^)]*\)", "", POSITION.sub("", name)))
            if cleaned == norm(leg["player"]):
                return False, "officially_listed_inactive", stamp
    return True, "RECONSTRUCTED_ELIGIBILITY_NOT_VERSION_CERTIFIED", stamp


def fetch_final(game, destination):
    url = f"{BASE}/summary?event={game['id']}"
    try:
        response = requests.get(url, timeout=35)
        response.raise_for_status()
        raw = response.content
        data = json.loads(raw)
        competition = data["header"]["competitions"][0]
        if str(data["header"]["id"]) != game["id"] or competition["status"]["type"]["name"] != "STATUS_FINAL":
            raise ValueError("wrong game or non-final response")
        teams = {item["team"]["abbreviation"] for item in competition["competitors"]}
        if teams != set(game["scheduled_teams"]):
            raise ValueError("box-score team mismatch")
        target = destination / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        with target.open("xb") as handle:
            handle.write(raw)
        return {"game_id": game["id"], "source": url, "path": str(target),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "observed_at": datetime.now(timezone.utc).isoformat(), "status": "FINAL_FETCHED"}
    except Exception as exc:
        return {"game_id": game["id"], "source": url, "status": "FETCH_FAILED", "error": str(exc)}


def run(root, depth, roster, output, refreshed_history=None):
    output.mkdir(parents=True, exist_ok=False)
    inventory_path = root / "outputs/pregame-source-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    index = depth_index(depth)
    rosters = roster_index(roster)
    games = load_games(root / "work/espn-cache")
    targets = [game for game in games if game["season"] == 2026]
    coverage = {row["game_id"]: row["sources"] for row in inventory["game_coverage"]}
    history = defaultdict(list)
    tickets, exclusions, history_sources = [], [], []
    for game in games:
        if game["season"] == 2026:
            cutoff = instant(game["kickoff"]) - timedelta(minutes=30)
            pool = []
            for leg in candidates(game, history):
                labels = {LABELS[leg["team_id"]]}
                if leg["team_id"] == "SF": labels.add("49ERS")
                if leg["team_id"] == "PHI": labels.add("EAGLS")
                sections = [section for section in inventory["inactive_sections"]
                            if section["source"] in coverage.get(game["id"], []) and section["team_label"] in labels]
                ok, reason, stamp = eligibility(leg, cutoff, index, sections)
                if ok and not roster_clear(leg, game["week"], rosters):
                    ok, reason = False, "weekly_roster_reserve_inactive_missing_or_conflicting"
                if ok:
                    pool.append({**leg, "eligibility": reason, "depth_observed_at": stamp,
                                 "inactive_sources": sorted({section["source"] for section in sections})})
                else:
                    exclusions.append({"game_id": game["id"], "player_id": leg["player_id"], "player": leg["player"], "reason": reason})
            for kind, length in (("3", 3), ("5", 5), ("8", 8), ("TD", 3)):
                legs = choose([leg for leg in pool if (leg["market"] == "anytime_td") == (kind == "TD")], length)
                tickets.append({"ticket_id": f"depth-reconstruction-{game['id']}-{kind}",
                                "game_id": game["id"], "game": game["name"], "week": game["week"],
                                "kickoff": game["kickoff"], "decision_cutoff": cutoff.isoformat(),
                                "type": kind, "legs": legs, "status": "PROVISIONAL" if legs else "BLOCKED"})
        # Current-game rows enter only the history for later kickoffs.
        box, source = history_box(game, root / "work/espn-cache", refreshed_history)
        history_sources.append(source)
        for team in box["teams"]:
            history[team].append(box)
    manifest = {"kind": "UNPRICED_RETROSPECTIVE_STATISTICAL_RECONSTRUCTION", "valid_pregame_backtest": False,
                "engine_validation": False, "historically_frozen": False, "tickets_sha256": digest(tickets),
                "created_at": datetime.now(timezone.utc).isoformat(), "tickets": tickets,
                "lagged_history_sources": history_sources,
                "selection_sources": {"depth": str(depth), "depth_sha256": hashlib.sha256(depth.read_bytes()).hexdigest(),
                                      "weekly_roster": str(roster), "weekly_roster_sha256": hashlib.sha256(roster.read_bytes()).hexdigest(),
                                      "inactive_inventory_sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest()},
                "rules": {"targets": {"passing_yards": 150, "rushing_yards": 20, "receiving_yards": 25},
                          "TD": "3 rushing/receiving touchdown scorers, passing touchdowns excluded",
                          "ranking": "last-four-team-game smoothed hit frequency; not calibrated probabilities",
                          "weekly_roster_status_used": "ACT-only roster screen; weekly historical version availability unverified; not proof of game-day active status", "postgame_participation_used_for_selection": False,
                          "historical_source_versions_verified": False, "maximum_depth_age_hours": 48}}
    # Freeze every selection before any current-game settlement lookup.
    _save_once(output / "selection-manifest.json", manifest)
    final_cache = output / "final-boxes"
    final_cache.mkdir()
    with ThreadPoolExecutor(max_workers=6) as executor:
        fetches = list(executor.map(lambda game: fetch_final(game, final_cache), targets))
    _save_once(output / "final-source-manifest.json", {"sources": fetches})
    source_by_id = {row["game_id"]: row for row in fetches}
    by_id = {game["id"]: game for game in targets}
    grades = []
    for ticket in tickets:
        if not ticket["legs"]:
            grade = {"ticket_id": ticket["ticket_id"], "status": "blocked", "legs": []}
        elif source_by_id[ticket["game_id"]]["status"] != "FINAL_FETCHED":
            grade = {"ticket_id": ticket["ticket_id"], "status": "unresolved", "legs": []}
        else:
            grade = settle(ticket, box_with_td(by_id[ticket["game_id"]], final_cache))
        grades.append({**grade, "week": ticket["week"], "type": ticket["type"], "game": ticket["game"]})
    _save_once(output / "statistical-outcomes.json", {"tickets_sha256": digest(tickets), "grades": grades})
    counts = lambda rows: dict(Counter(row["status"] for row in rows))
    summary = {"kind": manifest["kind"], "valid_pregame_backtest": False, "roi": None,
               "games": len(targets), "slots": len(tickets), "counts": counts(grades),
               "by_type": {kind: counts([row for row in grades if row["type"] == kind]) for kind in ("3", "5", "8", "TD")},
               "by_week": {week: counts([row for row in grades if row["week"] == week]) for week in range(1, 9)},
               "final_fetches": dict(Counter(row["status"] for row in fetches)),
               "exclusions": dict(Counter(row["reason"] for row in exclusions))}
    _save_once(output / "summary.json", summary)
    _save_once(output / "exclusions.json", {"records": exclusions})
    lines = ["# Eight-week statistical reconstruction", "",
             "**Provisional retrospective target-hit results, not a verified pregame betting record or validation of the full engine.**", "",
             "Selection uses lagged boxes, stable ESPN IDs in the latest depth chart no older than 48 hours before the cutoff, a weekly roster-state screen, and official inactive reports published before kickoff minus 30 minutes. Only unambiguous ACT roster entries clear the reserve/inactive screen; ACT alone is not proof of game-day active status. Current-game participation is not used. All selections were saved before final boxes were fetched for grading. Historical file versions, weekly status availability and edited article contents are not certified. Eligibility remains reconstructed, not a certified historical active roster.", "",
             "Targets are research thresholds, not verified sportsbook offers: 150+ passing, 20+ rushing, 25+ receiving yards. TD is three distinct rushing/receiving scorers. Ranking is a lagged frequency baseline, not the engine's supplied opportunity/scenario model. Rules were chosen after looking at this season; this is not untouched validation. No prices, expected value or ROI are asserted. Missing box-score players stay unresolved; a ticket with an observed failed leg is statistically lost even if another leg is unresolved. Sportsbook void rules are not modeled.", "",
             "| Family | Statistical hits | Statistical misses | Unresolved | Blocked |", "|---|---:|---:|---:|---:|"]
    for kind, row in summary["by_type"].items():
        lines.append(f"| {kind} | {row.get('won',0)} | {row.get('lost',0)} | {row.get('unresolved',0)} | {row.get('blocked',0)} |")
    lines += ["", "| Week | Hits | Misses | Unresolved | Blocked |", "|---:|---:|---:|---:|---:|"]
    for week, row in summary["by_week"].items():
        lines.append(f"| {week} | {row.get('won',0)} | {row.get('lost',0)} | {row.get('unresolved',0)} | {row.get('blocked',0)} |")
    lines += ["", "## Every game and failed leg", "", "| Week | Game | Family | Result | Failed/unresolved legs |", "|---:|---|---|---|---|"]
    for row in grades:
        failed = "; ".join(f"{leg['player']} {leg['market']} {leg['line']}+: {leg.get('observed')} ({leg['status']})" for leg in row["legs"] if leg["status"] != "won") or "—"
        lines.append(f"| {row['week']} | {row['game']} | {row['type']} | {row['status']} | {failed} |")
    (output / "report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    _save_once(output / "sha256.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--depth", type=Path, required=True)
    parser.add_argument("--roster", type=Path, required=True)
    parser.add_argument("--history-cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.depth, args.roster, args.output, args.history_cache), indent=2))
