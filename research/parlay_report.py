"""Render and verify the historical parlay replay artifacts."""

import argparse
import json
from pathlib import Path

from .parlay_replay import digest


def _leg_label(leg: dict) -> str:
    market = {"passing_yards": "pass", "rushing_yards": "rush", "receiving_yards": "receive"}[leg["market"]]
    return f"{leg['player']} {int(float(leg['line']))}+ {market}"


def _miss_label(leg: dict) -> str:
    actual = leg["observed_yards"]
    return f"{_leg_label(leg)} ({actual:g})" if actual is not None else _leg_label(leg)


def run(output: Path) -> dict:
    manifest = json.loads((output / "parlay-replay-v2-manifest.json").read_text(encoding="utf-8"))
    grades = json.loads((output / "parlay-replay-v2-settlements.json").read_text(encoding="utf-8"))
    summary = json.loads((output / "parlay-replay-v2-summary.json").read_text(encoding="utf-8"))
    if (manifest["tickets_sha256"] != digest(manifest["tickets"])
            or grades["tickets_sha256"] != manifest["tickets_sha256"]
            or grades["settlements_sha256"] != digest(grades["settlements"])
            or summary["manifest_sha256"] != digest(manifest)
            or summary["settlements_sha256"] != grades["settlements_sha256"]):
        raise ValueError("parlay replay artifact integrity failure")
    by_id = {grade["ticket_id"]: grade for grade in grades["settlements"]}
    if len(by_id) != len(manifest["tickets"]):
        raise ValueError("ticket count mismatch")
    for ticket in manifest["tickets"]:
        grade = by_id[ticket["ticket_id"]]
        if grade["ticket_sha256"] != digest(ticket):
            raise ValueError("ticket settlement does not match selection")
    sections = ["# Week 1–8 reconstructed parlay replay — current-season eligibility proxy", "",
                "**Historical research constructions, not verified placed bets.** These tickets were selected after the games from archived prior-game inputs using one deterministic rule. Their selection fields exclude current-game outcomes, but the rule itself was chosen after reviewing 2026 results. They were not frozen at historical kickoff. No sportsbook combination quotes were archived, so returns and price edge are not measurable.",
                "", "Rules: fixed research lines of 200+ passing, 40+ rushing, and 40+ receiving yards; calibrated probability at least 55%; prior appearance for the same team in the 2026 season; highest-ranked distinct players; at least one leg from each team; 3, 5, or 8 legs when enough candidates exist. Week 1 produces no tickets because the data lacks a pregame 2026 roster source. A known failed leg makes a ticket lost; if no leg fails but one lacks an outcome, the ticket remains unresolved. Tickets from the same game share legs and are not independent.", "",
                "| Legs | Constructed | Won | Lost | Unresolved | Win rate on resolved |",
                "|---:|---:|---:|---:|---:|---:|"]
    for length in (3, 5, 8):
        subset = [grade for grade in grades["settlements"] if grade["leg_count"] == length]
        won = sum(grade["status"] == "won" for grade in subset)
        lost = sum(grade["status"] == "lost" for grade in subset)
        unresolved = sum(grade["status"] == "unresolved" for grade in subset)
        sections.append(f"| {length} | {len(subset)} | {won} | {lost} | {unresolved} | {won / (won + lost):.1%} |")
    total = summary["total"]
    sections.append(f"| **Total** | **{total['constructed']}** | **{total['won']}** | **{total['lost']}** | **{total['unresolved']}** | **{total['won'] / (total['won'] + total['lost']):.1%}** |")
    sections.extend(["", "## Weekly results", "",
                     "| Week | 3-leg W–L–U | 5-leg W–L–U | 8-leg W–L–U |",
                     "|---:|---:|---:|---:|"])
    for week in range(1, 9):
        cells = []
        for length in (3, 5, 8):
            row = next(item for item in summary["summary"] if item["week"] == week and item["leg_count"] == length)
            cells.append(f"{row['won']}–{row['lost']}–{row['unresolved']}")
        sections.append(f"| {week} | {' | '.join(cells)} |")
    for length in (3, 5, 8):
        sections.extend(["", f"## {length}-leg tickets", "",
                         "| Week | Game ID | Result | Exact legs selected | Failed or unresolved legs |",
                         "|---:|---|---|---|---|"])
        for ticket in manifest["tickets"]:
            if ticket["leg_count"] != length:
                continue
            grade = by_id[ticket["ticket_id"]]
            chosen = "; ".join(_leg_label(leg) for leg in ticket["legs"])
            failed = "; ".join(_miss_label(leg) for leg in grade["legs"] if leg["status"] == "lost")
            unresolved = "; ".join(_leg_label(leg) for leg in grade["legs"] if leg["status"] == "unresolved")
            note = failed or ("Unresolved: " + unresolved if unresolved else "None")
            sections.append(f"| {ticket['week']} | {ticket['game_id']} | {grade['status'].title()} | {chosen} | {note} |")
    sections.extend(["", "## Evidence and limits", "",
                     f"The selection manifest records each exact leg and its prior game IDs. Its ticket digest is `{manifest['tickets_sha256']}`. The settlement file records each leg's final yardage and ESPN game-summary URL; its digest is `{grades['settlements_sha256']}`. The report verified both hashes and every settlement-to-ticket link.",
                     "", "A final box appearance in another offensive yardage market supports grading a missing market as zero. A player absent from the entire offensive box remains unresolved. Historical pregame roster status, offered lines, combined prices, TD opportunities, and parlay joint probabilities were not available. The 55% threshold and ticket-building rule were chosen after viewing 2026 errors, so this is exploratory replay performance, not independent prospective validation or an actual wagering record.", ""])
    (output / "parlay-replay-v2-audit.md").write_text("\n".join(sections), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output)
    print(result["total"])
