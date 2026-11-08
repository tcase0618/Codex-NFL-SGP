"""League behaviour parameters for the simulator, estimated from prior seasons only.

For a game in season S the estimation window is seasons S-3 .. S-1 (plus any
completed weeks of S before the game when ``through`` is given). Everything here
is fitted from play-by-play; nothing is hand-entered except documented rules.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from .. import lake
from ..config import settings


def pass_design(down, togo, yl, sd, hsr, qtr):
    """Design matrix for situational dropback probability (shared by fit and simulation)."""
    down = np.asarray(down, float)
    togo = np.clip(np.asarray(togo, float), 1, 30)
    yl = np.asarray(yl, float) / 100.0
    sd = np.clip(np.asarray(sd, float), -28, 28) / 14.0
    hsr = np.clip(np.asarray(hsr, float), 0, 1800) / 1800.0
    late = (np.asarray(qtr) >= 4).astype(float) * (1 - hsr)
    two_min = (hsr <= 120 / 1800).astype(float)
    return np.column_stack([
        down == 1, down == 2, down == 3, down == 4, np.log(togo), np.log(togo) * (down == 3), yl, yl ** 2, sd,
        sd * late, sd * two_min, two_min, late, (yl < 0.1).astype(float),
    ]).astype(float)


def fourth_design(togo, yl, sd, gsr):
    togo = np.clip(np.asarray(togo, float), 1, 30)
    yl = np.asarray(yl, float) / 100.0
    sd = np.clip(np.asarray(sd, float), -28, 28) / 14.0
    t = np.clip(np.asarray(gsr, float), 0, 3600) / 3600.0
    return np.column_stack([np.log(togo), yl, yl ** 2, (yl < 0.4).astype(float), sd, sd * (1 - t), (1 - t),
                            (sd < 0) * (1 - t) ** 2, togo <= 1]).astype(float)


@dataclass
class League:
    season: int
    pass_coef: np.ndarray
    pass_intercept: float
    fourth_coef: np.ndarray       # (3, k) classes: go, fg, punt
    fourth_intercept: np.ndarray
    fg_coef: tuple                # logistic on distance
    xp_rate: float
    two_pt_rate: float
    two_pt_success: float
    rates: dict
    dists: dict = field(default_factory=dict)
    clock: dict = field(default_factory=dict)
    windows: tuple = ()


def _emp(x, cap=None) -> np.ndarray:
    a = pd.to_numeric(pd.Series(x), errors="coerce").dropna().to_numpy(float)
    if cap is not None:
        a = a[(a >= cap[0]) & (a <= cap[1])]
    return a.astype(np.float32)


def fit_league(season: int, through_week: int | None = None) -> League:
    seasons = [s for s in range(season - 3, season) if s >= 2006]
    p = lake.load_plays(seasons)
    if through_week:
        cur = lake.load_plays([season])
        p = pd.concat([p, cur[cur["week"] < through_week]], ignore_index=True)
    p = p[p["posteam"].notna()]
    pt = p["play_type"].astype("string")
    reg = p[pt.isin(["pass", "run"]).fillna(False) & (p["qb_kneel"] != 1) & (p["qb_spike"] != 1) & (p["two_point_attempt"] != 1) & p["down"].notna()]
    X = pass_design(reg["down"], reg["ydstogo"], reg["yardline_100"], reg["score_differential"], reg["half_seconds_remaining"], reg["qtr"])
    y = (reg["qb_dropback"] == 1).astype(int)
    lr = LogisticRegression(C=10.0, max_iter=500).fit(X, y)
    # 4th-down decisions
    f4 = p[(p["down"] == 4) & (pt.isin(["pass", "run", "field_goal", "punt"]).fillna(False)) & (p["qb_kneel"] != 1)]
    cls = np.select([f4["play_type"].isin(["pass", "run"]), f4["play_type"] == "field_goal"], [0, 1], 2)
    m4 = LogisticRegression(C=10.0, max_iter=1000).fit(
        fourth_design(f4["ydstogo"], f4["yardline_100"], f4["score_differential"], f4["game_seconds_remaining"]), cls)
    fg = p[pt == "field_goal"]
    dist = pd.to_numeric(fg["kick_distance"], errors="coerce").fillna(fg["yardline_100"] + 17)
    mfg = LogisticRegression(C=10.0).fit(np.column_stack([dist, dist ** 2 / 100]), (fg["field_goal_result"] == "made").astype(int))
    xp = p[pt == "extra_point"]
    tp = p[p["two_point_attempt"] == 1]
    tds = (p["touchdown"] == 1) & (p["td_team"] == p["posteam"])
    # penalty population includes pre-snap fouls, which nflfastR records as play_type == 'no_play'
    snaps = p[pt.isin(["pass", "run", "no_play"]).fillna(False) & (p["two_point_attempt"] != 1) & p["down"].notna()]
    drop = reg[reg["qb_dropback"] == 1]
    sack = drop["sack"] == 1
    scr = drop["qb_scramble"] == 1
    thr = drop[~sack & ~scr & (drop["pass_attempt"] == 1)]
    run = reg[(reg["qb_dropback"] != 1)]
    comp = thr[thr["complete_pass"] == 1]
    rates = {
        "sack": float(sack.mean()), "scramble": float(scr.mean()), "complete": float((thr["complete_pass"] == 1).mean()),
        "int": float((thr["interception"] == 1).mean()), "untargeted": float(thr["receiver_player_id"].isna().mean()),
        "fumble_lost_run": float((run["fumble_lost"] == 1).mean()), "fumble_lost_comp": float((comp["fumble_lost"] == 1).mean()),
        "fumble_lost_sack": float((drop[sack]["fumble_lost"] == 1).mean()),
        "pen_off": float(((snaps["penalty"] == 1) & (snaps["penalty_team"] == snaps["posteam"])).mean()),
        "pen_def": float(((snaps["penalty"] == 1) & (snaps["penalty_team"] == snaps["defteam"])).mean()),
        "pbu_per_incomplete": float(thr.loc[thr["complete_pass"] == 0, "pass_defense_1_player_id"].notna().mean()),
        "tackled_comp": float(comp[["solo_tackle_1_player_id", "assist_tackle_1_player_id", "tackle_with_assist_1_player_id"]].notna().any(axis=1).mean()),
        "tackled_run": float(run[["solo_tackle_1_player_id", "assist_tackle_1_player_id", "tackle_with_assist_1_player_id"]].notna().any(axis=1).mean()),
        "assist_per_tackle": _extra_tacklers(p),
        "ypc": float(run["yards_gained"].mean()), "ypcomp": float(comp["yards_gained"].mean()),
    }
    ko = p[pt == "kickoff"]
    ko_start = 100 - pd.to_numeric(ko["yardline_100"], errors="coerce") + pd.to_numeric(ko["kick_distance"], errors="coerce").fillna(0) - pd.to_numeric(ko["return_yards"], errors="coerce").fillna(0)
    # kickoff outcome: next offense's yardline_100 = derived from next play; use touchback/return info
    # kickoff outcome = receiving offense's yardline_100 on the next scrimmage snap of the same game
    nxt_type = p.groupby("game_id")["play_type"].shift(-1).astype("string")
    nxt_yl = pd.to_numeric(p.groupby("game_id")["yardline_100"].shift(-1), errors="coerce")
    is_ko = (pt == "kickoff").fillna(False) & nxt_type.isin(["pass", "run"]).fillna(False) & nxt_yl.notna()
    ko_next = nxt_yl[is_ko].to_numpy()
    if len(ko_next) < 200:
        ko_next = np.full(200, 70.0)
    punts = p[(pt == "punt").fillna(False)]
    pnet = np.where(punts["touchback"] == 1, pd.to_numeric(punts["kick_distance"], errors="coerce") - 20,
                    pd.to_numeric(punts["kick_distance"], errors="coerce") - pd.to_numeric(punts["return_yards"], errors="coerce").fillna(0))
    punts = punts.assign(net=pnet)
    ints = thr[thr["interception"] == 1]
    dists = {
        "comp_yards": _emp(comp["yards_gained"], (-10, 99)), "rush_yards": _emp(run["yards_gained"], (-15, 99)),
        "scramble_yards": _emp(drop[scr]["yards_gained"], (-10, 99)), "sack_yards": _emp(drop[sack]["yards_gained"], (-25, 0)),
        "ko_start": np.asarray(ko_next, np.float32),
        "punt_net_by_yl": {b: _emp(punts.loc[(punts["yardline_100"] // 10) == b, "net"]) for b in range(3, 10)},
        "int_air": _emp(ints["air_yards"].fillna(10)), "int_ret": _emp(ints["return_yards"].fillna(0)),
        "pen_off_yards": _emp(snaps.loc[(snaps["penalty"] == 1) & (snaps["penalty_team"] == snaps["posteam"]), "penalty_yards"], (1, 30)),
        "pen_def_yards": _emp(snaps.loc[(snaps["penalty"] == 1) & (snaps["penalty_team"] == snaps["defteam"]), "penalty_yards"], (1, 50)),
        "ko_return_yards": _emp(ko.loc[(ko["touchback"] != 1), "return_yards"], (0, 110)),
        "punt_return_yards": _emp(punts.loc[(punts["touchback"] != 1) & punts["punt_returner_player_id"].notna(), "return_yards"], (-10, 100)),
    }
    rates["ko_returned"] = float(((ko["touchback"] != 1) & ko["kickoff_returner_player_id"].notna()).mean())
    rates["punt_returned"] = float(punts["punt_returner_player_id"].notna().mean())
    clock = _clock(p)
    return League(season, lr.coef_[0], float(lr.intercept_[0]), m4.coef_, m4.intercept_,
                  (float(mfg.intercept_[0]), float(mfg.coef_[0, 0]), float(mfg.coef_[0, 1])),
                  float((xp["extra_point_result"] == "good").mean()) if len(xp) else 0.95,
                  float(len(tp) / max(1, tds.sum())), float((tp["two_point_conv_result"] == "success").mean()) if len(tp) else 0.48,
                  rates, dists, clock, tuple(seasons))


def _clock(p: pd.DataFrame) -> dict:
    """Empirical seconds consumed between consecutive snaps by outcome and tempo."""
    q = p[["game_id", "game_seconds_remaining", "play_type", "complete_pass", "qb_scramble", "sack", "qtr",
           "score_differential", "half_seconds_remaining", "fixed_drive"]].copy()
    q["next_gsr"] = q.groupby("game_id")["game_seconds_remaining"].shift(-1)
    q["same_drive"] = q.groupby("game_id")["fixed_drive"].shift(-1) == q["fixed_drive"]
    q["dt"] = q["game_seconds_remaining"] - q["next_gsr"]
    q = q[q["same_drive"] & (q["dt"] >= 0) & (q["dt"] <= 60)]
    pt = q["play_type"].astype("string")
    hurry = ((q["qtr"].isin([2, 4])) & (q["half_seconds_remaining"] <= 240) & (q["score_differential"] <= 0)).to_numpy()
    isrun = (pt == "run").fillna(False).to_numpy(bool)
    ispass = (pt == "pass").fillna(False).to_numpy(bool)
    cat = np.select([isrun, ispass & (q["sack"] == 1).to_numpy(), ispass & (q["qb_scramble"] == 1).to_numpy(),
                     ispass & (q["complete_pass"] == 1).to_numpy(), ispass],
                    ["run", "sack", "scramble", "complete", "incomplete"], "other")
    out = {}
    for c in ("run", "sack", "scramble", "complete", "incomplete"):
        for h in (0, 1):
            arr = q.loc[(cat == c) & (hurry == bool(h)), "dt"].to_numpy(np.float32)
            out[(c, h)] = arr if len(arr) > 50 else q.loc[cat == c, "dt"].to_numpy(np.float32)
    return out


def _extra_tacklers(p: pd.DataFrame) -> float:
    """Mean credited tacklers beyond the first on plays with any tackle credit."""
    cols = [c for c in ("solo_tackle_1_player_id", "solo_tackle_2_player_id", "assist_tackle_1_player_id",
                        "assist_tackle_2_player_id", "tackle_with_assist_1_player_id", "tackle_with_assist_2_player_id") if c in p]
    n = p[cols].notna().sum(axis=1)
    n = n[n > 0]
    return float((n - 1).mean()) if len(n) else 0.0


def league_for(season: int, through_week: int | None = None) -> League:
    path = settings().model_dir / f"sim_league_{season}_{through_week or 0}.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())
    lg = fit_league(season, through_week)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps(lg))
    return lg
