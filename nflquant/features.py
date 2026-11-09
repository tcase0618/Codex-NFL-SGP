"""Pregame feature engineering for game- and player-level models.

Information boundary: a feature row for game g uses only
  * completed games strictly before g's week (ratings, QB history, usage),
  * injury-report statuses published for g's week (official reports are issued
    before kickoff; the nflverse copy was retrieved later, so publication time is
    asserted by the source process, not by a stored timestamp),
  * schedule facts known in advance (home, rest, travel, roof, forecast-proxy
    temperature/wind recorded in the schedule).
Outcome columns (scores, result) are attached only as labels.

The projected starting QB is the previous game's primary QB (most dropbacks),
replaced by the team's next most-used QB if listed Out/Doubtful; never the
actual starter. Week 1 uses the prior-season primary QB if still on the week-1
roster, else the week-1 depth-chart QB1 (flagged as depth-chart sourced).
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from . import lake
from .config import settings

QB_PRIOR_DROPBACKS = 250.0   # EB prior weight (dropbacks) toward replacement level
QB_REPLACEMENT = -0.08       # replacement-level EPA/dropback prior mean (fitted value logged in build)
INACTIVE_STATUSES = {"Out", "Doubtful"}


@lru_cache(maxsize=1)
def qb_games() -> pd.DataFrame:
    """Per QB per game dropbacks and EPA (all seasons)."""
    cols = ["game_id", "season", "week", "posteam", "passer_id", "qb_dropback", "epa", "qb_spike", "two_point_attempt", "game_date"]
    frames = []
    for s in range(settings().first_season, settings().last_season + 1):
        p = lake.load_plays([s])
        if p.empty:
            continue
        p = p[cols]
        p = p[(p["qb_dropback"] == 1) & (p["qb_spike"] != 1) & (p["two_point_attempt"] != 1) & p["passer_id"].notna()]
        frames.append(p.groupby(["game_id", "season", "week", "posteam", "passer_id", "game_date"], as_index=False)
                      .agg(dropbacks=("epa", "size"), epa=("epa", "sum")))
    q = pd.concat(frames, ignore_index=True)
    q["game_date"] = pd.to_datetime(q["game_date"])
    return q.sort_values(["game_date", "game_id"])


def _injury_out() -> pd.DataFrame:
    inj = lake.load("injuries")
    inj = inj[inj["report_status"].isin(list(INACTIVE_STATUSES))]
    return inj[["season", "week", "team", "gsis_id", "position", "report_status"]].dropna(subset=["gsis_id"])


def projected_qbs(games: pd.DataFrame) -> pd.DataFrame:
    """Projected starting QB for each (game, team) using pregame information only."""
    q = qb_games()
    out_ids = _injury_out()
    out_set = set(zip(out_ids["season"].astype(int), out_ids["week"].astype(int), out_ids["team"], out_ids["gsis_id"]))
    primary = q.sort_values("dropbacks").groupby(["game_id", "posteam"]).tail(1)
    primary = primary.merge(games[["game_id", "kickoff"]], on="game_id", how="left").sort_values("kickoff")
    rosters = lake.load("rosters")
    rost_qb = rosters[(rosters["position"] == "QB")][["season", "week", "team", "gsis_id"]].dropna()
    rost_set = set(zip(rost_qb["season"].astype(int), rost_qb["week"].astype(int), rost_qb["team"], rost_qb["gsis_id"]))
    depth = lake.load("depth_charts")
    dq = depth[(depth["position"] == "QB") & (depth["depth_team"].astype("string") == "1")][["season", "week", "club_code", "gsis_id"]].dropna()
    depth_qb1 = {(int(s), int(w), t): g for s, w, t, g in zip(dq["season"], dq["week"], dq["club_code"], dq["gsis_id"])}
    # 2025+ depth charts are timestamped snapshots without week: latest QB order per team-season
    snap = depth[depth["dt"].notna() & (depth["pos_abb"] == "QB")] if "dt" in depth else depth.iloc[:0]
    depth_qbs = {}
    if len(snap):
        snap = snap.assign(season=pd.to_datetime(snap["dt"]).dt.year.where(pd.to_datetime(snap["dt"]).dt.month >= 3,
                                                                          pd.to_datetime(snap["dt"]).dt.year - 1))
        snap = snap.assign(ts=pd.to_datetime(snap["dt"]).dt.tz_localize(None))
        for (ss, t), gg in snap.groupby(["season", "team"]):
            depth_qbs[(int(ss), t)] = [(ts, list(dict.fromkeys(x.sort_values("pos_rank")["gsis_id"].dropna())))
                                       for ts, x in gg.groupby("ts")]
    rows = []
    for _, g in games.sort_values("kickoff").iterrows():
        for team in (g["home_team"], g["away_team"]):
            prev = primary[(primary["posteam"] == team) & (primary["kickoff"] < g["kickoff"])]
            s, w = int(g["season"]), int(g["week"])
            source = "previous_game_primary"
            cand = None
            if len(prev):
                cand = prev["passer_id"].iat[-1]
                if prev["season"].iat[-1] != s:
                    source = "prior_season_primary"
                    if rost_set and (s, 1, team, cand) not in rost_set and (s, w, team, cand) not in rost_set:
                        cand = depth_qb1.get((s, w, team)) or depth_qb1.get((s, 1, team))
                        source = "depth_chart_qb1" if cand else "unknown"
            if cand is not None and (s, w, team, cand) in out_set:
                recent = q[(q["posteam"] == team) & (q["game_date"] < g["kickoff"]) & (q["season"] >= s - 1)]
                alts = recent[recent["passer_id"] != cand].groupby("passer_id")["dropbacks"].sum().sort_values(ascending=False)
                # replacement must be on this team's roster for this week (fix: Nov 9 2026 audit —
                # a former backup who had left the team was being projected)
                on_roster = lambda a: (not rost_set) or (s, w, team, a) in rost_set
                alts = [a for a in alts.index if (s, w, team, a) not in out_set and on_roster(a)]
                if not alts:
                    snaps = [o for ts, o in depth_qbs.get((s, team), []) if ts < g["kickoff"]]
                    dc = snaps[-1] if snaps else []
                    alts = [a for a in dc if (s, w, team, a) not in out_set and a != cand and on_roster(a)]
                cand, source = (alts[0], "injury_replacement") if alts else (None, "unknown")
            elif cand is not None and rost_set and (s, w, team, cand) not in rost_set and len(prev) and prev["season"].iat[-1] == s:
                # previous-game passer no longer on the roster (trade/release): next rostered QB
                recent = q[(q["posteam"] == team) & (q["game_date"] < g["kickoff"]) & (q["season"] == s)]
                alts = [a for a in recent.groupby("passer_id")["dropbacks"].sum().sort_values(ascending=False).index
                        if (s, w, team, a) in rost_set and (s, w, team, a) not in out_set]
                cand, source = (alts[0], "roster_replacement") if alts else (cand, source)
            rows.append({"game_id": g["game_id"], "team": team, "qb_id": cand, "qb_source": source})
    return pd.DataFrame(rows)


def qb_ratings(proj: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Shrunk prior EPA/dropback for each projected QB, using games before kickoff."""
    q = qb_games()
    kick = games.set_index("game_id")["kickoff"]
    out = []
    by_qb = {k: v for k, v in q.groupby("passer_id")}
    for _, r in proj.iterrows():
        k0 = kick.get(r["game_id"])
        hist = by_qb.get(r["qb_id"])
        if hist is None or k0 is None:
            out.append((0.0, 0.0, QB_REPLACEMENT))
            continue
        h = hist[hist["game_date"] < k0].tail(24)  # ~1.5 seasons of starts
        db, epa = float(h["dropbacks"].sum()), float(h["epa"].sum())
        shr = (epa + QB_PRIOR_DROPBACKS * QB_REPLACEMENT) / (db + QB_PRIOR_DROPBACKS)
        out.append((db, epa / db if db else 0.0, shr))
    proj = proj.copy()
    proj["qb_prior_dropbacks"], proj["qb_raw_epa_db"], proj["qb_epa_db"] = zip(*out) if out else ([], [], [])
    return proj


