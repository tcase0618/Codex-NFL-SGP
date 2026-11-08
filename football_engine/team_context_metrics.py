"""Read-only, snapshot-bound descriptive opponent cohorts for existing team IDs."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

REVISION = "team-context-v1"
SUPPORTED = {
    "offense.opponent_adjusted_offensive_epa": "offense",
    "matchup.opponent_adjusted_offensive_epa": "offense",
    "defense.opponent_adjusted_defensive_epa": "defense",
    "matchup.opponent_adjusted_defensive_epa": "defense",
    "matchup.defense_vs_deep_passing": "deep",
}
VIEWS = ("season", "last3", "last5", "last8", "home", "away", "first_half",
         "second_half", "leading", "trailing", "tied", "short_rest", "long_rest")


def utc(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("as_of must contain a timezone")
    return parsed.astimezone(timezone.utc)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def competitive(play):
    return (play.get("play_type") in ("pass", "run")
            and not any(play.get(k) == 1 for k in ("two_point_attempt", "qb_kneel", "qb_spike")))


def deep_pass(play):
    return (competitive(play) and play.get("pass_attempt") == 1
            and play.get("sack") != 1 and play.get("qb_scramble") != 1
            and finite(play.get("air_yards")) and play["air_yards"] >= 20)


def select_games(games, team, view):
    chosen = sorted((g for g in games if team in (g["home"], g["away"])),
                    key=lambda g: (g["game_date"], g["id"]))
    if view.startswith("last"):
        return chosen[-int(view[4:]):]
    if view in ("home", "away"):
        return [g for g in chosen if g[view] == team]
    if view in ("short_rest", "long_rest"):
        selected = []
        for game in chosen:
            rest = game["data"].get("home_rest" if game["home"] == team else "away_rest")
            try:
                rest = float(rest)
            except (TypeError, ValueError):
                continue
            # Week-one schedule defaults do not prove actual rest.
            if game.get("week", 1) <= 1 or not math.isfinite(rest):
                continue
            if rest < 7 if view == "short_rest" else rest > 7:
                selected.append(game)
        return selected
    return chosen


def state_matches(play, view, side):
    if view == "first_half":
        return play.get("qtr") in (1, 2)
    if view == "second_half":
        return play.get("qtr") in (3, 4)
    if view in ("leading", "trailing", "tied"):
        score = play.get("score_differential")
        if not finite(score):
            return False
        score *= -1 if side == "defense" else 1
        return score > 0 if view == "leading" else score < 0 if view == "trailing" else score == 0
    return True


def compute_cohorts(games, plays, team, view="season"):
    """Play-weighted adjustment with opponent baselines excluding this focal team.

    Baselines use same-season completed games before the snapshot cutoff, not
    pregame snapshots. All baseline plays exclude games involving the focal team.
    Offense subtracts opponent EPA allowed; defense subtracts opponent EPA made;
    both add the independent league baseline. No shrinkage or calibrated forecast.
    """
    if view not in VIEWS:
        return {}
    chosen = select_games(games, team, view)
    ids = {g["id"] for g in chosen}
    independent_games = {g["id"] for g in games if team not in (g["home"], g["away"])}
    pools = {"offense": defaultdict(list), "defense": defaultdict(list)}
    league = []
    for p in plays:
        if p["game_id"] not in independent_games or not competitive(p) or not finite(p.get("epa")):
            continue
        league.append(p)
        pools["offense"][p["defteam"]].append(p)
        pools["defense"][p["posteam"]].append(p)
    league_mean = sum(p["epa"] for p in league) / len(league) if league else None
    results = {}
    for side in ("offense", "defense", "deep"):
        key = "posteam" if side == "offense" else "defteam"
        actual_side = "offense" if side == "offense" else "defense"
        cohort = [p for p in plays if p["game_id"] in ids and p[key] == team
                  and competitive(p) and state_matches(p, view, actual_side)]
        if side == "deep":
            # Unknown depth is excluded, never classified as short or deep.
            cohort = [p for p in cohort if deep_pass(p)]
        observed = [p for p in cohort if finite(p.get("epa"))]
        source_ids = {p["source_id"] for p in cohort}
        source_ids.update(g["source_id"] for g in chosen)
        counts = {}
        missing = len(cohort) - len(observed)
        value = None
        reason = None
        if not cohort:
            reason = "NO_APPLICABLE_COHORT"
        elif not observed:
            reason = "EPA_MISSING"
        elif missing:
            reason = "INCOMPLETE_EPA_COHORT"
        elif side == "deep":
            value = sum(p["epa"] for p in observed) / len(observed)
        else:
            opponents = {p["defteam" if side == "offense" else "posteam"] for p in observed}
            baseline = {}
            for opponent in sorted(opponents):
                rows = pools[side].get(opponent, [])
                counts[opponent] = {"plays": len(rows), "games": len({p["game_id"] for p in rows})}
                if not rows:
                    reason = "OPPONENT_BASELINE_MISSING"
                    continue
                baseline[opponent] = sum(p["epa"] for p in rows) / len(rows)
            if league_mean is None:
                reason = "LEAGUE_BASELINE_MISSING"
            if reason is None:
                value = sum(p["epa"] - baseline[p["defteam" if side == "offense" else "posteam"]]
                            + league_mean for p in observed) / len(observed)
            # League centering depends on every independent league observation.
            source_ids.update(p["source_id"] for p in league)
            source_ids.update(g["source_id"] for g in games if g["id"] in independent_games)
        game_ids = sorted({p["game_id"] for p in observed})
        qualified = value is not None and len(observed) >= 100 and len(game_ids) >= 3
        if counts and any(c["plays"] < 100 or c["games"] < 3 for c in counts.values()):
            qualified = False
        results[side] = {
            "value": value,
            "status": ("DESCRIPTIVE_OPPONENT_ADJUSTED" if side != "deep" else "DESCRIPTIVE")
                      if value is not None else "UNAVAILABLE",
            "missing": value is None, "missing_reason": reason, "reason": reason,
            "sample_plays": len(observed), "eligible_plays": len(cohort), "missing_epa_plays": missing,
            "sample_games": len(game_ids), "game_ids": game_ids,
            "source_ids": sorted(source_ids), "qualified": qualified,
            "baseline_counts": counts, "league_baseline_plays": len(league) if side != "deep" else 0,
            "league_baseline_epa": league_mean if side != "deep" else None,
            "unit": "EPA/play", "direction": "higher" if side == "offense" else "lower",
            "formula": "mean(EPA | competitive pass attempt, air_yards >= 20)" if side == "deep"
                       else "mean(play EPA - opponent mean EPA excluding all focal-team games + independent league mean EPA)",
            "grain": "team/season/view/metric; play-weighted",
            "baseline_policy": "same-season before cutoff; excludes ALL focal-team games; retrospective, not pregame",
            "minimum_plays": 100, "minimum_games": 3,
            "sample_policy": "research screening only; not empirically calibrated",
            "models_calibrated": False, "historical_pregame_eligible": False,
            "percentile": None, "confidence_interval": None,
        }
    return results


class TeamContextMetrics:
    """Read overlay; unsupported metric IDs/views return None for parent fallback."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        with closing(self.connect()) as db:
            self.metadata = {r[0]: json.loads(r[1]) for r in db.execute("SELECT key,value FROM metadata")}
            self.sources = [json.loads(r[0]) for r in db.execute("SELECT data FROM sources ORDER BY id")]

    def connect(self):
        return sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)

    def verify_base(self, path):
        """Optional parent startup check; never hash the multi-GB base per query."""
        h = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        if h.hexdigest() != self.metadata["input_sha256"]:
            raise ValueError("Context overlay input database hash does not match base")
        return True

    def value(self, team, season, metric_id, view="season", as_of=None):
        if metric_id not in SUPPORTED or view not in VIEWS:
            return None
        with closing(self.connect()) as db:
            row = db.execute("SELECT data FROM metrics WHERE team=? AND season=? AND view=? AND metric_id=?",
                             (team, season, view, metric_id)).fetchone()
        if row is None:
            return None
        result = json.loads(row[0])
        if as_of is not None and utc(as_of) < utc(self.metadata["as_of"]):
            result.update(value=None, status="UNAVAILABLE", missing=True, qualified=False,
                          reason="QUERY_PRECEDES_SNAPSHOT_CUTOFF", missing_reason="QUERY_PRECEDES_SNAPSHOT_CUTOFF",
                          sample_plays=0, sample_games=0, game_ids=[], source_ids=[], baseline_counts={},
                          eligible_plays=0, missing_epa_plays=0, league_baseline_plays=0, league_baseline_epa=None)
        result["requested_as_of"] = as_of
        return result

    def close(self):
        """Compatibility no-op: there is no persistent connection."""

    def personnel(self, team, season, as_of=None):
        """Supplemental current-capture counts, separate from the 20 scalar slots.

        Roster-listed is not healthy, active, game-day eligible, or a projection.
        Depth rank 1 is a chart listing, not a confirmed available starter.
        """
        with closing(self.connect()) as db:
            rows = [json.loads(r[0]) for r in db.execute(
                "SELECT data FROM current_personnel WHERE team=?", (team,))]
        if not rows or season not in {utc(r["observed_at"]).year for r in rows}:
            return None
        if as_of is not None and utc(as_of) < utc(self.metadata["as_of"]):
            return {"status": "UNAVAILABLE", "reason": "QUERY_PRECEDES_SNAPSHOT_CUTOFF",
                    "team": team, "season": season, "source_ids": [], "rows": [],
                    "roster_listed_count": None, "depth_listed_count": None, "depth_row_count": None,
                    "advanced_personnel_populated": 0, "advanced_personnel_defined": 20}
        roster = [r for r in rows if r["kind"] == "roster"]
        depth = [r for r in rows if r["kind"] == "depth"]
        roster_names = {r["data"]["player"] for r in roster if r["data"].get("player")}
        depth_names = {r["data"]["player"] for r in depth if r["data"].get("player")}
        positions = defaultdict(set)
        for row in roster:
            if row["data"].get("position") and row["data"].get("player"):
                positions[row["data"]["position"]].add(row["data"]["player"])
        return {"team": team, "season": season, "status": "CURRENT_CAPTURE_CONTEXT_ONLY",
                "snapshot_as_of": self.metadata["as_of"], "requested_as_of": as_of,
                "observed_at_min": min(r["observed_at"] for r in rows),
                "observed_at_max": max(r["observed_at"] for r in rows),
                "roster_listed_count": len(roster_names) if roster else None,
                "depth_listed_count": len(depth_names) if depth else None,
                "depth_row_count": len(depth) if depth else None,
                "depth_rank_one_listed_count": len({r["data"]["player"] for r in depth
                                                    if r["data"].get("depth") == 1}) if depth else None,
                "roster_listed_by_position": {k: len(v) for k, v in sorted(positions.items())},
                "source_ids": sorted({r["source_id"] for r in rows}), "rows": rows,
                "count_identity_policy": "distinct exact source-listed names within team; not cross-source player-ID resolution",
                "advanced_personnel_populated": 0, "advanced_personnel_defined": 20,
                "availability_inferred": False, "historical_continuity_inferred": False,
                "historical_pregame_eligible": False,
                "warning": "Current roster/depth listings include source sections as reported; counts do not establish healthy, active, available, or projected starters."}
