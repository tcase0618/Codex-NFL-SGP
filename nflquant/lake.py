"""Normalize raw nflverse assets into the analytical lake (Parquet).

Outputs (``data/lake``):
    plays/{season}.parquet   one row per play, pbp + participation + FTN charting joined
    games.parquet            schedules with derived fields (rest, travel, roof, weather)
    player_games.parquet     player weekly box stats joined to snap counts
    injuries.parquet, depth_charts.parquet, rosters.parquet, players.parquet

Leakage guard: columns that encode the *final* game outcome (result, total,
home_score, away_score, *_wp_post, drive_* summary fields) are retained only for
labeling/evaluation and are never selected by ``features``.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .config import settings

# Historical franchise codes -> current keys (original codes are retained in *_orig columns).
FRANCHISE = {"SD": "LAC", "OAK": "LV", "STL": "LA", "LAR": "LA", "WSH": "WAS", "JAC": "JAX"}

PBP_COLUMNS = [
    "play_id", "game_id", "season", "season_type", "week", "game_date", "home_team", "away_team",
    "posteam", "defteam", "posteam_type", "yardline_100", "qtr", "down", "ydstogo", "goal_to_go",
    "quarter_seconds_remaining", "half_seconds_remaining", "game_seconds_remaining", "drive", "fixed_drive",
    "fixed_drive_result", "drive_start_transition", "drive_start_yard_line", "sp", "play_type", "yards_gained",
    "shotgun", "no_huddle", "qb_dropback", "qb_kneel", "qb_spike", "qb_scramble", "pass_length", "pass_location",
    "air_yards", "yards_after_catch", "run_location", "run_gap", "field_goal_result", "kick_distance",
    "extra_point_result", "two_point_conv_result", "timeout", "timeout_team", "posteam_timeouts_remaining",
    "defteam_timeouts_remaining", "posteam_score", "defteam_score", "score_differential", "ep", "epa", "wp",
    "vegas_wp", "cp", "cpoe", "xpass", "pass_oe", "xyac_mean_yardage", "success", "first_down", "first_down_penalty",
    "third_down_converted", "third_down_failed", "fourth_down_converted", "fourth_down_failed", "incomplete_pass",
    "complete_pass", "interception", "sack", "qb_hit", "touchdown", "pass_touchdown", "rush_touchdown",
    "return_touchdown", "td_team", "td_player_id", "fumble", "fumble_lost", "fumble_forced", "safety", "penalty",
    "penalty_team", "penalty_player_id", "penalty_yards", "penalty_type", "tackled_for_loss", "pass_attempt", "rush_attempt",
    "field_goal_attempt", "punt_attempt", "kickoff_attempt", "extra_point_attempt", "two_point_attempt",
    "touchback", "punt_inside_twenty", "return_yards", "return_team", "punt_blocked", "pass", "rush", "special", "play",
    "passer_player_id", "receiver_player_id", "rusher_player_id", "passer_id", "rusher_id", "receiver_id", "passing_yards", "receiving_yards",
    "rushing_yards", "interception_player_id", "sack_player_id", "half_sack_1_player_id", "half_sack_2_player_id",
    "qb_hit_1_player_id", "qb_hit_2_player_id", "tackle_for_loss_1_player_id", "tackle_for_loss_2_player_id",
    "solo_tackle_1_player_id", "solo_tackle_2_player_id", "assist_tackle_1_player_id", "assist_tackle_2_player_id",
    "tackle_with_assist_1_player_id", "tackle_with_assist_2_player_id", "pass_defense_1_player_id",
    "pass_defense_2_player_id", "forced_fumble_player_1_player_id", "fumbled_1_player_id", "kicker_player_id",
    "punter_player_id", "kickoff_returner_player_id", "punt_returner_player_id", "roof", "surface", "temp",
    "wind", "div_game", "home_coach", "away_coach", "stadium_id", "series", "series_success",
]
PART_COLUMNS = [
    "offense_formation", "offense_personnel", "defense_personnel", "defenders_in_box", "number_of_pass_rushers",
    "offense_players", "defense_players", "time_to_throw", "was_pressure", "route", "defense_man_zone_type",
    "defense_coverage_type",
]
FTN_COLUMNS = [
    "is_play_action", "is_rpo", "is_screen_pass", "is_motion", "is_qb_out_of_pocket", "is_interception_worthy",
    "is_throw_away", "read_thrown", "is_catchable_ball", "is_contested_ball", "is_created_reception", "is_drop",
    "is_qb_sneak", "n_blitzers", "n_pass_rushers", "n_defense_box", "is_qb_fault_sack", "is_trick_play",
]


def franchise(code):
    return FRANCHISE.get(code, code) if isinstance(code, str) else code


def _read(path: Path, columns=None) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    return df


def build_plays(season: int, raw: Path) -> pd.DataFrame | None:
    pbp = _read(raw / "pbp" / f"play_by_play_{season}.parquet")
    if pbp is None:
        return None
    pbp = pbp[[c for c in PBP_COLUMNS if c in pbp.columns]].copy()
    for c in PBP_COLUMNS:
        if c not in pbp.columns:
            pbp[c] = np.nan
    for c in ("posteam", "defteam", "home_team", "away_team", "td_team", "penalty_team", "timeout_team", "return_team"):
        pbp[c + "_orig"] = pbp[c] if c in ("posteam", "defteam", "home_team", "away_team") else None
        pbp[c] = pbp[c].map(franchise)
    pbp = pbp.drop(columns=[c for c in pbp.columns if c.endswith("_orig") and c.split("_orig")[0] not in
                            ("posteam", "defteam", "home_team", "away_team")])
    part = _read(raw / "pbp_participation" / f"pbp_participation_{season}.parquet")
    if part is not None:
        part = part.rename(columns={"nflverse_game_id": "game_id"})
        part = part[["game_id", "play_id"] + [c for c in PART_COLUMNS if c in part.columns]]
        part["play_id"] = part["play_id"].astype(float)
        pbp = pbp.merge(part.drop_duplicates(["game_id", "play_id"]), on=["game_id", "play_id"], how="left")
        pbp["has_participation"] = pbp["offense_players"].notna()
    else:
        for c in PART_COLUMNS:
            pbp[c] = np.nan
        pbp["has_participation"] = False
    ftn = _read(raw / "ftn_charting" / f"ftn_charting_{season}.parquet")
    if ftn is not None:
        ftn = ftn.rename(columns={"nflverse_game_id": "game_id", "nflverse_play_id": "play_id"})
        ftn = ftn[["game_id", "play_id"] + [c for c in FTN_COLUMNS if c in ftn.columns]]
        ftn["play_id"] = ftn["play_id"].astype(float)
        pbp = pbp.merge(ftn.drop_duplicates(["game_id", "play_id"]), on=["game_id", "play_id"], how="left")
        pbp["has_ftn"] = pbp["is_play_action"].notna()
    else:
        for c in FTN_COLUMNS:
            pbp[c] = np.nan
        pbp["has_ftn"] = False
    pbp["play_id"] = pbp["play_id"].astype(float)
    # Normalized booleans (NaN stays NaN = unknown, never coerced to False)
    for c in ("was_pressure",) + tuple(c for c in FTN_COLUMNS if c.startswith("is_")):
        pbp[c] = pbp[c].map(lambda v: np.nan if v is None or (isinstance(v, float) and math.isnan(v)) else float(bool(v)))
    pbp["man_coverage"] = pbp["defense_man_zone_type"].map({"MAN_COVERAGE": 1.0, "ZONE_COVERAGE": 0.0})
    pbp["offense_personnel_code"] = pbp["offense_personnel"].map(personnel_code)
    pbp["defense_package"] = pbp["defense_personnel"].map(defense_package)
    pbp["blitz"] = np.where(pbp["n_blitzers"].notna(), (pbp["n_blitzers"] > 0).astype(float), np.nan)
    box = pbp["defenders_in_box"].where(pbp["defenders_in_box"] > 0, pbp["n_defense_box"].where(pbp["n_defense_box"].astype(float) > 0))
    pbp["box_count"] = pd.to_numeric(box, errors="coerce")
    return pbp


def personnel_code(text) -> str | None:
    """'1 RB, 1 TE, 3 WR' -> '11'. Returns None for special-teams or unknown groupings."""
    if not isinstance(text, str) or not text:
        return None
    counts = {}
    for part in text.split(","):
        bits = part.strip().split(" ")
        if len(bits) == 2 and bits[0].isdigit():
            counts[bits[1]] = counts.get(bits[1], 0) + int(bits[0])
    if any(k in counts for k in ("K", "P", "LS")):
        return None
    rb = counts.get("RB", 0) + counts.get("FB", 0)
    te = counts.get("TE", 0)
    if rb + te + counts.get("WR", 0) == 0:
        return None
    return f"{rb}{te}"


def defense_package(text) -> str | None:
    """Defensive package from DB count: base (<=4 DB), nickel (5), dime (6+)."""
    if not isinstance(text, str) or not text:
        return None
    db = 0
    for part in text.split(","):
        bits = part.strip().split(" ")
        if len(bits) == 2 and bits[0].isdigit() and bits[1] in ("CB", "FS", "SS", "S", "DB"):
            db += int(bits[0])
    if db == 0:
        return None
    return "base" if db <= 4 else ("nickel" if db == 5 else "dime")


def haversine(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h))


def build_games(raw: Path) -> pd.DataFrame:
    g = pd.read_csv(raw / "schedules" / "games.csv", low_memory=False)
    g = g[g["season"] >= 2006].copy()
    for c in ("home_team", "away_team"):
        g[c + "_orig"] = g[c]
        g[c] = g[c].map(franchise)
    g["kickoff"] = pd.to_datetime(g["gameday"] + " " + g["gametime"].fillna("13:00"), errors="coerce")
    g["completed"] = g["result"].notna()
    g["is_playoff"] = g["game_type"] != "REG"
    g["dome"] = g["roof"].isin(["dome", "closed"]).astype(float)
    g["neutral_site"] = (g["location"] == "Neutral").astype(float)
    g["away_travel_miles"] = np.nan
    g["home_travel_miles"] = 0.0
    g["altitude_ft"] = np.nan
    g["tz_shift_away"] = np.nan
    st_path = raw / "stadiums" / "stadiums.csv"
    if st_path.exists():
        st = pd.read_csv(st_path).set_index("stadium_id")
        coords = st[["lat", "lon"]].to_dict("index")
        # Each team's home stadium for a season = most frequent non-neutral home venue
        home = (g[g["location"] != "Neutral"].groupby(["season", "home_team"])["stadium_id"]
                .agg(lambda x: x.mode().iat[0] if len(x.mode()) else None))
        def dist(team, season, venue):
            base = home.get((season, team))
            if base not in coords or venue not in coords:
                return np.nan
            return haversine((coords[base]["lat"], coords[base]["lon"]), (coords[venue]["lat"], coords[venue]["lon"]))
        g["away_travel_miles"] = [dist(t, s, v) for t, s, v in zip(g.away_team, g.season, g.stadium_id)]
        g["home_travel_miles"] = [dist(t, s, v) for t, s, v in zip(g.home_team, g.season, g.stadium_id)]
        g["altitude_ft"] = g["stadium_id"].map(st["altitude"]) * 3.28084
        tz = st["tz_offset"].to_dict()
        g["tz_shift_away"] = [tz.get(v, np.nan) - tz.get(home.get((s, t)), np.nan) for t, s, v in
                              zip(g.away_team, g.season, g.stadium_id)]
    return g


def build_player_games(raw: Path, first: int, last: int) -> pd.DataFrame:
    frames = []
    for s in range(first, last + 1):
        d = _read(raw / "stats_player" / f"stats_player_week_{s}.parquet")
        if d is not None:
            frames.append(d)
    pg = pd.concat(frames, ignore_index=True)
    pg["team"] = pg["team"].map(franchise)
    pg["opponent_team"] = pg["opponent_team"].map(franchise)
    players = _read(raw / "players" / "players.parquet", ["gsis_id", "pfr_id"])
    snaps = []
    for s in range(max(first, 2012), last + 1):
        d = _read(raw / "snap_counts" / f"snap_counts_{s}.parquet")
        if d is not None:
            snaps.append(d)
    if snaps and players is not None:
        sn = pd.concat(snaps, ignore_index=True)
        sn = sn.merge(players.dropna(subset=["pfr_id"]).drop_duplicates("pfr_id"),
                      left_on="pfr_player_id", right_on="pfr_id", how="left")
        sn = sn.dropna(subset=["gsis_id"])[["gsis_id", "game_id", "offense_snaps", "offense_pct",
                                              "defense_snaps", "defense_pct", "st_snaps", "st_pct"]]
        pg = pg.merge(sn.drop_duplicates(["gsis_id", "game_id"]), left_on=["player_id", "game_id"],
                      right_on=["gsis_id", "game_id"], how="left").drop(columns=["gsis_id"])
    return pg


def concat_seasonal(raw: Path, release: str, tmpl: str, first: int, last: int) -> pd.DataFrame | None:
    frames = [d for s in range(first, last + 1)
              if (d := _read(raw / release / tmpl.format(s=s))) is not None]
    if not frames:
        return None
    out = pd.concat(frames, ignore_index=True)
    for c in ("team", "club_code"):
        if c in out.columns:
            out[c] = out[c].map(franchise)
    return out


def run(first: int | None = None, last: int | None = None, verbose: bool = True) -> dict:
    cfg = settings()
    first = first or cfg.first_season
    last = last or cfg.last_season
    raw, lake = cfg.raw_dir, cfg.lake_dir
    (lake / "plays").mkdir(parents=True, exist_ok=True)
    summary = {"plays": {}}
    for s in range(first, last + 1):
        plays = build_plays(s, raw)
        if plays is None:
            continue
        plays.to_parquet(lake / "plays" / f"{s}.parquet", index=False)
        summary["plays"][s] = {"rows": len(plays), "games": int(plays.game_id.nunique()),
                               "participation_rows": int(plays["has_participation"].sum()),
                               "ftn_rows": int(plays["has_ftn"].sum())}
        if verbose:
            print("plays", s, summary["plays"][s], flush=True)
    games = build_games(raw)
    games.to_parquet(lake / "games.parquet", index=False)
    pg = build_player_games(raw, first, last)
    pg.to_parquet(lake / "player_games.parquet", index=False)
    summary["games"] = len(games)
    summary["player_games"] = len(pg)
    for name, release, tmpl, start in (
        ("injuries", "injuries", "injuries_{s}.parquet", 2009),
        ("depth_charts", "depth_charts", "depth_charts_{s}.parquet", first),
        ("rosters", "weekly_rosters", "roster_weekly_{s}.parquet", first),
    ):
        d = concat_seasonal(raw, release, tmpl, max(first, start), last)
        if d is not None:
            for c in d.columns:
                if d[c].dtype == object:
                    d[c] = d[c].astype("string")
            d.to_parquet(lake / f"{name}.parquet", index=False)
            summary[name] = len(d)
    players = _read(raw / "players" / "players.parquet")
    if players is not None:
        players["latest_team"] = players["latest_team"].map(franchise)
        players.to_parquet(lake / "players.parquet", index=False)
        summary["players"] = len(players)
    for f in ("ngs_passing", "ngs_receiving", "ngs_rushing"):
        d = _read(raw / "nextgen_stats" / f"{f}.parquet")
        if d is not None:
            d["team_abbr"] = d["team_abbr"].map(franchise)
            d.to_parquet(lake / f"{f}.parquet", index=False)
    (lake / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary


def load_plays(seasons) -> pd.DataFrame:
    lake = settings().lake_dir
    frames = [pd.read_parquet(lake / "plays" / f"{s}.parquet") for s in seasons
              if (lake / "plays" / f"{s}.parquet").exists()]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load(name: str) -> pd.DataFrame:
    return pd.read_parquet(settings().lake_dir / f"{name}.parquet")


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, default=str))