def injury_load(games: pd.DataFrame) -> pd.DataFrame:
    """Snap-share-weighted Out/Doubtful counts per team-week, offense and defense separately."""
    inj = _injury_out()
    pg = lake.load("player_games")[["player_id", "season", "week", "team", "offense_pct", "defense_pct", "game_id"]]
    pg = pg.sort_values(["season", "week"])
    by_ts = {k: v for k, v in pg.groupby(["team", "season"])}
    empty = pg.iloc[:0]
    rows = []
    for (s, w, t), grp in inj.groupby(["season", "week", "team"]):
        tp = by_ts.get((t, s), empty)
        hist = tp[(tp["week"] < w) & tp["player_id"].isin(grp["gsis_id"])]
        if hist.empty:
            off = de = 0.0
        else:
            last = hist.groupby("player_id").tail(4).groupby("player_id")[["offense_pct", "defense_pct"]].mean()
            off, de = float(last["offense_pct"].fillna(0).sum()), float(last["defense_pct"].fillna(0).sum())
        rows.append({"season": int(s), "week": int(w), "team": t, "inj_off_snaps": off, "inj_def_snaps": de, "inj_count": len(grp)})
    return pd.DataFrame(rows)


RATING_COLS = ["off_epa", "def_epa", "off_pass", "def_pass", "off_rush", "def_rush", "off_sr", "def_sr",
               "off_ppd", "def_ppd", "off_pressure", "def_pressure", "elo", "games_played"]


