"""Post-selection participation diagnosis; never changes a frozen ticket."""
import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .complete_parlays import cached_json, BASE
from .parlay_replay import digest, _save_once
from .replay_2026 import fetch_json
from .resolve_participation import classify, roster_url


def run(output):
    manifest = json.loads((output / "selection-manifest.json").read_text(encoding="utf-8"))
    original = json.loads((output / "statistical-outcomes.json").read_text(encoding="utf-8"))
    assert digest(manifest["tickets"]) == original["tickets_sha256"]
    tickets = {ticket["ticket_id"]: ticket for ticket in manifest["tickets"]}
    affected = {tickets[row["ticket_id"]]["game_id"] for row in original["grades"]
                if any(leg["status"] == "unresolved" for leg in row["legs"])}
    cache = output / "participation-sources"
    requests, teams = {}, {}
    for game_id in affected:
        box = cached_json(f"{BASE}/summary?event={game_id}", output / "final-boxes")
        teams[game_id] = {item["team"]["abbreviation"]: item["team"]["id"]
                          for item in box["header"]["competitions"][0]["competitors"]}
        for team, team_id in teams[game_id].items():
            requests[(game_id, team)] = roster_url(game_id, team_id)
    def fetch(item):
        key, url = item
        try:
            data = fetch_json(url, cache)
            if len(data.get("entries", [])) < 20:
                raise ValueError("incomplete game roster")
            return key, data
        except Exception:
            return key, {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        rosters = dict(executor.map(fetch, requests.items()))
    rows, proofs = [], {}
    for row in original["grades"]:
        game_id = tickets[row["ticket_id"]]["game_id"]
        legs = []
        for leg in row["legs"]:
            leg = dict(leg)
            if leg["status"] == "unresolved":
                own = leg["team_id"]
                other = next(team for team in teams[game_id] if team != own)
                own_data, other_data = rosters[(game_id, own)], rosters[(game_id, other)]
                status = classify(leg, own_data, other_data) if own_data and other_data else "participation_unknown"
                key = f"{game_id}:{own}:{leg['player_id']}"
                proofs[key] = {"classification": status, "player": leg["player"],
                               "source": requests[(game_id, own)], "own_roster_sha256": digest(own_data),
                               "other_roster_sha256": digest(other_data)}
                leg["participation_evidence_key"] = key
                if status == "played_zero":
                    leg.update(status="lost", observed=0)
                else:
                    leg["status"] = status
            legs.append(leg)
        statuses = {leg["status"] for leg in legs}
        if statuses & {"wrong_team", "not_on_game_roster"}:
            status = "invalid_selection"
        elif "did_not_play" in statuses:
            status = "nonparticipant_ticket"
        elif "participation_unknown" in statuses:
            status = "participation_unknown"
        elif "lost" in statuses:
            status = "lost"
        elif statuses == {"won"}:
            status = "won"
        else:
            status = row["status"]
        rows.append({**row, "original_status": row["status"], "status": status, "legs": legs})
    summary = {"valid_pregame_backtest": False, "roi": None,
               "counts": dict(Counter(row["status"] for row in rows)),
               "by_type": {kind: dict(Counter(row["status"] for row in rows if row["type"] == kind)) for kind in ("3", "5", "8", "TD")},
               "unique_missing_player_games": dict(Counter(row["classification"] for row in proofs.values()))}
    _save_once(output / "participation-diagnosis.json", {"tickets_sha256": original["tickets_sha256"],
                                                        "summary": summary, "evidence": proofs, "grades": rows})
    lines = ["# Settlement-only participation diagnosis", "",
             "These checks occur after selections were frozen. No player was removed, replaced or reranked. This does not repair historical pregame source-version uncertainty. Nonparticipant tickets and invalid selections are separate from statistical wins/losses; sportsbook void rules and ROI remain unknown.", "",
             "| Family | Hits | Misses | Nonparticipant | Invalid selection | Unknown |", "|---|---:|---:|---:|---:|---:|"]
    for kind, counts in summary["by_type"].items():
        lines.append(f"| {kind} | {counts.get('won',0)} | {counts.get('lost',0)} | {counts.get('nonparticipant_ticket',0)} | {counts.get('invalid_selection',0)} | {counts.get('participation_unknown',0)} |")
    lines += ["", "The report.md file retains every original statistical result and failed leg. participation-diagnosis.json contains revised categories and per-leg evidence. A nonparticipant classification takes precedence even when a different leg failed. Do not drop these tickets to advertise a better hit rate.", ""]
    (output / "participation-diagnosis.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().output), indent=2))
