"""Full walk-forward backtest of the current strategy (2012-2026).

Strategy per game (one slip each):
  A. Ceiling QB stack: QB passing yards + 2 of his own WR/TE receiving yards, each at the first
     ladder line at/above the player's projection; best combo by calibrated product × template lift.
  B. ATTD 2+1 split: two scorers from one team + one from the other, highest calibrated product × lift.
Everything is out-of-sample:
  * player models retrained each season on prior seasons only (current features incl. vacated usage),
  * isotonic calibration fit on prior seasons' predictions only,
  * template correlation lifts estimated from prior seasons only.
No historical sportsbook prices exist, so returns are shown under explicit pricing assumptions.
"""
from __future__ import annotations

import itertools
import json
import sys

import numpy as np
import pandas as pd
from scipy.stats import nbinom
from sklearn.isotonic import IsotonicRegression

from nflquant import lake
from nflquant.config import settings
from nflquant.models.player import QUANTILES, _X, _xgb, nb_dispersion, rows_for

TARGETS = ["passing_yards", "receiving_yards", "rushing_tds", "receiving_tds"]
LAD = {"passing_yards": [174.5, 199.5, 224.5, 249.5, 274.5, 299.5], "receiving_yards": [29.5, 39.5, 49.5, 59.5, 69.5, 79.5, 89.5]}
OUT = settings().lake_dir / "full_backtest"