def build_game_features(verbose: bool = True) -> pd.DataFrame:
    games = lake.load("games")
    games = games[games["season"] >= 2007].copy()
    ratings = pd.read_parquet(settings().lake_dir / "ratings.parquet")
    r = ratings[["season", "week", "team"] + RATING_COLS + ["elo_hfa_points"]]
    f = games[["game_id", "season", "week", "game_type", "kickoff", "home_team", "away_team", "home_score", "away_score",
               "result", "total", "spread_line", "total_line", "home_moneyline", "away_moneyline", "home_rest",
               "away_rest", "div_game", "roof", "temp", "wind", "neutral_site", "away_travel_miles", "tz_shift_away",
               "altitude_ft", "completed"]].copy()
    for side in ("home", "away"):
        f = f.merge(r.add_prefix(f"{side}_").rename(columns={f"{side}_season": "season", f"{side}_week": "week",
                                                             f"{side}_team": f"{side}_team"}),
                    on=["season", "week", f"{side}_team"], how="left")
    if verbose:
        print("features: ratings merged", f.shape, flush=True)
    proj = qb_ratings(projected_qbs(games), games)
    for side in ("home", "away"):
        p = proj.rename(columns={"team": f"{side}_team", "qb_id": f"{side}_qb_id", "qb_source": f"{side}_qb_source",
                                 "qb_prior_dropbacks": f"{side}_qb_dropbacks", "qb_raw_epa_db": f"{side}_qb_raw",
                                 "qb_epa_db": f"{side}_qb_epa"})
        f = f.merge(p, on=["game_id", f"{side}_team"], how="left")
    if verbose:
        print("features: QBs projected", flush=True)
    inj = injury_load(games)
    for side in ("home", "away"):
        f = f.merge(inj.rename(columns={"team": f"{side}_team", "inj_off_snaps": f"{side}_inj_off",
                                        "inj_def_snaps": f"{side}_inj_def", "inj_count": f"{side}_inj_n"}),
                    on=["season", "week", f"{side}_team"], how="left")
    for c in ("home_inj_off", "home_inj_def", "away_inj_off", "away_inj_def", "home_inj_n", "away_inj_n"):
        f[c] = f[c].fillna(0.0)
    f["injury_data"] = (f["season"] >= 2012).astype(float)
    f = add_derived(f)
    f.to_parquet(settings().lake_dir / "game_features.parquet", index=False)
    return f


def add_derived(f: pd.DataFrame) -> pd.DataFrame:
    f = f.copy()
    f["is_neutral"] = f["neutral_site"].fillna(0)
    f["dome"] = f["roof"].isin(["dome", "closed"]).astype(float)
    f["temp_f"] = pd.to_numeric(f["temp"], errors="coerce").where(f["dome"] == 0, 70.0)
    f["wind_mph"] = pd.to_numeric(f["wind"], errors="coerce").where(f["dome"] == 0, 0.0)
    f["rest_diff"] = f["home_rest"] - f["away_rest"]
    f["is_playoff"] = (f["game_type"] != "REG").astype(float)
    for k in ("epa", "pass", "rush", "sr", "ppd"):
        # home offense vs away defense (defense rating = allowed, so add)
        f[f"h_off_matchup_{k}"] = f[f"home_off_{k}"] + f[f"away_def_{k}"]
        f[f"a_off_matchup_{k}"] = f[f"away_off_{k}"] + f[f"home_def_{k}"]
        f[f"net_{k}"] = f[f"h_off_matchup_{k}"] - f[f"a_off_matchup_{k}"]
        f[f"sum_{k}"] = f[f"h_off_matchup_{k}"] + f[f"a_off_matchup_{k}"]
    f["net_qb"] = f["home_qb_epa"] - f["away_qb_epa"]
    f["sum_qb"] = f["home_qb_epa"] + f["away_qb_epa"]
    f["elo_diff"] = f["home_elo"] - f["away_elo"]
    f["net_pressure"] = (f["home_off_pressure"] + f["away_def_pressure"]) - (f["away_off_pressure"] + f["home_def_pressure"])
    f["net_inj_off"] = f["home_inj_off"] - f["away_inj_off"]
    f["net_inj_def"] = f["home_inj_def"] - f["away_inj_def"]
    f["sum_inj"] = f["home_inj_off"] + f["away_inj_off"] + f["home_inj_def"] + f["away_inj_def"]
    f["early_season"] = (f["week"] <= 4).astype(float)
    f["min_games"] = np.minimum(f["home_games_played"], f["away_games_played"])
    return f


def load_game_features() -> pd.DataFrame:
    return pd.read_parquet(settings().lake_dir / "game_features.parquet")


if __name__ == "__main__":
    build_game_features()
