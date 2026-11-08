"""Out-of-sample backtest of the simulator on completed games.

For a deterministic sample of completed games, simulate from *pregame* inputs only
(walk-forward game prediction for the anchor, walk-forward player predictions for
usage) and compare against what happened:
    win-probability log loss / Brier (sim vs game model), margin/total 80% PI coverage,
    simulated margin σ vs empirical residual σ, player-stat MAE (sim mean vs ML mean),
    player 80% interval coverage, and accounting identities in every simulation.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .. import lake
from ..config import settings
from ..models.calibration import brier, log_loss
from .build import build_spec, anchor, summarize
from .engine import Sim

MAP = {"passing_yards": "pass_yds", "rushing_yards": "rush_yds", "receiving_yards": "rec_yds", "receptions": "rec",
       "targets": "targets", "passing_tds": "pass_td", "interceptions": "pass_int", "carries": "rush_att"}


def run(seasons=(2025, 2026), per_season: int = 60, n: int = 2000, seed: int = 7, verbose: bool = True) -> dict:
    oos = pd.read_parquet(settings().lake_dir / "game_oos_predictions.parquet")
    pp = pd.read_parquet(settings().lake_dir / "player_oos_predictions.parquet")
    gf = pd.read_parquet(settings().lake_dir / "game_features.parquet").set_index("game_id")
    pool = oos[oos["season"].isin(seasons)].sort_values("game_id")
    sample = pd.concat([g.sample(min(per_season, len(g)), random_state=seed) for _, g in pool.groupby("season")])
    rows, prow, ident = [], [], True
    for i, (_, g) in enumerate(sample.iterrows()):
        proj = pp[pp["game_id"] == g["game_id"]].rename(columns={"pred": "mean"})
        if proj.empty:
            continue
        f = gf.loc[g["game_id"]]
        qb = {f["home_team"]: f["home_qb_id"], f["away_team"]: f["away_qb_id"]}
        spec = build_spec(g["game_id"], proj, qb)
        anchor(spec, (g["home_points"], g["away_points"]), n=1500, iters=4, seed=seed + i)
        sim = Sim(spec, n, seed + 1000 + i).run()
        s = summarize(sim, spec)
        ident &= all(v is not False for v in s["consistency"].values())
        m = sim.ts["points"][:, 0] - sim.ts["points"][:, 1]
        t = sim.ts["points"].sum(1)
        rows.append({"game_id": g["game_id"], "season": g["season"], "y_win": g["y_win"], "p_sim": s["win_prob_home"] + 0.5 * s["tie_prob"],
                     "p_model": g["p_home"], "y_margin": g["y_margin"], "y_total": g["y_total"],
                     "m_lo": np.percentile(m, 10), "m_hi": np.percentile(m, 90), "t_lo": np.percentile(t, 10), "t_hi": np.percentile(t, 90),
                     "sim_sd": float(m.std()), "model_margin": g["margin_ensemble"],
                     "p_ot": s["ot_prob"], "p_tie": s["tie_prob"]})
        idx = sim.pidx
        for target, stat in MAP.items():
            sub = proj[proj["target"] == target]
            for _, r in sub.iterrows():
                j = idx.get(r["player_id"])
                if j is None:
                    continue
                x = sim.ps[stat][:, j]
                prow.append({"target": target, "y": r["y"], "ml": r["mean"], "sim": float(x.mean()),
                             "lo": np.percentile(x, 10), "hi": np.percentile(x, 90)})
        if verbose and i % 10 == 0:
            print("sim backtest", i, g["game_id"], flush=True)
    d, p = pd.DataFrame(rows), pd.DataFrame(prow)
    res = {"n_games": int(len(d)), "n_sims_per_game": n, "scope": f"random sample of {per_season}/season from {list(seasons)}",
           "log_loss_sim": log_loss(d["p_sim"], d["y_win"]), "log_loss_model": log_loss(d["p_model"], d["y_win"]),
           "brier_sim": brier(d["p_sim"], d["y_win"]), "brier_model": brier(d["p_model"], d["y_win"]),
           "margin_cov80": float(np.mean((d.y_margin >= d.m_lo) & (d.y_margin <= d.m_hi))),
           "total_cov80": float(np.mean((d.y_total >= d.t_lo) & (d.y_total <= d.t_hi))),
           "mean_sim_margin_sd": float(d["sim_sd"].mean()),
           "empirical_margin_resid_sd": float(np.std(d["y_margin"] - d["model_margin"])),
           "all_identities_hold": bool(ident), "player": {},
           "sim_ot_rate": float(d["p_ot"].mean()), "sim_tie_rate": float(d["p_tie"].mean()),
           "observed_ot_rate": _observed("overtime", seasons), "observed_tie_rate": _observed("tie", seasons),
           "note": "Simulated from walk-forward pregame inputs only; anchoring uses the walk-forward game model's expected points."}
    for t, g in p.groupby("target"):
        res["player"][t] = {"n": int(len(g)), "mae_sim": float(np.mean(np.abs(g.sim - g.y))), "mae_ml": float(np.mean(np.abs(g.ml - g.y))),
                            "cov80": float(np.mean((g.y >= g.lo) & (g.y <= g.hi)))}
    (settings().lake_dir / "sim_validation.json").write_text(json.dumps(res, indent=1, default=float))
    return res


def _observed(kind: str, seasons) -> float:
    g = lake.load("games")
    g = g[g["season"].between(min(seasons) - 5, max(seasons)) & g["completed"]]
    return float((g["overtime"] == 1).mean()) if kind == "overtime" else float((g["result"] == 0).mean())


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, default=float))
