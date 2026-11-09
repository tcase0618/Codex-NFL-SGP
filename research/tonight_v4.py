"""Tonight's parlays under the post-audit rules (v4 = v3 + Nov 9 fixes).

Fixes applied: rostered-QB replacement rule, depth-chart player pool, vacated-usage features,
calibrated P(over | plays) legs, hybrid parlay pricing (ML marginals × simulator correlation),
validated-market filter, safety margins (single +5%, parlay +10%, lottery +20%).
Usage: python -m research.tonight_v4 2026_09_BUF_MIN
"""
from __future__ import annotations

import itertools
import sys

import numpy as np
from scipy.stats import norm

from research.strategy_v3 import american, hybrid_price, joint_sim, legs_for


def bet_at(p, margin):
    need = 1 / p * (1 + margin)
    return f"+{(need - 1) * 100:.0f}" if need >= 2 else f"{-100 / (need - 1):.0f}"


def main():
    gid = sys.argv[1] if len(sys.argv) > 1 else "2026_09_BUF_MIN"
    L = legs_for(gid)
    sim, pred = joint_sim(gid)
    from nflquant import service
    names = service._players().set_index("gsis_id")["display_name"].to_dict()
    name2pid = {names.get(pid, pid): pid for pid in sim.pidx}
    m = pred["model"]
    H, A = pred["home"], pred["away"]
    mar = sim.score[:, 0] - sim.score[:, 1]
    tot = sim.score.sum(1)
    print(f"{A} @ {H}: model {A} {m['away_points']:.1f} - {H} {m['home_points']:.1f}, P({H}) {m['p_home']:.3f}, total {m['total_ensemble']:.1f}; "
          f"market {pred['market_benchmark']['spread_line']} / {pred['market_benchmark']['total_line']}")
    print("QBs:", {k: (v or {}).get("name") + f" ({(v or {}).get('source')})" for k, v in pred["projected_qbs"].items()})
    legs = [dict(r) for _, r in L[(L["tier"] != "excluded") & L["p"].between(0.50, 0.90)].iterrows()]
    # game legs: probability centred on the MARKET line (model-vs-market spread edge vanished in 2025-26,
    # total edge never cleared break-even), model σ for spread; correlation vector from the simulation
    mk = pred["market_benchmark"]
    mkt_margin = mk["spread_line"] if mk["spread_line"] is not None else m["margin_ensemble"]
    mkt_total = mk["total_line"] if mk["total_line"] is not None else m["total_ensemble"]
    m = dict(m, margin_ensemble=mkt_margin, total_ensemble=mkt_total, p_home=float(1 - norm.cdf(0, mkt_margin, m["sigma_margin"])))
    game = []
    for side, sgn, pteam in ((A, -1, 1 - m["p_home"]), (H, 1, m["p_home"])):
        game.append({"leg": f"{side} ML", "player": side, "stat": "ml", "line": 0, "p": pteam, "vec": (mar * sgn) > 0, "tier": "game"})
    for ln in (3.5, 6.5):
        p = 1 - norm.cdf(ln, -m["margin_ensemble"], m["sigma_margin"])
        game.append({"leg": f"{A} -{ln}", "player": A, "stat": "spread", "line": ln, "p": p, "vec": -mar > ln, "tier": "game"})
        game.append({"leg": f"{H} +{ln}", "player": H, "stat": "spread", "line": ln, "p": 1 - p, "vec": -mar < ln, "tier": "game"})
    for ln in (40.5, 43.5, 46.5, 49.5):
        p = 1 - norm.cdf(ln, m["total_ensemble"], m["sigma_total"])
        game.append({"leg": f"Over {ln}", "player": "G", "stat": "total", "line": ln, "p": p, "vec": tot > ln, "tier": "game"})
        game.append({"leg": f"Under {ln}", "player": "G", "stat": "total", "line": ln, "p": 1 - p, "vec": tot < ln, "tier": "game"})
    print("\nTOP ELIGIBLE PLAYER LEGS")
    for l in sorted(legs, key=lambda l: -l["p"])[:25]:
        print(f"  [{l['tier']:7s}] {l['leg']:45s} P {l['p']:.3f} fair {american(l['p'])}")

    def price(combo):
        pl = [c for c in combo if c["tier"] != "game"]
        gl = [c for c in combo if c["tier"] == "game"]
        p_pl, _ = hybrid_price(pl, sim, name2pid) if pl else (1.0, 1.0)
        # combine with game legs: marginal product × simulated joint lift across all legs
        from research.strategy_v3 import leg_vector
        vecs = [leg_vector(sim, c, name2pid) for c in pl] + [c["vec"] for c in gl]
        if any(v is None for v in vecs):
            return None
        joint = np.logical_and.reduce(vecs).mean()
        sp = np.prod([v.mean() for v in vecs])
        lift = float(np.clip(joint / sp, 0.6, 2.5)) if sp else 1.0
        return min(np.prod([c["p"] for c in combo]) * lift, 0.97), lift

    def best(pool, k, lo, hi, n=3, key=lambda p, lift: p):
        out = []
        for c in itertools.combinations(pool, k):
            if len({x["player"] for x in c}) < k or sum(x["stat"] in ("ml", "spread") for x in c) > 1 or sum(x["stat"] == "total" for x in c) > 1:
                continue
            r = price(c)
            if r and lo <= r[0] <= hi:
                out.append((key(*r), r[0], r[1], c))
        return sorted(out, key=lambda o: -o[0])[:n]

    top = sorted(legs, key=lambda l: -l["p"])
    pool_safe = [l for l in top if l["p"] >= 0.62][:12] + [g for g in game if g["p"] >= 0.6]
    pool_val = [l for l in top if 0.55 <= l["p"] <= 0.80][:12] + [g for g in game if 0.45 <= g["p"] <= 0.75]
    lot = [l for l in top if 0.30 <= l["p"] <= 0.62][:14] + [g for g in game if 0.2 <= g["p"] <= 0.6]
    lot += [dict(r) for _, r in L[(L["tier"] != "excluded") & L["p"].between(0.30, 0.50)].iterrows()][:10]
    sections = [("SAFE 3-LEG", pool_safe, 3, 0.45, 0.80, 0.10), ("VALUE 4-LEG", pool_val, 4, 0.18, 0.40, 0.10),
                ("LOTTERY 5-LEG", lot, 5, 0.015, 0.10, 0.20)]
    for title, pool, k, lo, hi, margin in sections:
        print(f"\n=== {title} ===")
        key = (lambda p, lift: p * min(lift, 1.3)) if "LOTTERY" not in title else (lambda p, lift: lift * np.sqrt(p))
        for score, p, lift, c in best(pool, k, lo, hi, key=key):
            print(f"  P={p:.3f} fair {american(p)}  corr lift {lift:.2f}  bet at {bet_at(p, margin)} or better")
            for x in c:
                print(f"      [{x['tier']:7s}] {x['leg']:45s} {x['p']:.3f}")


if __name__ == "__main__":
    main()
