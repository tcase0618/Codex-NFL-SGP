"""Complete historical research constructions; no wager approval or price edge.

All selection uses lagged boxes. Current-game player rows enter settlement only.
Week 1 roster membership remains unverified; no final roster is used to repair it.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .parlay_replay import canonical, digest, _save_once
from .replay_2026 import BASE, parse_box

VERSION = "all-games-research-v1"
LINES = {"passing_yards": 150, "rushing_yards": 20, "receiving_yards": 25}


def cached_json(url: str, cache: Path) -> dict:
    return json.loads((cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")).read_bytes())


def load_games(cache: Path) -> list[dict]:
    games = []
    for season, weeks in ((2025, range(15, 19)), (2026, range(1, 9))):
        for week in weeks:
            data = cached_json(f"{BASE}/scoreboard?dates={season}&seasontype=2&week={week}&limit=100", cache)
            for event in data["events"]:
                game = {"id": event["id"], "season": season, "week": week, "kickoff": event["date"],
                        "name": event["name"],
                        "scheduled_teams": [item["team"]["abbreviation"] for item in event["competitions"][0]["competitors"]]}
                games.append(game)
    return sorted(games, key=lambda game: (game["kickoff"], game["id"]))


def box_with_td(game: dict, cache: Path) -> dict:
    raw = cached_json(f"{BASE}/summary?event={game['id']}", cache)
    box = parse_box(game, raw)
    for team in raw["boxscore"]["players"]:
        abbreviation = team["team"]["abbreviation"]
        for section in team["statistics"]:
            if section.get("name") not in ("rushing", "receiving") or "TD" not in section["labels"]:
                continue
            index = section["labels"].index("TD")
            for item in section.get("athletes", []):
                player_id = str(item["athlete"]["id"])
                if player_id in box["teams"][abbreviation] and len(item.get("stats", [])) > index:
                    record = box["teams"][abbreviation][player_id]
                    record["td"] = record.get("td", 0) + int(item["stats"][index])
    return box


def candidates(game: dict, history: dict) -> list[dict]:
    result = []
    for team in game["scheduled_teams"]:
        prior = history[team][-4:]
        if not prior or any(item["kickoff"] >= game["kickoff"] for item in prior):
            raise ValueError("missing or non-lagged history")
        # Union of earlier appearances includes backups and players returning
        # from a missed game. It is not proof of current team membership.
        players = {}
        for earlier in prior:
            players.update(earlier["teams"][team])
        for player_id, player in players.items():
            appearances = [item["teams"][team].get(player_id) for item in prior]
            observed = [item for item in appearances if item is not None]
            common = {"player_id": player_id, "player": player["name"], "team_id": team,
                      "prior_game_ids": [item["id"] for item in prior],
                      "prior_appearances": len(observed),
                      "eligibility": "UNVERIFIED_PRIOR_TEAM_APPEARANCE_ONLY"}
            for market, line in LINES.items():
                values = [item["markets"].get(market, {"yards": 0})["yards"] for item in observed]
                # Avoid inventing an unfamiliar market for a player; count a
                # missing section as zero only after any prior market use.
                if not any(market in item["markets"] for item in observed):
                    continue
                hits = sum(value >= line for value in values)
                result.append({**common, "market": market, "line": line,
                               "ranking_score": (hits + 1) / (len(values) + 2),
                               "prior_hits": hits, "prior_market_mean": sum(values) / len(values),
                               "score_kind": "smoothed_lagged_frequency_not_calibrated"})
            # Anytime TD excludes passing TD. This baseline uses past rushing
            # and receiving TD occurrence, not a red-zone opportunity model.
            if any("rushing_yards" in item["markets"] or "receiving_yards" in item["markets"] for item in observed):
                hits = sum(item.get("td", 0) > 0 for item in observed)
                result.append({**common, "market": "anytime_td", "line": 1,
                               "ranking_score": (hits + 1) / (len(observed) + 2),
                               "prior_hits": hits,
                               "prior_market_mean": sum(item.get("td", 0) for item in observed) / len(observed),
                               "score_kind": "smoothed_lagged_TD_frequency_not_opportunity_model"})
    return result


def select(pool: list[dict], length: int) -> list[dict]:
    # Ranking is fixed before outcome lookup. Prefer greater history and market
    # production on ties, then stable player IDs. One market per player.
    ordered = sorted(pool, key=lambda leg: (-leg["ranking_score"], -leg["prior_appearances"],
                                            -leg["prior_market_mean"], leg["player_id"], leg["market"]))
    teams = sorted({leg["team_id"] for leg in ordered})
    if len(teams) != 2:
        raise ValueError("both teams required")
    chosen = [next(leg for leg in ordered if leg["team_id"] == team) for team in teams]
    used = {leg["player_id"] for leg in chosen}
    for leg in ordered:
        if len(chosen) >= length:
            break
        if leg["player_id"] not in used:
            chosen.append(leg)
            used.add(leg["player_id"])
    if len(chosen) != length:
        raise ValueError(f"insufficient historical players for {length} legs")
    return chosen


def settle(ticket: dict, box: dict) -> dict:
    legs = []
    for leg in ticket["legs"]:
        player = box["teams"][leg["team_id"]].get(leg["player_id"])
        if player is None:
            actual = None
        elif leg["market"] == "anytime_td":
            actual = player.get("td", 0)
        else:
            actual = player["markets"].get(leg["market"], {"yards": 0})["yards"]
        status = "unresolved" if actual is None else "won" if actual >= leg["line"] else "lost"
        legs.append({**leg, "observed": actual, "status": status})
    status = "lost" if any(leg["status"] == "lost" for leg in legs) else (
        "unresolved" if any(leg["status"] == "unresolved" for leg in legs) else "won")
    return {"ticket_id": ticket["ticket_id"], "ticket_sha256": digest(ticket),
            "status": status, "legs": legs,
            "source": f"{BASE}/summary?event={box['id']}", "box_sha256": digest(box)}


def run(output: Path, cache: Path) -> dict:
    games = load_games(cache)
    history = defaultdict(list)
    tickets = []
    targets = [game for game in games if game["season"] == 2026]
    # Build every selection before reading any 2026 outcomes for settlement.
    # Earlier boxes enter history solely after their game chronologically.
    for game in games:
        if game["season"] == 2026:
            pool = candidates(game, history)
            for kind, length in (("3", 3), ("5", 5), ("8", 8), ("TD", 3)):
                legs = select([leg for leg in pool if (leg["market"] == "anytime_td") == (kind == "TD")], length)
                tickets.append({"ticket_id": f"all-games-{game['id']}-{kind}", "type": kind,
                                "week": game["week"], "game_id": game["id"], "game": game["name"],
                                "kickoff": game["kickoff"], "legs": legs})
        box = box_with_td(game, cache)
        for team in box["teams"]:
            history[team].append(box)
    assert len(tickets) == len(targets) * 4
    manifest = {"version": VERSION, "kind": "HISTORICAL_RESEARCH_CONSTRUCTIONS",
                "historically_frozen": False, "tickets_sha256": digest(tickets), "tickets": tickets,
                "rules": {"yardage_lines": LINES, "TD_legs": 3, "probability_floor": None,
                          "unique_players": True, "both_teams": True, "history": "last four team games",
                          "rosters": "unverified prior team appearances; Week 1 seeded from 2025",
                          "ranking": "smoothed prior hit frequency; no model-approved joint probability"}}
    _save_once(output / "all-games-parlay-manifest.json", manifest)
    by_game = {game["id"]: game for game in targets}
    boxes = {game_id: box_with_td(game, cache) for game_id, game in by_game.items()}
    grades = [settle(ticket, boxes[ticket["game_id"]]) for ticket in tickets]
    _save_once(output / "all-games-parlay-settlements.json", {
        "tickets_sha256": manifest["tickets_sha256"], "settlements_sha256": digest(grades), "settlements": grades})
    summary = []
    for week in range(1, 9):
        for kind in ("3", "5", "8", "TD"):
            subset = [grade for ticket, grade in zip(tickets, grades) if ticket["week"] == week and ticket["type"] == kind]
            summary.append({"week": week, "type": kind, "tickets": len(subset),
                            **{status: sum(grade["status"] == status for grade in subset)
                               for status in ("won", "lost", "unresolved")}})
    report = {"games": len(targets), "tickets": len(tickets), "weekly": summary}
    (output / "all-games-parlay-summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = ["# All-game 3/5/8/TD research constructions", "",
             "All four ticket types are constructed for each of 121 games in Weeks 1–8: 484 total. These replace neither the earlier screened replay nor the actual engine approval process. They are exploratory constructions created after the games. Historical roster eligibility and offered markets are unverified, especially Week 1. Missing players remain unresolved. There are no captured combined prices, so ROI is not measurable.", "",
             "Yardage lines: 150+ passing, 20+ rushing, 25+ receiving. TD means three players each scoring a rushing or receiving touchdown; passing TD is excluded. Selection uses smoothed hit frequency from prior appearances in the last four team games, distinct players and both teams. There is no probability floor. This ranking score is not a calibrated probability; the TD ranking is a historical-frequency baseline, not the core red-zone opportunity model. The same first legs are reused across sizes, so tickets overlap.", "",
             "| Type | Tickets | Won | Lost | Unresolved |", "|---|---:|---:|---:|---:|"]
    for kind in ("3", "5", "8", "TD"):
        subset = [row for row in summary if row["type"] == kind]
        lines.append(f"| {kind} | {sum(row['tickets'] for row in subset)} | {sum(row['won'] for row in subset)} | {sum(row['lost'] for row in subset)} | {sum(row['unresolved'] for row in subset)} |")
    for week in range(1, 9):
        lines.extend(["", f"## Week {week}", "", "| Game | Type | Result | Exact legs and final observations |", "|---|---|---|---|"])
        for ticket, grade in zip(tickets, grades):
            if ticket["week"] != week:
                continue
            detail = "; ".join(f"{leg['player']} ({leg['team_id']}) {leg['market']} {leg['line']}+: {leg['observed'] if leg['observed'] is not None else 'unknown'} [{leg['status']}]" for leg in grade["legs"])
            lines.append(f"| {ticket['game']} | {ticket['type']} | {grade['status']} | {detail} |")
    lines.extend(["", "The manifest preserves exact selections, their prior game IDs and a ticket hash. The settlement file links each ticket hash to its observed outcomes, ESPN summary URL and parsed-box hash. Neither file was frozen before the historical kickoff. The rules were chosen after the season audit and need untouched future validation.", ""])
    (output / "all-games-parlay-audit.md").write_text("\n".join(lines), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.cache), indent=2))
