"""Opponent-adjusted team ratings, computed strictly as-of each week.

For each season S and week W, using only completed games of season S with week < W:

    y_g = μ + o_team + d_opp + h·home + ε,   weight = plays in the team-game

solved as ridge regression centred on a prior β0 (last season's final ratings ×
``carry``), i.e. minimise Σ w(y − Xβ)² + λ‖β − β0‖². Early-season ratings are
therefore dominated by the regressed prior and converge to in-season evidence.
Recency weighting uses an exponential half-life in games.

Rated quantities (offense and defense each): EPA/play, EPA/dropback, EPA/designed
run, success rate, points per drive, pressure rate (2016-2025 only).

Elo (benchmark only): K=20, margin-of-victory multiplier ln(|MOV|+1)·2.2/(Δ·0.001+2.2),
season-to-season regression of one third toward 1505, home advantage fitted from
prior seasons' average home margin (25 Elo points per point is used as conversion).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import settings
from .metrics.team import load_team_games

TARGETS = {
    "epa": "off.epa_play.all",
    "pass": "off.pass_epa.all",
    "rush": "off.rush_epa.all",
    "sr": "off.success.all",
    "ppd": "off.points_per_drive.all",
    "pressure": "off.pressure_rate.all",
}
LAMBDA = {"epa": 220.0, "pass": 150.0, "rush": 150.0, "sr": 220.0, "ppd": 40.0, "pressure": 150.0}
CARRY = 0.6
HALF_LIFE_GAMES = 10.0


def _fit(obs: pd.DataFrame, teams: list[str], prior: dict, lam: float) -> tuple[dict, dict, float, float]:
    """Weighted ridge around prior. Returns (off, def, mu, home)."""
    t_index = {t: i for i, t in enumerate(teams)}
    k = len(teams)
    if obs.empty:
        return ({t: prior.get(("o", t), 0.0) for t in teams}, {t: prior.get(("d", t), 0.0) for t in teams}, np.nan, 0.0)
    y = obs["y"].to_numpy()
    w = obs["w"].to_numpy()
    mu = np.average(y, weights=w)
    X = np.zeros((len(obs), 2 * k + 1))
    for r, (tm, op, hm) in enumerate(zip(obs["team"], obs["opponent"], obs["is_home"])):
        X[r, t_index[tm]] = 1.0
        X[r, k + t_index[op]] = 1.0
        X[r, 2 * k] = hm - 0.5
    b0 = np.zeros(2 * k + 1)
    for t, i in t_index.items():
        b0[i] = prior.get(("o", t), 0.0)
        b0[k + i] = prior.get(("d", t), 0.0)
    pen = np.full(2 * k + 1, lam)
    pen[2 * k] = lam * 0.5
    A = X.T @ (X * w[:, None]) + np.diag(pen)
    rhs = X.T @ (w * (y - mu)) + pen * b0
    beta = np.linalg.solve(A, rhs)
    off = {t: float(beta[i]) for t, i in t_index.items()}
    de = {t: float(beta[k + i]) for t, i in t_index.items()}
    return off, de, float(mu), float(beta[2 * k])


def build(verbose: bool = True) -> pd.DataFrame:
    tg = load_team_games()
    tg = tg[tg["points_for"].notna() | tg["kickoff"].notna()]
    games = pd.read_parquet(settings().lake_dir / "games.parquet")
    out = []
    prior: dict[str, dict] = {k: {} for k in TARGETS}
    seasons = sorted(tg["season"].dropna().unique().astype(int))
    for s in seasons:
        sg = tg[(tg["season"] == s) & tg["points_for"].notna()].copy()
        teams = sorted(set(games.loc[games["season"] == s, "home_team"]) | set(games.loc[games["season"] == s, "away_team"]))
        weeks = sorted(games.loc[games["season"] == s, "week"].unique().astype(int))
        sg["order"] = sg.groupby("team")["kickoff"].rank(method="first")
        final = {}
        for W in weeks + [max(weeks) + 1]:
            past = sg[sg["week"] < W]
            rec = {"season": s, "week": W}
            rows = {t: {"season": s, "week": W, "team": t} for t in teams}
            for key, fid in TARGETS.items():
                if key == "pressure" and not (2016 <= s <= 2025):
                    for t in teams:
                        rows[t][f"off_{key}"] = np.nan
                        rows[t][f"def_{key}"] = np.nan
                    continue
                d = past[fid + "__d"].to_numpy()
                obs = pd.DataFrame({"team": past["team"], "opponent": past["opponent"], "is_home": past["is_home"],
                                    "y": np.where(d > 0, past[fid + "__n"] / np.where(d > 0, d, 1), np.nan),
                                    "w": d})
                if not past.empty:
                    latest = past.groupby("team")["order"].transform("max")
                    obs["w"] = obs["w"] * 0.5 ** ((latest - past["order"]).to_numpy() / HALF_LIFE_GAMES)
                obs = obs[obs["w"] > 0].dropna()
                off, de, mu, home = _fit(obs, teams, prior[key], LAMBDA[key] if key != "ppd" else LAMBDA[key])
                for t in teams:
                    rows[t][f"off_{key}"] = off.get(t, 0.0)
                    rows[t][f"def_{key}"] = de.get(t, 0.0)
                    rows[t][f"mu_{key}"] = mu
                    rows[t][f"home_{key}"] = home
                if W == max(weeks) + 1:
                    final[key] = {("o", t): CARRY * off.get(t, 0.0) for t in teams}
                    final[key].update({("d", t): CARRY * de.get(t, 0.0) for t in teams})
            gp = past.groupby("team").size()
            for t in teams:
                rows[t]["games_played"] = int(gp.get(t, 0))
            out.extend(rows.values())
        prior = {k: final.get(k, {}) for k in TARGETS}
        if verbose:
            print("ratings", s, flush=True)
    r = pd.DataFrame(out)
    r = r.merge(_elo(games), on=["season", "week", "team"], how="left")
    r.to_parquet(settings().lake_dir / "ratings.parquet", index=False)
    return r


def _elo(games: pd.DataFrame) -> pd.DataFrame:
    g = games[games["season"] >= 2006].sort_values(["kickoff", "game_id"])
    elo: dict[str, float] = {}
    rows = []
    hfa_hist = []
    hfa_points = 2.5
    for s in sorted(g["season"].unique()):
        elo = {t: 1505 + (v - 1505) * (2 / 3) for t, v in elo.items()}
        sg = g[g["season"] == s]
        if hfa_hist:
            hfa_points = float(np.mean(hfa_hist[-5:]))
        teams = set(sg["home_team"]) | set(sg["away_team"])
        for wk in sorted(sg["week"].unique()):
            for t in teams:
                rows.append({"season": s, "week": int(wk), "team": t, "elo": elo.get(t, 1505.0), "elo_hfa_points": hfa_points})
            for _, x in sg[sg["week"] == wk].iterrows():
                if pd.isna(x["result"]):
                    continue
                h, a = x["home_team"], x["away_team"]
                rh, ra = elo.get(h, 1505.0), elo.get(a, 1505.0)
                hfa = 0 if x["location"] == "Neutral" else hfa_points * 25
                diff = rh + hfa - ra
                exp_h = 1 / (1 + 10 ** (-diff / 400))
                mov = float(x["result"])
                act = 1.0 if mov > 0 else (0.0 if mov < 0 else 0.5)
                mult = np.log(abs(mov) + 1) * 2.2 / ((diff if act == 1 else -diff) * 0.001 + 2.2)
                delta = 20 * mult * (act - exp_h)
                elo[h], elo[a] = rh + delta, ra - delta
        reg = sg[(sg["game_type"] == "REG") & sg["result"].notna() & (sg["location"] != "Neutral")]
        if len(reg):
            hfa_hist.append(float(reg["result"].mean()))
        final_week = int(sg["week"].max()) + 1
        for t in teams:
            rows.append({"season": s, "week": final_week, "team": t, "elo": elo.get(t, 1505.0), "elo_hfa_points": hfa_points})
    return pd.DataFrame(rows).drop_duplicates(["season", "week", "team"], keep="first")


def load() -> pd.DataFrame:
    return pd.read_parquet(settings().lake_dir / "ratings.parquet")


if __name__ == "__main__":
    build()
