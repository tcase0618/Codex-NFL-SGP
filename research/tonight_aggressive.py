"""Aggressive ladder: main and ceiling lines, maximize hit probability within payout tiers.

Legs are pushed to and above each player's median outcome. For every payout tier the search
returns the combination with the highest model joint probability (calibrated ML marginals ×
simulator correlation lift), favouring legs that hit under the same game script.
Usage: python -m research.tonight_aggressive 2026_09_BUF_MIN
"""
from __future__ import annotations

import itertools
import sys

import numpy as np
import pandas as pd
from scipy.stats import nbinom

from nflquant import service
from nflquant.config import settings
from research.strategy_backtest import p_over
from research.strategy_v3 import american, calibrators, joint_sim

LADDER = {
    "receiving_yards": [39.5, 49.5, 59.5, 69.5, 79.5, 89.5, 99.5, 109.5, 124.5],
    "rushing_yards": [39.5, 49.5, 59.5, 69.5, 79.5, 89.5, 99.5],
    "passing_yards": [224.5, 249.5, 274.5, 299.5, 324.5],
    "receptions": [4.5, 5.5, 6.5, 7.5, 8.5],
    "passing_tds": [1.5, 2.5, 3.5],
}
SIM = {"receiving_yards": "rec_yds", "rushing_yards": "rush_yds", "passing_yards": "pass_yds", "receptions": "rec", "passing_tds": "pass_td"}


def legs(gid, sim, names):
    up = pd.read_parquet(settings().lake_dir / "upcoming_player_predictions.parquet")
    up = up[up["game_id"] == gid]
    oos = pd.read_parquet(settings().lake_dir / "player_oos_predictions.parquet", columns=["target", "season", "nb_size"])
    nb = oos[oos["season"] == oos["season"].max()].groupby("target")["nb_size"].median().to_dict()
    cal = calibrators()
    out = []
    for _, r in up.iterrows():
        t, pid = r["target"], r["player_id"]
        if t not in LADDER or pid not in sim.pidx:
            continue
        x = sim.ps[SIM[t]][:, sim.pidx[pid]]
        for ln in LADDER[t]:
            if r["mean"] <= 0 or ln < 0.8 * max(r.get("q50") or r["mean"], 1):  # main line or higher only
                continue
            if t.endswith("yards"):
                raw = p_over({"kind": "yards", "q10": r["q10"], "q25": r["q25"], "q50": r["q50"], "q75": r["q75"], "q90": r["q90"]}, ln)
            else:
                size = nb.get(t, 8.0)
                raw = 1 - nbinom.cdf(int(np.floor(ln)), size, size / (size + r["mean"]))
            if raw < 0.10:   # beyond the calibrated support (tail-extrapolated quantiles): not trusted
                continue
            p = float(cal[t].predict([raw])[0]) if t in cal else raw
            if 0.12 <= p <= 0.62:
                out.append({"leg": f"{names.get(pid, pid)} {t.replace('_', ' ')} o{ln}", "player": pid, "team": r["team"], "p": p,
                            "vec": x > ln, "proj": r["mean"]})
    # anytime TD from the ML TD models, correlation vector from the simulation
    td = up[up["target"].isin(["rushing_tds", "receiving_tds"])].groupby("player_id")["p_ge1"].apply(lambda s: 1 - np.prod(1 - s.fillna(0)))
    for pid, p in td.items():
        if pid in sim.pidx and 0.2 <= p <= 0.6:
            j = sim.pidx[pid]
            out.append({"leg": f"{names.get(pid, pid)} anytime TD", "player": pid + "_td", "team": "", "p": float(p),
                        "vec": (sim.ps["rec_td"][:, j] + sim.ps["rush_td"][:, j]) > 0, "proj": None})
    return out


def price(c):
    vecs = [x["vec"] for x in c]
    joint = np.logical_and.reduce(vecs).mean()
    sp = np.prod([v.mean() for v in vecs])
    lift = float(np.clip(joint / sp, 0.6, 3.0)) if sp else 1.0
    return min(np.prod([x["p"] for x in c]) * lift, 0.95), lift


def main():
    gid = sys.argv[1] if len(sys.argv) > 1 else "2026_09_BUF_MIN"
    sim, pred = joint_sim(gid, n=40000)
    names = service._players().set_index("gsis_id")["display_name"].to_dict()
    L = legs(gid, sim, names)
    best_per_player = {}
    for l in sorted(L, key=lambda l: -l["p"]):
        best_per_player.setdefault(l["player"], []).append(l)
    print("CEILING LADDER (main line and up, calibrated P)")
    for l in sorted(L, key=lambda l: -l["p"]):
        print(f"  {l['leg']:48s} P {l['p']:.3f}  fair {american(l['p'])}")
    pool = [l for l in L if l["p"] >= 0.30]
    pool = sorted(pool, key=lambda l: -l["p"])[:22]
    tiers = [("MAIN-LINE 3-LEG (~+400 to +900)", 3, 0.10, 0.20), ("CEILING 4-LEG (~+900 to +2000)", 4, 0.045, 0.10),
             ("MOONSHOT 5-LEG (~+2000 to +6000)", 5, 0.015, 0.045)]
    for title, k, lo, hi in tiers:
        res = []
        for c in itertools.combinations(pool, k):
            players = [x["player"].replace("_td", "") for x in c]
            if len(set(players)) < k:
                continue
            p, lift = price(c)
            if lo <= p <= hi:
                res.append((p, lift, c))
        res.sort(key=lambda r: -r[0])
        print(f"\n=== {title} ===")
        for p, lift, c in res[:3]:
            print(f"  P={p:.3f} fair {american(p)}  script lift {lift:.2f}")
            for x in c:
                print(f"      {x['leg']:48s} {x['p']:.3f}")


if __name__ == "__main__":
    main()
