"""Participation-filtered reconstruction. Explicitly NOT a pregame backtest.

Outcome-derived participation is hindsight. Keep these artifacts separate from
the original replay and never promote their scores as a pregame strategy edge.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from .complete_parlays import BASE, LINES, box_with_td, cached_json, candidates, load_games, select, settle
from .parlay_replay import digest, _save_once
from .resolve_participation import roster_url


def filter_participants(pool: list[dict], rosters: dict) -> list[dict]:
    result = []
    for leg in pool:
        roster = rosters.get(leg["team_id"])
        if not roster or not roster.get("entries") or not roster.get("source"):
            raise ValueError("missing game-specific roster evidence")
        entry = next((entry for entry in roster["entries"]
                      if str(entry["playerId"]) == leg["player_id"]), None)
        if entry is not None and entry.get("didNotPlay") is False:
            result.append({**leg, "eligibility": "POSTGAME_PARTICIPATION_CONFIRMED_NOT_PREGAME",
                           "eligibility_source": roster["source"], "roster_sha256": digest(roster["entries"]),
                           "roster_entry": entry})
    return result


def run(output: Path, cache: Path) -> dict:
    history = defaultdict(list)
    tickets = []
    skips = []
    targets = []
    for game in load_games(cache):
        if game["season"] == 2026:
            raw = cached_json(f"{BASE}/summary?event={game['id']}", cache)
            rosters = {}
            for competitor in raw["header"]["competitions"][0]["competitors"]:
                team = competitor["team"]
                url = roster_url(game["id"], team["id"])
                rosters[team["abbreviation"]] = {**cached_json(url, cache), "source": url}
            pool = filter_participants(candidates(game, history), rosters)
            targets.append(game)
            for kind, length in (("3", 3), ("5", 5), ("8", 8), ("TD", 3)):
                try:
                    legs = select([leg for leg in pool if (leg["market"] == "anytime_td") == (kind == "TD")], length)
                except ValueError as error:
                    skips.append({"game_id": game["id"], "week": game["week"], "type": kind, "reason": str(error)})
                    continue
                tickets.append({"ticket_id": f"participants-{game['id']}-{kind}", "type": kind,
                                "game_id": game["id"], "week": game["week"], "game": game["name"],
                                "kickoff": game["kickoff"], "legs": legs})
        box = box_with_td(game, cache)
        for team in box["teams"]:
            history[team].append(box)
    boxes = {game["id"]: box_with_td(game, cache) for game in targets}
    grades = []
    for ticket in tickets:
        grade = settle(ticket, boxes[ticket["game_id"]])
        for leg in grade["legs"]:
            if leg["status"] == "unresolved":
                # Game roster explicitly confirms play; absent offensive stat
                # means zero production, not DNP, for this reconstruction.
                leg["observed"] = 0
                leg["status"] = "lost"
                leg["zero_basis"] = "confirmed participant absent from final offensive box"
        grade["status"] = "lost" if any(leg["status"] == "lost" for leg in grade["legs"]) else "won"
        grades.append(grade)
    assert len(tickets) + len(skips) == len(targets) * 4
    assert all(leg["roster_entry"].get("didNotPlay") is False for ticket in tickets for leg in ticket["legs"])
    artifact = {"kind": "POSTGAME_PARTICIPATION_FILTERED_RECONSTRUCTION",
                "valid_pregame_backtest": False, "hindsight_participation_filter": True,
                "rules": {"yardage_lines": LINES, "TD_legs": 3, "prior_history": "last four team games",
                          "ranking": "unchanged smoothed prior frequency"},
                "tickets_sha256": digest(tickets), "settlements_sha256": digest(grades),
                "tickets": tickets, "settlements": grades, "skipped": skips}
    _save_once(output / "active-player-parlays.json", artifact)
    summary = []
    for kind in ("3", "5", "8", "TD"):
        subset = [grade for ticket, grade in zip(tickets, grades) if ticket["type"] == kind]
        summary.append({"type": kind, "tickets": len(subset), "won": sum(grade["status"] == "won" for grade in subset),
                        "lost": sum(grade["status"] == "lost" for grade in subset),
                        "skipped": sum(item["type"] == kind for item in skips)})
    lines = ["# Participation-filtered parlay reconstruction", "",
             "Every selected player has an explicit didNotPlay=false entry on the selected team's game-specific ESPN roster. These are postgame participation records, not archived pregame active lists. Filtering with them introduces hindsight and can improve apparent performance by excluding active players who never played. Consequently this result is not a valid pregame backtest or independent strategy validation. Original tickets are preserved.", "",
             "The yardage thresholds remain 150+ passing, 20+ rushing, and 25+ receiving. TD means three rushing/receiving scorers. Ranking still uses prior-game hit frequency, distinct players, both teams, and no probability floor. New and transferred players with no earlier same-team data are not introduced using future statistics. Unknown/DNP/wrong-team entries cannot be selected. Tickets with insufficient eligible prior-history players are explicitly skipped.", "",
             "| Type | Constructed | Won | Lost | Skipped |", "|---|---:|---:|---:|---:|"]
    for row in summary:
        lines.append(f"| {row['type']} | {row['tickets']} | {row['won']} | {row['lost']} | {row['skipped']} |")
    for week in range(1, 9):
        lines.extend(["", f"## Week {week}", "", "| Game | Type | Result | Exact legs and observations |", "|---|---|---|---|"])
        for ticket, grade in zip(tickets, grades):
            if ticket["week"] == week:
                detail = "; ".join(f"{leg['player']} ({leg['team_id']}) {leg['market']} {leg['line']}+: {leg['observed']} [{leg['status']}]" for leg in grade["legs"])
                lines.append(f"| {ticket['game']} | {ticket['type']} | {grade['status']} | {detail} |")
        for skip in skips:
            if skip["week"] == week:
                lines.append(f"| {skip['game_id']} | {skip['type']} | skipped | {skip['reason']} |")
    lines.extend(["", "The JSON artifact includes each exact roster entry, game-specific roster URL and hash, prior history IDs, ticket hash, final-box source and settlement. Sportsbook offering, quoted prices and actual wagering returns remain unverified.", ""])
    (output / "active-player-parlays.md").write_text("\n".join(lines), encoding="utf-8")
    return {"games": len(targets), "tickets": len(tickets), "skips": len(skips), "summary": summary}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.cache), indent=2))
