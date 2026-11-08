"""Research-only chronological replay from prior ESPN final box scores.

This is deliberately separate from the prospective ledger: historical feed
retrieval cannot establish that a pregame quote or roster snapshot was observed.
"""

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from urllib.request import Request, urlopen

from football_engine.engine import _poisson_tail


BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
MARKETS = {
    "passing": ("passing_yards", "C/ATT", 200),
    "rushing": ("rushing_yards", "CAR", 40),
    "receiving": ("receiving_yards", "TGTS", 40),
}
SCRIPTS = ((0.75, 0.25), (1.0, 0.5), (1.25, 0.25))


def fetch_json(url: str, cache: Path) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    if not target.exists():
        with urlopen(Request(url, headers={"User-Agent": "CaseCapitalFootballBetting-research/0.1"}), timeout=25) as response:
            raw = response.read()
        target.write_bytes(raw)
    return json.loads(target.read_bytes())


def schedule(season: int, week: int, cache: Path) -> list[dict]:
    url = f"{BASE}/scoreboard?dates={season}&seasontype=2&week={week}&limit=100"
    events = fetch_json(url, cache).get("events", [])
    return [{"season": season, "week": week, "id": event["id"], "kickoff": event["date"]}
            for event in events if event["status"]["type"]["name"] == "STATUS_FINAL"]


def parse_box(game: dict, box: dict) -> dict:
    competition = box["header"]["competitions"][0]
    if competition["status"]["type"]["name"] != "STATUS_FINAL":
        raise ValueError(f"non-final box: {game['id']}")
    teams = {}
    for team in box["boxscore"]["players"]:
        abbreviation = team["team"]["abbreviation"]
        players = {}
        for section in team.get("statistics", []):
            if section.get("name") not in MARKETS:
                continue
            market, volume_label, _ = MARKETS[section["name"]]
            labels = section["labels"]
            if "YDS" not in labels or volume_label not in labels:
                continue
            yi, vi = labels.index("YDS"), labels.index(volume_label)
            for entry in section.get("athletes", []):
                player = entry.get("athlete", {})
                player_id = str(player.get("id", ""))
                stats = entry.get("stats", [])
                if not player_id or len(stats) <= max(yi, vi):
                    continue
                try:
                    volume = float(stats[vi].split("/")[-1].replace(",", ""))
                    yards = float(stats[yi].replace(",", ""))
                except ValueError:
                    continue
                players.setdefault(player_id, {"name": player.get("displayName", player_id), "markets": {}})
                players[player_id]["markets"][market] = {"volume": volume, "yards": yards}
        teams[abbreviation] = players
    if len(teams) != 2:
        raise ValueError(f"incomplete box: {game['id']}")
    return {**game, "teams": teams}


def forecasts(game: dict, history: dict[str, list[dict]]) -> list[dict]:
    rows = []
    for team_id, final_players in game["teams"].items():
        prior_games = history[team_id][-4:]
        if len(prior_games) < 2:
            continue
        latest_players = prior_games[-1]["teams"][team_id]
        for player_id, prior_player in latest_players.items():
            for market, threshold in (("passing_yards", 200), ("rushing_yards", 40), ("receiving_yards", 40)):
                prior = [prior_game["teams"][team_id].get(player_id, {}).get("markets", {}).get(market)
                         for prior_game in prior_games]
                observed = [item for item in prior if item is not None]
                if len(observed) < 2:
                    continue
                mean_volume = sum(item["volume"] for item in observed) / len(prior_games)
                total_volume = sum(item["volume"] for item in observed)
                if mean_volume < 1 or total_volume < 1:
                    continue
                efficiency = sum(item["yards"] for item in observed) / total_volume
                if efficiency <= 0:
                    continue
                needed = ceil(threshold / efficiency)
                probability = sum(weight * _poisson_tail(mean_volume * multiplier, needed)
                                  for multiplier, weight in SCRIPTS)
                final_player = final_players.get(player_id)
                actual = None
                if final_player is not None:
                    # An offensive box appearance proves game participation;
                    # absence from a second yardage section is zero use there.
                    actual = final_player["markets"].get(market, {"yards": 0, "volume": 0})["yards"]
                outcome = None if actual is None else int(actual >= threshold)
                # A player absent from the entire offensive box remains
                # unresolved: the feed cannot distinguish inactive from zero use.
                rows.append({"season": game["season"], "week": game["week"], "game_id": game["id"],
                             "kickoff": game["kickoff"], "team_id": team_id, "player_id": player_id,
                             "player": prior_player["name"], "market": market, "line": threshold,
                             "probability": round(probability, 8), "outcome": outcome,
                             "actual_yards": actual,
                             "prior_game_ids": ";".join(item["id"] for item in prior_games),
                             "prior_observations": len(observed), "mean_volume": round(mean_volume, 3),
                             "yards_per_opportunity": round(efficiency, 3)})
    return rows


def run(output: Path, cache: Path) -> dict:
    games = [item for week in range(15, 19) for item in schedule(2025, week, cache)]
    games += [item for week in range(1, 9) for item in schedule(2026, week, cache)]
    boxes = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        future_games = {pool.submit(fetch_json, f"{BASE}/summary?event={item['id']}", cache): item for item in games}
        for future in as_completed(future_games):
            item = future_games[future]
            boxes[item["id"]] = parse_box(item, future.result())
    history = defaultdict(list)
    rows = []
    for game in sorted(games, key=lambda item: item["kickoff"]):
        box = boxes[game["id"]]
        if game["season"] == 2026:
            rows.extend(forecasts(box, history))
        for team in box["teams"]:
            history[team].append(box)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "replay-2026-predictions.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    weekly = []
    for week in range(1, 9):
        subset = [row for row in rows if row["week"] == week]
        resolved = [row for row in subset if row["outcome"] is not None]
        weekly.append({"week": week, "games": len({row["game_id"] for row in subset}),
                       "forecasts": len(subset), "resolved": len(resolved),
                       "unresolved": len(subset) - len(resolved),
                       "hits": sum(row["outcome"] for row in resolved),
                       "hit_rate": round(sum(row["outcome"] for row in resolved) / len(resolved), 4) if resolved else None,
                       "brier": round(sum((row["probability"] - row["outcome"]) ** 2 for row in resolved) / len(resolved), 4) if resolved else None})
    report = {"season": 2026, "prior_season_seed_weeks": [15, 16, 17, 18],
              "scored_weeks": [1, 2, 3, 4, 5, 6, 7, 8], "source": BASE,
              "retrieved_at": datetime.now(timezone.utc).isoformat(), "weekly": weekly,
              "notes": ["Research-only reconstructed lagged box-score inputs; no archived pregame snapshot or roster proof.",
                        "Fixed illustrative thresholds, not verified offered markets or combined quotes.",
                        "Players absent from the entire offensive final box are unresolved; an offensive participant missing a market stat is graded at zero.",
                        "TD and multi-leg correlation excluded because red-zone opportunity and joint simulation inputs are unavailable.",
                        "No actual wager, price edge, ROI, or production eligibility is measured."]}
    (output / "replay-2026-summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.cache), indent=2))
