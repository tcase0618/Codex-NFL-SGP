"""Resolve missing legs from historical game-specific rosters.

Keep original tickets immutable. Non-participants and wrong-team selections
are classified explicitly; no sportsbook void rule is assumed.
"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .complete_parlays import cached_json
from .parlay_replay import digest, _save_once
from .replay_2026 import BASE, fetch_json


def roster_url(game_id, team_id):
    return f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{game_id}/competitions/{game_id}/competitors/{team_id}/roster?limit=100"


def classify(leg, team_roster, other_roster):
    player_id = leg["player_id"]
    entries = {str(entry["playerId"]): entry for entry in team_roster.get("entries", [])}
    other_entries = {str(entry["playerId"]): entry for entry in other_roster.get("entries", [])}
    if player_id not in entries:
        return "wrong_team" if player_id in other_entries else "not_on_game_roster"
    entry = entries[player_id]
    if entry.get("didNotPlay") is True:
        return "did_not_play"
    if entry.get("didNotPlay") is False:
        return "played_zero"
    return "participation_unknown"


def run(output: Path, cache: Path):
    original = json.loads((output / "all-games-parlay-settlements.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "all-games-parlay-manifest.json").read_text(encoding="utf-8"))
    if digest(manifest["tickets"]) != original["tickets_sha256"]:
        raise ValueError("ticket digest mismatch")
    games = {ticket["game_id"] for ticket in manifest["tickets"]}
    teams_by_game = {}
    requests = {}
    for game_id in games:
        box = cached_json(f"{BASE}/summary?event={game_id}", cache)
        teams_by_game[game_id] = {item["team"]["abbreviation"]: str(item["team"]["id"])
                                   for item in box["header"]["competitions"][0]["competitors"]}
        for abbreviation, team_id in teams_by_game[game_id].items():
            requests[(game_id, abbreviation)] = roster_url(game_id, team_id)
    rosters = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        tasks = {pool.submit(fetch_json, url, cache): key for key, url in requests.items()}
        for task in as_completed(tasks):
            key = tasks[task]
            roster = task.result()
            if not roster.get("entries") or len(roster["entries"]) < 20:
                raise ValueError(f"incomplete roster: {key}")
            rosters[key] = roster
    resolved = []
    evidence = {}
    for ticket, original_grade in zip(manifest["tickets"], original["settlements"]):
        assert ticket["ticket_id"] == original_grade["ticket_id"]
        legs = []
        for leg in original_grade["legs"]:
            item = dict(leg)
            if item["status"] == "unresolved":
                game_id = ticket["game_id"]
                own = leg["team_id"]
                other = next(team for team in teams_by_game[game_id] if team != own)
                classification = classify(leg, rosters[(game_id, own)], rosters[(game_id, other)])
                key = f"{game_id}:{own}:{leg['player_id']}"
                proof = {"classification": classification,
                         "own_roster_source": requests[(game_id, own)],
                         "other_roster_source": requests[(game_id, other)],
                         "own_roster_sha256": digest(rosters[(game_id, own)]),
                         "other_roster_sha256": digest(rosters[(game_id, other)]),
                         "own_entry": next((entry for entry in rosters[(game_id, own)]["entries"]
                                            if str(entry["playerId"]) == leg["player_id"]), None)}
                evidence[key] = proof
                item["participation_resolution"] = classification
                item["participation_evidence_key"] = key
                if classification == "played_zero":
                    item["observed"] = 0
                    item["status"] = "lost"
                else:
                    item["status"] = classification
            legs.append(item)
        statuses = {leg["status"] for leg in legs}
        # A broken selection is a construction failure even if another valid
        # leg also lost. Retain those losing legs in the evidence.
        if statuses & {"wrong_team", "not_on_game_roster"}:
            status = "invalid_selection"
        elif "participation_unknown" in statuses:
            status = "participation_unknown"
        elif "did_not_play" in statuses:
            status = "nonparticipant_ticket"
        elif "lost" in statuses:
            status = "lost"
        else:
            status = "won"
        resolved.append({**original_grade, "status": status, "legs": legs,
                         "original_status": original_grade["status"]})
    artifact = {"kind": "historical_participation_resolution",
                "original_settlements_sha256": original["settlements_sha256"],
                "tickets_sha256": original["tickets_sha256"], "resolutions_sha256": digest(resolved),
                "settlements": resolved, "evidence": evidence}
    _save_once(output / "all-games-participation-resolved.json", artifact)
    lines = ["# Participation resolution for all-game parlays", "",
             "Game-specific ESPN roster records were checked for both teams in every game. The original ticket manifest and settlements remain unchanged. A missing player with didNotPlay=false is a participant with zero in the absent offensive market. Explicit didNotPlay=true is non-participation. A player absent from the selected team's game roster is an invalid historical selection. No sportsbook void or reduced-leg ticket settlement is inferred.", "",
             "| Type | Won | Lost | Invalid selection | Nonparticipant ticket | Still unknown |", "|---|---:|---:|---:|---:|---:|"]
    summary = {}
    for kind in ("3", "5", "8", "TD"):
        grades = [grade for ticket, grade in zip(manifest["tickets"], resolved) if ticket["type"] == kind]
        counts = {status: sum(grade["status"] == status for grade in grades)
                  for status in ("won", "lost", "invalid_selection", "nonparticipant_ticket", "participation_unknown")}
        summary[kind] = counts
        lines.append(f"| {kind} | {' | '.join(str(counts[key]) for key in counts)} |")
    previous_missing = [grade for grade in resolved if grade["original_status"] == "unresolved"]
    lines.extend(["", f"All {len(previous_missing)} originally unresolved tickets are classified: "
                  f"{sum(grade['status'] == 'lost' for grade in previous_missing)} losses, "
                  f"{sum(grade['status'] == 'invalid_selection' for grade in previous_missing)} invalid selections, "
                  f"{sum(grade['status'] == 'nonparticipant_ticket' for grade in previous_missing)} nonparticipant tickets. "
                  "Tickets previously called lost are also reclassified when a different leg has a roster defect. "
                  "The corrected categories are mutually exclusive, with invalid selection taking precedence, "
                  "then non-participation, then known lost/won. No new winning ticket was produced by resolving missing data."])
    lines.extend(["", "## Every previously missing player-game", "",
                  "| Game ID | Player | Selected team | Resolution | Roster source |", "|---|---|---|---|---|"])
    names = {f"{ticket['game_id']}:{leg['team_id']}:{leg['player_id']}": leg["player"]
             for ticket in manifest["tickets"] for leg in ticket["legs"]}
    for key, proof in sorted(evidence.items()):
        game_id, team, _ = key.split(":")
        lines.append(f"| {game_id} | {names[key]} | {team} | {proof['classification']} | [ESPN game roster]({proof['own_roster_source']}) |")
    lines.extend(["", "Invalid selections and nonparticipant tickets are defects in the reconstructed candidate pool. They must not be removed to advertise a higher win rate. Replaying a different roster filter requires new tickets chosen from evidence available before kickoff. These retrospective roster lookups are settlement evidence only.", ""])
    (output / "all-games-participation-resolution.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"ticket_counts": summary,
                      "unique_player_games": len(evidence),
                      "leg_resolutions": {status: sum(proof["classification"] == status for proof in evidence.values())
                                          for status in ("played_zero", "did_not_play", "wrong_team", "not_on_game_roster", "participation_unknown")}}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    run(args.output, args.cache)
