"""Empirical test of ceiling-stack templates on real outcomes (2016-2026, out-of-sample).

Each player's "ceiling line" = first ladder line at or above his pregame projection (so each leg
needs an above-projection game). For every game we build every stack of each template from
players who played, then compare the realised hit rate with the product of calibrated
probabilities. lift = realised / product. Templates with lift > 1 are game-script stacks that
hit more often than independent pricing implies.
"""
from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd

from nflquant.config import settings

LAD = {"passing_yards": [174.5, 199.5, 224.5, 249.5, 274.5], "receiving_yards": [29.5, 39.5, 49.5, 59.5, 69.5, 79.5, 89.5],
       "rushing_yards": [29.5, 39.5, 49.5, 59.5, 69.5, 79.5], "receptions": [2.5, 3.5, 4.5, 5.5, 6.5]}


def ceiling_legs() -> pd.DataFrame:
    legs = pd.read_parquet(settings().lake_dir / "strategy_legs.parquet")
    legs = legs[(legs["played"] == 1) & legs["stat"].isin(list(LAD))]
    oos = pd.read_parquet(settings().lake_dir / "player_oos_predictions.parquet", columns=["game_id", "player_id", "target", "pred", "is_home"])
    oos = oos.rename(columns={"target": "stat"})
    legs = legs.merge(oos, on=["game_id", "player_id", "stat"], how="left")
    legs = legs[legs["line"] >= legs["pred"]]
    legs = legs.sort_values("line").groupby(["game_id", "player_id", "stat"]).head(1)  # first line above projection
    return legs[legs["p_cal"].between(0.25, 0.60)]


def study(legs: pd.DataFrame) -> pd.DataFrame:
    recs = []
    for gid, g in legs.groupby("game_id"):
        qb = g[(g["group"] == "QB") & (g["stat"] == "passing_yards")]
        rec = g[g["group"].isin(["WR", "TE"]) & (g["stat"] == "receiving_yards")]
        rb = g[(g["group"] == "RB") & (g["stat"] == "rushing_yards")]
        recn = g[g["group"].isin(["WR", "TE", "RB"]) & (g["stat"] == "receptions")]
        teams = g["team"].unique()

        def add(t, rows):
            recs.append((t, len(rows), float(np.prod([r.p_cal for r in rows])), float(all(r.hit for r in rows))))
        for q in qb.itertuples():
            mates = [r for r in rec.itertuples() if r.team == q.team]
            opp = [r for r in rec.itertuples() if r.team != q.team]
            for m in mates:
                add("QB yds + own WR/TE yds", [q, m])
            for a, b in itertools.combinations(mates, 2):
                add("QB yds + 2 own WR/TE yds", [q, a, b])
            for m, o in itertools.product(mates, opp):
                add("QB yds + own WR + opp WR (bring-back)", [q, m, o])
            for q2 in qb.itertuples():
                if q2.team != q.team and q.Index < q2.Index:
                    add("Both QBs yds (shootout)", [q, q2])
                    for m in mates:
                        add("Both QBs yds + one WR (shootout+)", [q, q2, m])
            for r in rb.itertuples():
                if r.team == q.team:
                    add("QB yds + own RB rush (same-team volume)", [q, r])
                else:
                    add("QB yds + opp RB rush (opposing script)", [q, r])
        for a, b in itertools.combinations(rec.itertuples(), 2):
            add("Two WR/TE yds same team" if a.team == b.team else "Two WR/TE yds opposite teams", [a, b])
        for r in rb.itertuples():
            for m in rec.itertuples():
                add("RB rush + own WR yds" if m.team == r.team else "RB rush + opp WR yds", [r, m])
        for a, b in itertools.combinations(recn.itertuples(), 2):
            add("Two receptions same team" if a.team == b.team else "Two receptions opposite teams", [a, b])
    d = pd.DataFrame(recs, columns=["template", "legs", "pred", "hit"])
    t = d.groupby("template").agg(stacks=("hit", "size"), legs=("legs", "first"), predicted=("pred", "mean"),
                                  realised=("hit", "mean")).reset_index()
    t["lift"] = t["realised"] / t["predicted"]
    t["fair_odds_realised"] = t["realised"].map(lambda p: f"+{100 * (1 - p) / p:.0f}" if 0 < p < 0.5 else (f"{-100 * p / (1 - p):.0f}" if p else "-"))
    return t.sort_values("lift", ascending=False)


if __name__ == "__main__":
    t = study(ceiling_legs())
    print(t.to_string(index=False))
    (settings().lake_dir / "stack_study.json").write_text(t.to_json(orient="records", indent=1))