def walk_forward(first=2012):
    d = pd.read_parquet(settings().lake_dir / "player_dataset.parquet")
    d = d[d["game_id"].isin(set(lake.load("games").query("completed").game_id)) & (d["season"] >= 2010)]
    out = []
    for t in TARGETS:
        rows = rows_for(d, t)
        kind = "yards" if t.endswith("yards") else "count"
        for S in range(first, int(d["season"].max()) + 1):
            tr, te = rows[rows["season"] < S], rows[rows["season"] == S]
            m = _xgb(kind).fit(_X(tr), tr[f"y_{t}"])
            mu = np.clip(m.predict(_X(te)), 0, None)
            r = pd.DataFrame({"game_id": te["game_id"].to_numpy(), "season": S, "player_id": te["player_id"].to_numpy(),
                              "team": te["team"].to_numpy(), "group": te["group"].to_numpy(), "target": t, "pred": mu,
                              "y": te[f"y_{t}"].to_numpy(), "played": te["played"].to_numpy()})
            if kind == "yards":
                q = np.sort(_xgb(kind, quantile=True).fit(_X(tr), tr[f"y_{t}"]).predict(_X(te)), axis=1)
                for i, a in enumerate(QUANTILES):
                    r[f"q{int(a * 100)}"] = q[:, i]
            else:
                size = nb_dispersion(np.clip(m.predict(_X(tr)), 0, None), tr[f"y_{t}"].to_numpy())
                r["p_ge1"] = 1 - nbinom.pmf(0, size, size / (size + np.maximum(mu, 1e-9)))
            out.append(r)
            print(t, S, len(te), flush=True)
    o = pd.concat(out, ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    o.to_parquet(OUT / "oos.parquet", index=False)
    return o


def p_over_q(q, line):
    qs = np.array([0.1, 0.25, 0.5, 0.75, 0.9])
    q = np.maximum.accumulate(np.asarray(q, float))
    if line <= q[0]:
        return 0.9
    if line >= q[-1]:
        return 0.1
    return float(1 - np.interp(line, q, qs))


def ceiling_legs(o):
    rows = []
    for t, lad in LAD.items():
        sub = o[o["target"] == t]
        for r in sub.itertuples():
            line = next((x for x in lad if x >= r.pred), None)
            if line is None:
                continue
            rows.append({"game_id": r.game_id, "season": r.season, "player_id": r.player_id, "team": r.team, "group": r.group,
                         "stat": t, "line": line, "p_raw": p_over_q([r.q10, r.q25, r.q50, r.q75, r.q90], line),
                         "hit": float(r.y > line), "played": r.played})
    return pd.DataFrame(rows)


def td_legs(o):
    td = o[o["target"].isin(["rushing_tds", "receiving_tds"])].pivot_table(
        index=["game_id", "season", "player_id", "team", "group", "played"], columns="target", values=["p_ge1", "y"], aggfunc="first").fillna(0)
    td.columns = [f"{a}_{b}" for a, b in td.columns]
    td = td.reset_index()
    return pd.DataFrame({"game_id": td["game_id"], "season": td["season"], "player_id": td["player_id"], "team": td["team"],
                         "group": td["group"], "stat": "anytime_td", "line": 0.5, "played": td["played"],
                         "p_raw": 1 - (1 - td["p_ge1_rushing_tds"]) * (1 - td["p_ge1_receiving_tds"]),
                         "hit": ((td["y_rushing_tds"] + td["y_receiving_tds"]) > 0).astype(float)})


def calibrate(legs):
    legs = legs.copy()
    legs["p"] = np.nan
    for stat, g in legs.groupby("stat"):
        for s in sorted(g["season"].unique()):
            tr = g[(g["season"] < s) & (g["played"] == 1)]
            idx = g.index[g["season"] == s]
            if len(tr) < 1000:
                continue  # no calibration history yet -> season used for warm-up only
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(tr["p_raw"], tr["hit"])
            legs.loc[idx, "p"] = iso.predict(legs.loc[idx, "p_raw"])
    return legs


def candidate_slips(legs):
    """All template stacks per game from players who played (books void inactive players)."""
    L = legs[(legs["played"] == 1) & legs["p"].notna()]
    recs = []
    for gid, g in L.groupby("game_id"):
        season = int(g["season"].iat[0])
        qb = g[(g["stat"] == "passing_yards") & (g["group"] == "QB")]
        rec = g[(g["stat"] == "receiving_yards") & g["group"].isin(["WR", "TE"]) & g["p"].between(0.25, 0.65)]
        for q in qb.itertuples():
            mates = [r for r in rec.itertuples() if r.team == q.team]
            for a, b in itertools.combinations(mates, 2):
                recs.append((gid, season, "A_ceiling_qb_stack", q.p * a.p * b.p, q.hit * a.hit * b.hit,
                             f"QB {q.player_id} o{q.line} + {a.player_id} o{a.line} + {b.player_id} o{b.line}"))
        td = g[(g["stat"] == "anytime_td") & (g["p"] >= 0.15)]
        teams = td["team"].unique()
        if len(teams) == 2:
            for big, small in ((teams[0], teams[1]), (teams[1], teams[0])):
                two = td[td["team"] == big].nlargest(4, "p").itertuples()
                one = td[td["team"] == small].nlargest(3, "p")
                for a, b in itertools.combinations(list(two), 2):
                    for c in one.itertuples():
                        recs.append((gid, season, "B_attd_2plus1", a.p * b.p * c.p, a.hit * b.hit * c.hit,
                                     f"TD {a.player_id} + {b.player_id} + {c.player_id}"))
    return pd.DataFrame(recs, columns=["game_id", "season", "strategy", "p_prod", "hit", "slip"])


def run(rebuild: bool = True):
    o = walk_forward() if rebuild else pd.read_parquet(OUT / "oos.parquet")
    legs = calibrate(pd.concat([ceiling_legs(o), td_legs(o)], ignore_index=True))
    cand = candidate_slips(legs)
    # walk-forward template lift from prior seasons' candidate stacks
    lift = {}
    for strat, g in cand.groupby("strategy"):
        for s in sorted(g["season"].unique()):
            prior = g[g["season"] < s]
            lift[(strat, s)] = float(np.clip(prior["hit"].sum() / prior["p_prod"].sum(), 0.7, 2.0)) if len(prior) > 500 else 1.0
    cand["lift"] = [lift[(a, b)] for a, b in zip(cand["strategy"], cand["season"])]
    cand["p_model"] = (cand["p_prod"] * cand["lift"]).clip(upper=0.95)
    picks = cand.sort_values("p_model", ascending=False).groupby(["game_id", "strategy"]).head(1)
    picks = picks[picks["season"] >= 2013]  # 2012 = calibration warm-up
    # returns under pricing assumptions (stake 1 unit per slip)
    VIG = 1.045  # per-leg book margin (≈ -110 two-way markets)
    picks["dec_indep"] = 1 / (picks["p_prod"] * VIG ** 3)          # book prices legs independently, no correlation adjustment
    picks["dec_fair"] = 1 / picks["p_model"]                        # paid exactly at our fair odds
    picks["dec_sgp"] = 1 / (picks["p_model"] * VIG ** 3)            # book prices the correlation fully + margin
    res = {"coverage": "2013-2026 graded (2012 calibration warm-up); 2006-2011 not testable out-of-sample", "strategies": {}}
    for strat, g in picks.groupby("strategy"):
        r = {"slips": int(len(g)), "hits": int(g["hit"].sum()), "hit_rate": float(g["hit"].mean()),
             "predicted": float(g["p_model"].mean()), "avg_fair_odds": float((1 / g["p_model"]).mean() * 100 - 100)}
        for k in ("dec_indep", "dec_sgp", "dec_fair"):
            ret = (g["hit"] * g[k]).sum() - len(g)
            r[f"roi_{k[4:]}"] = float(ret / len(g))
        r["by_season"] = g.groupby("season").agg(slips=("hit", "size"), hits=("hit", "sum"), pred=("p_model", "mean"),
                                                 hit_rate=("hit", "mean")).reset_index().to_dict("records")
        b = pd.cut(g["p_model"], [0, 0.06, 0.09, 0.12, 0.16, 0.3, 1])
        r["calibration"] = g.groupby(b, observed=True).agg(n=("hit", "size"), pred=("p_model", "mean"), actual=("hit", "mean")).reset_index().astype({"p_model": str}).to_dict("records")
        res["strategies"][strat] = r
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=float))
    picks.to_parquet(OUT / "picks.parquet", index=False)
    return res


if __name__ == "__main__":
    r = run(rebuild="--reuse" not in sys.argv)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk not in ("by_season", "calibration")} for k, v in r["strategies"].items()}, indent=1))
