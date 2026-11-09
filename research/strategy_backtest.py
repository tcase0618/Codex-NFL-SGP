"""In-depth walk-forward backtest of the parlay strategy.

Uses only out-of-sample player predictions (each season predicted by models trained on
earlier seasons). For every projected player, stat and alt line we compute the model's
P(over), then replay the strategy's ticket construction game by game and compare
predicted vs realised hit rates.

Legs whose player recorded no stat are treated as VOID (books void inactive players);
an 'all-in' stress view counts them as losses.

No historical sportsbook prices are available, so ROI against a book cannot be measured;
the report states break-even prices and calibration instead.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import nbinom
from sklearn.isotonic import IsotonicRegression

from nflquant.config import settings

LINES = {
    "receptions": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5],
    "receiving_yards": [9.5, 19.5, 29.5, 39.5, 49.5, 59.5, 69.5, 79.5, 89.5],
    "rushing_yards": [9.5, 19.5, 29.5, 39.5, 49.5, 59.5, 69.5, 79.5],
    "passing_yards": [174.5, 199.5, 224.5, 249.5, 274.5],
    "passing_tds": [0.5, 1.5, 2.5],
    "targets": [2.5, 3.5, 4.5, 5.5, 6.5],
    "carries": [7.5, 9.5, 12.5, 14.5, 17.5],
}
QS = [0.1, 0.25, 0.5, 0.75, 0.9]
OUT = settings().lake_dir / "strategy_backtest.json"


def p_over(row, line):
    if row["kind"] == "count":
        r = row["nb_size"]
        return 1 - nbinom.cdf(int(np.floor(line)), r, r / (r + max(row["pred"], 1e-6)))
    q = np.array([row["q10"], row["q25"], row["q50"], row["q75"], row["q90"]], float)
    q = np.maximum.accumulate(q)
    # linear interpolation inside [q10, q90]; tails extrapolated with the outer slopes, clipped
    if line <= q[0]:
        slope = (q[1] - q[0]) / 0.15 if q[1] > q[0] else 1e9
        return float(np.clip(1 - (0.1 - (q[0] - line) / slope), 0.9, 0.99))
    if line >= q[-1]:
        slope = (q[-1] - q[-2]) / 0.15 if q[-1] > q[-2] else 1e9
        return float(np.clip(1 - (0.9 + (line - q[-1]) / slope), 0.01, 0.1))
    return float(1 - np.interp(line, q, QS))


def build_legs(o: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for target, lines in LINES.items():
        sub = o[o["target"] == target]
        for line in lines:
            if sub.empty:
                continue
            if sub["kind"].iat[0] == "count":
                r = sub["nb_size"].to_numpy()
                mu = np.maximum(sub["pred"].to_numpy(), 1e-6)
                p = 1 - nbinom.cdf(int(np.floor(line)), r, r / (r + mu))
            else:
                p = np.array([p_over(x, line) for x in sub[["kind", "q10", "q25", "q50", "q75", "q90", "pred", "nb_size"]].to_dict("records")])
            r_b = sub["nb_size"].fillna(8).to_numpy() if sub["kind"].iat[0] == "count" else None
            base = sub["baseline"].to_numpy()
            if r_b is not None:
                pb = 1 - nbinom.cdf(int(np.floor(line)), r_b, r_b / (r_b + np.maximum(base, 1e-6)))
            else:  # baseline for yards: same spread as the model's interval, centred on EWMA
                spread = (sub["q90"] - sub["q10"]).clip(lower=1).to_numpy() / 2.563
                from scipy.stats import norm
                pb = 1 - norm.cdf(line, base, spread)
            rows.append(pd.DataFrame({"game_id": sub["game_id"].to_numpy(), "season": sub["season"].to_numpy(),
                                      "player_id": sub["player_id"].to_numpy(), "team": sub["team"].to_numpy(),
                                      "group": sub["group"].to_numpy(), "stat": target, "line": line, "p": p,
                                      "p_base": pb, "hit": (sub["y"].to_numpy() > line).astype(float),
                                      "played": sub["played"].to_numpy()}))
    # anytime TD from rushing/receiving TD P(>=1), assuming independence of the two
    td = o[o["target"].isin(["rushing_tds", "receiving_tds"])].pivot_table(
        index=["game_id", "season", "player_id", "team", "group", "played"], columns="target", values=["p_ge1", "y"], aggfunc="first")
    td.columns = [f"{a}_{b}" for a, b in td.columns]
    td = td.reset_index().fillna(0)
    rows.append(pd.DataFrame({"game_id": td["game_id"], "season": td["season"], "player_id": td["player_id"], "team": td["team"],
                              "group": td["group"], "stat": "anytime_td", "line": 0.5,
                              "p": 1 - (1 - td.get("p_ge1_rushing_tds", 0)) * (1 - td.get("p_ge1_receiving_tds", 0)),
                              "p_base": np.nan, "hit": ((td.get("y_rushing_tds", 0) + td.get("y_receiving_tds", 0)) > 0.5).astype(float),
                              "played": td["played"]}))
    return pd.concat(rows, ignore_index=True)


def recalibrate_walk_forward(legs: pd.DataFrame) -> pd.Series:
    """Isotonic recalibration per stat, fit only on seasons before the scored season."""
    out = pd.Series(np.nan, index=legs.index)
    for stat, g in legs[legs["played"] == 1].groupby("stat"):
        for s in sorted(g["season"].unique()):
            tr = g[g["season"] < s]
            idx = legs.index[(legs["stat"] == stat) & (legs["season"] == s)]
            if len(tr) < 2000:
                out[idx] = legs.loc[idx, "p"]
                continue
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.005, y_max=0.995).fit(tr["p"], tr["hit"])
            out[idx] = iso.predict(legs.loc[idx, "p"])
    return out


def calib(df, pcol, bins=np.arange(0.0, 1.0001, 0.1)):
    d = df.dropna(subset=[pcol])
    b = pd.cut(d[pcol], bins, include_lowest=True)
    t = d.groupby(b, observed=True).agg(n=("hit", "size"), pred=(pcol, "mean"), actual=("hit", "mean")).reset_index()
    t["bin"] = t[pcol].astype(str)
    return t[["bin", "n", "pred", "actual"]]


def brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def logloss(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


# ------------------------------------------------------------------ ticket construction rules
def pick(cands: pd.DataFrame, k: int, lo: float, hi: float, pcol: str, rule: str = "top", max_per_team: int | None = None):
    c = cands[(cands[pcol] >= lo) & (cands[pcol] <= hi)].sort_values(pcol, ascending=False)
    chosen, players, teams = [], set(), {}
    for _, r in c.iterrows():
        if r["player_id"] in players:
            continue
        if max_per_team and teams.get(r["team"], 0) >= max_per_team:
            continue
        chosen.append(r)
        players.add(r["player_id"])
        teams[r["team"]] = teams.get(r["team"], 0) + 1
        if len(chosen) == k:
            return pd.DataFrame(chosen)
    return None


STRATEGIES = {
    # name: (k, lo, hi, prob column, max legs per team)   — v1 = what we bet on Sunday
    "v1_safe3":   (3, 0.55, 0.88, "p", None),
    "v1_mid3":    (3, 0.60, 0.80, "p", None),
    "v1_upside4": (4, 0.50, 0.65, "p", None),
    "v2_safe3":   (3, 0.55, 0.88, "p_cal", 2),
    "v2_mid3":    (3, 0.60, 0.80, "p_cal", 2),
    "v2_upside4": (4, 0.50, 0.65, "p_cal", 2),
}


def replay(legs: pd.DataFrame, lift_table: dict | None = None) -> pd.DataFrame:
    """Build each strategy's ticket for every game; grade it."""
    allowed = legs[legs["stat"].isin(["receptions", "receiving_yards", "rushing_yards", "passing_yards", "passing_tds", "anytime_td"])]
    rows = []
    for gid, g in allowed.groupby("game_id"):
        season = int(g["season"].iat[0])
        for name, (k, lo, hi, pcol, mpt) in STRATEGIES.items():
            t = pick(g, k, lo, hi, pcol, max_per_team=mpt)
            if t is None:
                continue
            graded = t[t["played"] == 1]
            void = len(graded) < len(t)
            p_prod = float(np.prod(graded[pcol])) if len(graded) else np.nan
            same = int(t["team"].value_counts().max())
            lift = (lift_table or {}).get(season, {}).get(f"{len(graded)}|{same}", 1.0)
            rows.append({"game_id": gid, "season": season, "strategy": name, "legs": len(t), "graded_legs": len(graded),
                         "void_legs": len(t) - len(graded), "max_same_team": same, "p_prod": p_prod,
                         "p_adj": min(p_prod * lift, 0.99) if len(graded) else np.nan,
                         "hit": float(graded["hit"].all()) if len(graded) else np.nan,
                         "hit_allin": float((t["hit"] * t["played"]).all()),
                         "desc": " + ".join(f"{r.stat}>{r.line}" for r in t.itertuples())})
    return pd.DataFrame(rows)


