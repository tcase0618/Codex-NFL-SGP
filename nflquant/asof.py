"""As-of (pregame) team field values for every team-game, computed vectorially.

value_asof(team, game) = (Σ_cur + k·prior) / (D_cur + k)
    Σ_cur, D_cur  numerator/denominator sums over the team's earlier games this season
    prior         0.5·(last season's full-season rate) + 0.5·(league rate of last season)
    k             pseudo-count in the field's own denominator units

The current game is excluded by shifting cumulative sums one game, so no
outcome of game g can enter its own features.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .metrics.team import load_team_games


def asof_fields(field_ids: list[str], k: float | dict = 150.0) -> pd.DataFrame:
    tg = load_team_games()
    tg = tg[tg["kickoff"].notna()].sort_values(["team", "season", "kickoff"]).reset_index(drop=True)
    out = tg[["game_id", "team", "season", "week", "opponent", "kickoff"]].copy()
    for fid in field_ids:
        n, d = tg[fid + "__n"].astype(float), tg[fid + "__d"].astype(float)
        played = tg["points_for"].notna()
        n, d = n.where(played, 0.0), d.where(played, 0.0)
        grp = [tg["team"], tg["season"]]
        cn = n.groupby(grp).cumsum() - n
        cd = d.groupby(grp).cumsum() - d
        season_tot = pd.DataFrame({"team": tg["team"], "season": tg["season"], "n": n, "d": d}).groupby(["team", "season"])[["n", "d"]].sum()
        league = season_tot.groupby("season")[["n", "d"]].sum()
        league_rate = (league["n"] / league["d"].replace(0, np.nan))
        prev_team = season_tot.rename(index=lambda s: s + 1, level=1)
        key = pd.MultiIndex.from_arrays([tg["team"], tg["season"]])
        pt_n = prev_team["n"].reindex(key).to_numpy()
        pt_d = prev_team["d"].reindex(key).to_numpy()
        lg_prev = league_rate.reindex(tg["season"] - 1).to_numpy()
        lg_cur = league_rate.reindex(tg["season"]).to_numpy()
        lg = np.where(np.isnan(lg_prev), lg_cur, lg_prev)
        prev_rate = np.where(pt_d > 0, pt_n / np.where(pt_d > 0, pt_d, 1), lg)
        prior = 0.5 * prev_rate + 0.5 * lg
        kk = k[fid] if isinstance(k, dict) else k
        val = (cn.to_numpy() + kk * prior) / (cd.to_numpy() + kk)
        out[fid] = np.where(np.isnan(prior) & (cd.to_numpy() == 0), np.nan, val)
        out[fid + "__sample"] = cd.to_numpy()
    return out
