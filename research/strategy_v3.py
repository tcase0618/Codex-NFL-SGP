"""Strategy v3 — rules derived from research/strategy_backtest.py and vacated_usage_test.py.

1. Probability = walk-forward-validated isotonic calibration of the model's P(over | plays)
   (raw model probabilities were 3-7 points too low in the betting zone).
2. Markets: only where the model beat a naive EWMA on played-only outcomes —
   passing yards / passing TDs / receiving yards, plus role-shock spots (teammate usage vacated:
   QB attempts/yards when vac_att >= 0.5, RB carries/rush yards when vac_car >= 0.25).
   Receptions / targets / carries / rushing yards in normal games are excluded (≈0% skill).
3. Parlay price = product of calibrated legs × empirical same-game lift (1.03); the simulator
   joint is not used for pricing (its marginals were less accurate than the ML models).
4. Bet only when the offered price beats fair by the required edge: 5% (single), 10% (parlay).
   Stake = quarter Kelly, capped at 1% of bankroll per ticket.
Usage: python -m research.strategy_v3 2026_09_BUF_MIN [--price "leg-id=+120" ...]
"""
from __future__ import annotations

import itertools
import sys

import numpy as np
import pandas as pd
from scipy.stats import nbinom
from sklearn.isotonic import IsotonicRegression

from nflquant import lake, service
from nflquant.config import settings
from research.strategy_backtest import LINES, p_over

SKILL_MARKETS = {"passing_yards", "passing_tds", "receiving_yards"}
SHOCK_MARKETS = {"attempts": "vac_att", "passing_yards": "vac_att", "passing_tds": "vac_att", "carries": "vac_car", "rushing_yards": "vac_car"}
SHOCK_THR = {"vac_att": 0.5, "vac_car": 0.25}
LINES = {**LINES, "attempts": [24.5, 29.5, 34.5]}
LIFT = 1.03


def calibrators():
    legs = pd.read_parquet(settings().lake_dir / "strategy_legs.parquet")
    legs = legs[legs["played"] == 1]
    return {s: IsotonicRegression(out_of_bounds="clip", y_min=0.005, y_max=0.995).fit(g["p"], g["hit"])
            for s, g in legs.groupby("stat") if len(g) > 2000}


def american(p):
    return f"{-100 * p / (1 - p):+.0f}" if p >= 0.5 else f"+{100 * (1 - p) / p:.0f}"


def legs_for(gid: str) -> pd.DataFrame:
    up = pd.read_parquet(settings().lake_dir / "upcoming_player_predictions.parquet")
    up = up[up["game_id"] == gid]
    if up.empty:
        raise SystemExit(f"no upcoming projections for {gid}")
    d = pd.read_parquet(settings().lake_dir / "player_dataset.parquet", columns=["game_id", "player_id", "vac_tgt", "vac_car", "vac_att"])
    d = d[d["game_id"] == gid].set_index("player_id")
    oos = pd.read_parquet(settings().lake_dir / "player_oos_predictions.parquet", columns=["target", "season", "nb_size"])
    nb = oos[oos["season"] == oos["season"].max()].groupby("target")["nb_size"].median().to_dict()
    names = service._players().set_index("gsis_id")["display_name"].to_dict()
    cal = calibrators()
    rows = []
    for _, r in up.iterrows():
        t = r["target"]
        if t not in LINES:
            continue
        kind = "yards" if t.endswith("yards") else "count"
        vac = d.loc[r["player_id"]] if r["player_id"] in d.index else None
        shock_col = SHOCK_MARKETS.get(t)
        shock = bool(vac is not None and shock_col and vac[shock_col] >= SHOCK_THR[shock_col])
        for line in LINES[t]:
            if kind == "count":
                size = nb.get(t, 8.0)
                raw = 1 - nbinom.cdf(int(np.floor(line)), size, size / (size + max(r["mean"], 1e-6)))
            else:
                raw = p_over({"kind": "yards", "q10": r["q10"], "q25": r["q25"], "q50": r["q50"], "q75": r["q75"], "q90": r["q90"]}, line)
            p = float(cal[t].predict([raw])[0]) if t in cal else float(raw)
            tier = "A-shock" if shock else ("A" if t in SKILL_MARKETS else "excluded")
            rows.append({"leg": f"{names.get(r['player_id'], r['player_id'])} {t} o{line}", "player": names.get(r["player_id"], r["player_id"]),
                         "team": r["team"], "stat": t, "line": line, "p_raw": raw, "p": p, "fair": american(p) if 0 < p < 1 else "—",
                         "tier": tier, "mean": r["mean"]})
    return pd.DataFrame(rows)


def kelly(p, american_price, frac=0.25, cap=0.01):
    dec = 1 + (american_price / 100 if american_price > 0 else 100 / -american_price)
    b = dec - 1
    f = (b * p - (1 - p)) / b
    return max(0.0, min(cap, frac * f)), p * dec - 1


def main():
    gid = sys.argv[1] if len(sys.argv) > 1 else "2026_09_BUF_MIN"
    L = legs_for(gid)
    pred = service.game_prediction(gid)
    print(f"{pred['away']} @ {pred['home']}  model {pred['model']['away_points']:.1f}-{pred['model']['home_points']:.1f}  "
          f"P(home) {pred['model']['p_home']:.3f}  market {pred['market_benchmark']['spread_line']}/{pred['market_benchmark']['total_line']}")
    print("Projected QBs:", {k: (v or {}).get("name") for k, v in pred["projected_qbs"].items()})
    elig = L[(L["tier"] != "excluded") & L["p"].between(0.52, 0.85)].sort_values("p", ascending=False)
    print("\nELIGIBLE LEGS (validated markets, calibrated P(over | plays))")
    for _, r in elig.iterrows():
        print(f"  [{r.tier:7s}] {r.leg:45s} P {r.p:.3f}  fair {r.fair:>6}  (raw {r.p_raw:.3f}, proj {r['mean']:.1f})")
    best = elig.drop_duplicates("player").head(8)
    print("\nPARLAYS (product × 1.03 lift; bet only if book pays >= 'bet at')")
    out = []
    for k in (2, 3):
        for c in itertools.combinations(best.to_dict("records"), k):
            p = min(np.prod([x["p"] for x in c]) * LIFT, 0.95)
            out.append((k, p, c))
    for k in (2, 3):
        for _, p, c in sorted([o for o in out if o[0] == k], key=lambda o: -o[1])[:3]:
            need = 1 / p * 1.10
            need_am = f"+{(need - 1) * 100:.0f}" if need >= 2 else f"{-100 / (need - 1):.0f}"
            print(f"  {k}-leg P={p:.3f} fair {american(p):>6} bet at {need_am:>6} | " + " + ".join(x["leg"] for x in c))
    print("\nSINGLES (bet at = fair + 5%)")
    for _, r in best.head(5).iterrows():
        need = 1 / r.p * 1.05
        need_am = f"+{(need - 1) * 100:.0f}" if need >= 2 else f"{-100 / (need - 1):.0f}"
        print(f"  {r.leg:45s} P {r.p:.3f} fair {r.fair:>6} bet at {need_am}")


if __name__ == "__main__":
    main()