def lift_walk_forward(tix: pd.DataFrame) -> dict:
    """Empirical correlation lift = realised hit rate / product of leg probabilities,
    by (#graded legs, max legs from one team), fit on seasons before each season."""
    out = {}
    v = tix.dropna(subset=["hit"])
    for s in sorted(v["season"].unique()):
        prior = v[v["season"] < s]
        tab = {}
        for key, g in prior.groupby(["graded_legs", "max_same_team"]):
            if len(g) >= 150 and g["p_prod"].sum() > 0:
                tab[f"{key[0]}|{key[1]}"] = float(np.clip(g["hit"].sum() / g["p_prod"].sum(), 0.6, 1.5))
        out[int(s)] = tab
    return out


def pairwise_lift(legs: pd.DataFrame, n_games: int = 1500, seed: int = 0) -> pd.DataFrame:
    """Realised P(A and B) / (P(A) P(B)) for leg pairs in the same game, by relationship type."""
    rng = np.random.default_rng(seed)
    pool = legs[(legs["played"] == 1) & legs["p"].between(0.4, 0.9) &
                legs["stat"].isin(["receptions", "receiving_yards", "rushing_yards", "passing_yards", "passing_tds", "anytime_td"])]
    games = rng.choice(pool["game_id"].unique(), size=min(n_games, pool["game_id"].nunique()), replace=False)
    recs = []
    for gid in games:
        g = pool[pool["game_id"] == gid]
        g = g.loc[g.groupby(["player_id", "stat"])["p"].idxmax()]  # one line per player-stat
        for a, b in itertools.combinations(g.to_dict("records"), 2):
            if a["player_id"] == b["player_id"]:
                rel = "same player"
            elif a["team"] == b["team"]:
                rel = ("QB+pass-catcher" if {"QB"} & {a["group"], b["group"]} and {"WR", "TE", "RB"} & {a["group"], b["group"]}
                       else "same team, other")
            else:
                rel = "opposing teams"
            recs.append((rel, a["p"] * b["p"], a["hit"] * b["hit"]))
    d = pd.DataFrame(recs, columns=["relationship", "p_prod", "both"])
    t = d.groupby("relationship").agg(pairs=("both", "size"), predicted=("p_prod", "mean"), realised=("both", "mean")).reset_index()
    t["lift"] = t["realised"] / t["predicted"]
    return t


