"""Deterministic historical parlay constructions, separate from placed bets.

Only projection fields enter selection. Final outcomes are joined after the
ticket manifest is saved. This does not create a pregame ledger record.
"""

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

RULE_VERSION = "three-five-eight-current-season-v2"
LENGTHS = (3, 5, 8)
MIN_PROBABILITY = 0.55


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def projection(row: dict) -> dict:
    return {key: row[key] for key in ("season", "week", "game_id", "kickoff", "team_id", "player_id",
                                      "player", "market", "line", "calibrated_probability", "prior_game_ids")}


def pick_legs(rows: list[dict], length: int, game_weeks: dict[str, int]) -> list[dict]:
    eligible = [projection(row) for row in rows if row["calibration_status"] == "trained_on_prior_games"
                and float(row["calibrated_probability"]) >= MIN_PROBABILITY
                and game_weeks.get(row["prior_game_ids"].split(";")[-1], 999) < int(row["week"])]
    by_team: dict[str, list[dict]] = {}
    for row in eligible:
        by_team.setdefault(row["team_id"], []).append(row)
    if len(by_team) != 2:
        return []
    ordered = sorted(eligible, key=lambda row: (-float(row["calibrated_probability"]), row["player_id"],
                                                     row["market"], float(row["line"])))
    # One leading candidate from each team, then the highest remaining
    # probabilities. Every ticket uses distinct players.
    chosen = [next(row for row in ordered if row["team_id"] == team) for team in sorted(by_team)]
    used = {row["player_id"] for row in chosen}
    for row in ordered:
        if len(chosen) == length:
            break
        if row["player_id"] not in used:
            chosen.append(row)
            used.add(row["player_id"])
    return chosen if len(chosen) == length else []


def build_tickets(rows: list[dict]) -> list[dict]:
    by_game: dict[str, list[dict]] = {}
    game_weeks = {row["game_id"]: int(row["week"]) for row in rows}
    for row in rows:
        by_game.setdefault(row["game_id"], []).append(row)
    tickets = []
    for game_id, candidates in sorted(by_game.items(), key=lambda item: (item[1][0]["kickoff"], item[0])):
        for length in LENGTHS:
            legs = pick_legs(candidates, length, game_weeks)
            if legs:
                tickets.append({"ticket_id": f"research-2026-{game_id}-{length}", "season": 2026,
                                "week": int(candidates[0]["week"]), "game_id": game_id,
                                "kickoff": candidates[0]["kickoff"], "leg_count": length,
                                "legs": legs})
    return tickets


def grade_ticket(ticket: dict, outcomes: dict[tuple[str, str, str, str], dict]) -> dict:
    legs = []
    for leg in ticket["legs"]:
        key = (ticket["game_id"], leg["player_id"], leg["market"], leg["line"])
        row = outcomes[key]
        status = "unresolved" if row["outcome"] == "" else "won" if row["outcome"] == "1" else "lost"
        legs.append({"player_id": leg["player_id"], "player": leg["player"], "team_id": leg["team_id"],
                     "market": leg["market"], "line": leg["line"], "status": status,
                     "observed_yards": None if row["actual_yards"] == "" else float(row["actual_yards"]),
                     "source": f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={ticket['game_id']}"})
    status = "lost" if any(leg["status"] == "lost" for leg in legs) else (
        "unresolved" if any(leg["status"] == "unresolved" for leg in legs) else "won")
    return {"ticket_id": ticket["ticket_id"], "week": ticket["week"], "game_id": ticket["game_id"],
            "leg_count": ticket["leg_count"], "status": status, "legs": legs,
            "ticket_sha256": digest(ticket)}


def _save_once(path: Path, value: dict) -> None:
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise ValueError(f"existing replay artifact differs: {path}")
        return
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)


def run(source: Path, output: Path) -> dict:
    with source.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    projected = sorted((projection(row) for row in rows), key=lambda row: (
        row["game_id"], row["player_id"], row["market"], float(row["line"])))
    tickets = build_tickets(rows)
    manifest_path = output / "parlay-replay-v2-manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        if old["tickets_sha256"] != digest(tickets) or old["projection_sha256"] != digest(projected):
            raise ValueError("existing replay ticket manifest does not match projections")
        manifest = old
    else:
        manifest = {"kind": "historical_reconstruction", "rule_version": RULE_VERSION,
                    "not_pregame_observed": True, "created_at": datetime.now(timezone.utc).isoformat(),
                    "projection_sha256": digest(projected), "tickets_sha256": digest(tickets),
                    "selection_rule": {"leg_counts": LENGTHS, "minimum_calibrated_probability": MIN_PROBABILITY,
                                       "one_player_per_ticket": True, "require_both_teams": True,
                                       "ranking": "highest probability, then player ID, market, line"},
                    "tickets": tickets}
        _save_once(manifest_path, manifest)
    outcomes = {(row["game_id"], row["player_id"], row["market"], row["line"]): row for row in rows}
    if len(outcomes) != len(rows):
        raise ValueError("duplicate forecast key")
    settlements = [grade_ticket(ticket, outcomes) for ticket in manifest["tickets"]]
    settlement_file = {"kind": "historical_statistical_grade", "tickets_sha256": manifest["tickets_sha256"],
                       "settlements_sha256": digest(settlements), "settlements": settlements}
    _save_once(output / "parlay-replay-v2-settlements.json", settlement_file)
    summary = []
    for length in LENGTHS:
        for week in range(1, 9):
            subset = [item for item in settlements if item["leg_count"] == length and item["week"] == week]
            summary.append({"leg_count": length, "week": week, "constructed": len(subset),
                            "won": sum(item["status"] == "won" for item in subset),
                            "lost": sum(item["status"] == "lost" for item in subset),
                            "unresolved": sum(item["status"] == "unresolved" for item in subset)})
    report = {"kind": "historical_reconstruction", "rule_version": RULE_VERSION,
              "manifest_sha256": digest(manifest), "settlements_sha256": digest(settlements),
              "summary": summary, "total": {"constructed": len(settlements),
                                            "won": sum(item["status"] == "won" for item in settlements),
                                            "lost": sum(item["status"] == "lost" for item in settlements),
                                            "unresolved": sum(item["status"] == "unresolved" for item in settlements)}}
    (output / "parlay-replay-v2-summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output), indent=2))
