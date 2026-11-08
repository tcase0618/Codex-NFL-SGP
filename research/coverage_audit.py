"""Explain every missing parlay slot without changing ticket selection."""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from .parlay_replay import MIN_PROBABILITY
from .replay_2026 import BASE


def run(output: Path, cache: Path) -> dict:
    with (output / "replay-2026-calibrated.csv").open(newline="", encoding="utf-8") as handle:
        forecasts = list(csv.DictReader(handle))
    manifest = json.loads((output / "parlay-replay-v2-manifest.json").read_text(encoding="utf-8"))
    selected = {(ticket["game_id"], ticket["leg_count"]) for ticket in manifest["tickets"]}
    games = []
    for week in range(1, 9):
        url = f"{BASE}/scoreboard?dates=2026&seasontype=2&week={week}&limit=100"
        data = json.loads((cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")).read_bytes())
        for event in data["events"]:
            games.append({"week": week, "game_id": event["id"], "game": event["name"]})
    game_weeks = {row["game_id"]: int(row["week"]) for row in forecasts}
    slots = []
    for game in games:
        rows = [row for row in forecasts if row["game_id"] == game["game_id"]]
        current = [row for row in rows if game_weeks.get(row["prior_game_ids"].split(";")[-1], 999) < game["week"]]
        eligible = [row for row in current if row["calibration_status"] == "trained_on_prior_games"
                    and float(row["calibrated_probability"]) >= MIN_PROBABILITY]
        players = len({row["player_id"] for row in eligible})
        teams = len({row["team_id"] for row in eligible})
        for kind in (3, 5, 8, "TD"):
            if kind == "TD":
                reason = "TD forecast and ticket builder not implemented in replay"
            elif (game["game_id"], kind) in selected:
                reason = "constructed"
            elif not current:
                reason = "no current-season prior-team appearance proxy"
            elif teams < 2:
                reason = "55% floor leaves fewer than two teams"
            elif players < kind:
                reason = f"55% floor leaves only {players} distinct players for {kind} legs"
            else:
                raise ValueError(f"unexplained missing ticket: {game} {kind}")
            slots.append({**game, "ticket_type": str(kind), "forecast_rows": len(rows),
                          "current_season_rows": len(current), "eligible_rows": len(eligible),
                          "eligible_distinct_players": players, "eligible_teams": teams, "reason": reason})
    with (output / "parlay-game-coverage.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(slots[0]))
        writer.writeheader()
        writer.writerows(slots)
    totals = {kind: dict(Counter(row["reason"] for row in slots if row["ticket_type"] == kind))
              for kind in ("3", "5", "8", "TD")}
    lines = ["# Missing parlay coverage by game", "",
             f"The cached schedules contain {len(games)} games and {len(slots)} requested game/type slots. The replay constructed {len(selected)} tickets. Every omitted slot is retained below and in parlay-game-coverage.csv.", "",
             "The replay implements a narrow yardage baseline, not the full football engine. Only 200 passing, 40 rushing and 40 receiving thresholds are candidates. It requires two prior observations, positive workload and efficiency, a calibrated probability of at least 55%, distinct players, both teams, and a current-season prior-team appearance proxy. Those are implementation choices, not proof that other tickets were impossible or unbettable.", "",
             "TD is absent because the replay never creates TD probabilities or TD tickets. parse_box discards touchdowns; the core engine's separate TD scoring model is not wired into the replay. The replay also bypasses evaluate_ticket and its seven audit statuses, eight skill statuses, scenario-specific opportunity gates and joint-sample correlation requirement. It has no sportsbook line inventory. Thus these constructions do not represent the full engine's approved tickets.", "",
             "Week 1 is entirely suppressed by the current-season appearance proxy. A prior appearance also does not prove current eligibility in later weeks. Historical roster/injury/inactive evidence is required to replace that proxy. Missing final outcomes affect settlement, not candidate selection.", "",
             "## Weekly coverage", "", "| Week | Games | 3-leg | 5-leg | 8-leg | TD | Missing slots |",
             "|---:|---:|---:|---:|---:|---:|---:|"]
    for week in range(1, 9):
        n = sum(game["week"] == week for game in games)
        counts = [sum(row["week"] == week and row["ticket_type"] == kind and row["reason"] == "constructed" for row in slots)
                  for kind in ("3", "5", "8", "TD")]
        lines.append(f"| {week} | {n} | {' | '.join(map(str, counts))} | {n * 4 - sum(counts)} |")
    lines.extend(["", "## Every game", "", "| Week | Matchup | 3-leg | 5-leg | 8-leg | TD |",
                  "|---:|---|---|---|---|---|"])
    for game in games:
        reasons = [next(row["reason"] for row in slots if row["game_id"] == game["game_id"] and row["ticket_type"] == kind)
                   for kind in ("3", "5", "8", "TD")]
        lines.append(f"| {game['week']} | {game['game']} | {' | '.join(reasons)} |")
    (output / "parlay-game-coverage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = {"games": len(games), "requested_slots": len(slots), "constructed": len(selected),
              "missing_slots": len(slots) - len(selected), "reasons": totals}
    (output / "parlay-game-coverage-summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.cache), indent=2))