def run() -> dict:
    o = pd.read_parquet(settings().lake_dir / "player_oos_predictions.parquet")
    legs = build_legs(o)
    legs["p_cal"] = recalibrate_walk_forward(legs)
    played = legs[legs["played"] == 1]
    res = {"n_leg_rows": int(len(legs)), "n_games": int(legs["game_id"].nunique()),
           "seasons": [int(legs.season.min()), int(legs.season.max())]}
    # 1. leg calibration
    res["leg_overall"] = {"brier_model": brier(played["p"], played["hit"]), "brier_recal": brier(played["p_cal"], played["hit"]),
                          "logloss_model": logloss(played["p"], played["hit"]), "logloss_recal": logloss(played["p_cal"], played["hit"])}
    nb = played.dropna(subset=["p_base"])
    res["leg_vs_baseline"] = {"brier_model": brier(nb["p"], nb["hit"]), "brier_ewma_baseline": brier(nb["p_base"], nb["hit"]), "n": int(len(nb))}
    res["leg_calibration_raw"] = calib(played, "p").to_dict("records")
    res["leg_calibration_recal"] = calib(played, "p_cal").to_dict("records")
    zone = played[played["p"].between(0.55, 0.88)]
    res["strategy_zone_by_stat"] = (zone.groupby("stat").agg(n=("hit", "size"), pred=("p", "mean"), pred_recal=("p_cal", "mean"),
                                                            actual=("hit", "mean")).reset_index().to_dict("records"))
    res["strategy_zone_by_stat_line"] = (zone.groupby(["stat", "line"]).agg(n=("hit", "size"), pred=("p", "mean"),
                                                                            actual=("hit", "mean")).reset_index()
                                         .query("n >= 300").to_dict("records"))
    res["strategy_zone_by_group"] = (zone.groupby("group").agg(n=("hit", "size"), pred=("p", "mean"), actual=("hit", "mean"))
                                     .reset_index().to_dict("records"))
    res["void_rate_in_zone"] = float(1 - legs[legs["p"].between(0.55, 0.88)]["played"].mean())
    # 2. correlation
    res["pairwise_lift"] = pairwise_lift(legs).to_dict("records")
    # 3. ticket replay
    tix = replay(legs)
    lift = lift_walk_forward(tix[tix["strategy"].str.startswith("v2")])
    tix = replay(legs, lift)
    summary = []
    for name, g in tix.groupby("strategy"):
        v = g.dropna(subset=["hit"])
        summary.append({"strategy": name, "tickets": int(len(g)), "graded": int(len(v)),
                        "void_any_leg": float((g["void_legs"] > 0).mean()), "pred_prod": float(v["p_prod"].mean()),
                        "pred_adj": float(v["p_adj"].mean()), "actual": float(v["hit"].mean()),
                        "actual_allin": float(g["hit_allin"].mean()),
                        "brier_prod": brier(v["p_prod"], v["hit"]), "brier_adj": brier(v["p_adj"], v["hit"]),
                        "fair_odds_actual": _american(float(v["hit"].mean()))})
    res["tickets"] = summary
    res["tickets_by_season"] = (tix.dropna(subset=["hit"]).groupby(["strategy", "season"])
                                .agg(n=("hit", "size"), pred=("p_prod", "mean"), actual=("hit", "mean")).reset_index()
                                .to_dict("records"))
    res["lift_table_latest"] = lift[max(lift)]
    # 4. break-even analysis: what a book must pay for each ticket type to be +EV at realised rates
    res["break_even"] = [{"strategy": s["strategy"], "realised_hit": s["actual"], "break_even_american": _american(s["actual"]),
                          "break_even_decimal": 1 / s["actual"] if s["actual"] else None} for s in summary]
    OUT.write_text(json.dumps(res, indent=1, default=float))
    legs.to_parquet(settings().lake_dir / "strategy_legs.parquet", index=False)
    tix.to_parquet(settings().lake_dir / "strategy_tickets.parquet", index=False)
    return res


def _american(p):
    if not p or p <= 0 or p >= 1:
        return None
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


if __name__ == "__main__":
    r = run()
    print(json.dumps({k: v for k, v in r.items() if k not in ("tickets_by_season", "strategy_zone_by_stat_line")}, indent=1, default=float))
