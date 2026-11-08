"""Same-game parlay research for one game from the nflquant joint simulation.

Leg probabilities come from the 50k-sim joint simulation (anchored to the game model);
the ML marginal P(>=line) is shown alongside as an independent check. Joint
probabilities are read from the same simulated games, so correlation is included.
No sportsbook prices are available here: fair odds are printed for comparison
against the offered price. Research only — not validated as profitable.
"""
import itertools, json, sys
import numpy as np, pandas as pd
from scipy.stats import nbinom
from nflquant import service
from nflquant.sim.build import build_spec, anchor
from nflquant.sim.engine import Sim

gid = sys.argv[1] if len(sys.argv) > 1 else "2026_09_TB_CHI"
N, SEED = 50000, 2026
pred = service.game_prediction(gid)
names = service._players().set_index("gsis_id")["display_name"].to_dict()
rows = [{"game_id": gid, "player_id": p["player_id"], "team": p["team"], "group": p["group"], "target": t, "mean": s["mean"] or 0}
        for p in pred["player_projections"] for t, s in p["stats"].items()]
proj = pd.DataFrame(rows)
qb = {pred["home"]: pred["projected_qbs"]["home"]["id"], pred["away"]: pred["projected_qbs"]["away"]["id"]}
spec = build_spec(gid, proj, qb)
anchor(spec, (pred["model"]["home_points"], pred["model"]["away_points"]), n=4000, iters=5)
sim = Sim(spec, N, SEED).run()
H, A = pred["home"], pred["away"]
pts = sim.ts["points"]
mlq = {(p["player_id"], t): s for p in pred["player_projections"] for t, s in p["stats"].items()}

def odds(p):
    return "—" if p <= 0 or p >= 1 else (f"{-100*p/(1-p):+.0f}" if p >= .5 else f"+{100*(1-p)/p:.0f}")

STAT = {"pass_yds": ("passing_yards", [199.5, 224.5, 249.5]), "rush_yds": ("rushing_yards", [29.5, 49.5, 69.5]),
        "rec_yds": ("receiving_yards", [29.5, 49.5, 69.5]), "rec": ("receptions", [2.5, 3.5, 4.5, 5.5]),
        "pass_td": ("passing_tds", [0.5, 1.5]), "any_td": (None, [0.5])}
legs = []
for pid, j in sim.pidx.items():
    for stat, (ml_t, lines) in STAT.items():
        x = (sim.ps["rec_td"][:, j] + sim.ps["rush_td"][:, j]) if stat == "any_td" else sim.ps[stat][:, j]
        if x.mean() < (0.15 if stat == "any_td" else 1.0):
            continue
        for ln in lines:
            ps = float((x > ln).mean())
            if not 0.05 < ps < 0.95:
                continue
            ml = None
            s = mlq.get((pid, ml_t)) if ml_t else None
            if s and s.get("q10") is not None:
                # interpolate ML quantiles (yards) / NB from mean (counts)
                if ml_t.endswith("yards"):
                    qs, vs = [.1, .25, .5, .75, .9], [s["q10"], s["q25"], s["q50"], s["q75"], s["q90"]]
                    ml = float(1 - np.interp(ln, vs, qs, left=0.05, right=0.95))
                else:
                    ml = float((x > ln).mean()) if s["mean"] is None else float(1 - nbinom.cdf(int(ln), 8, 8 / (8 + max(s["mean"], 1e-6))))
            legs.append({"player": names.get(pid, pid), "pid": pid, "stat": stat, "line": ln, "p_sim": ps, "p_ml": ml, "x": x > ln})
tot = pts.sum(1); mar = pts[:, 0] - pts[:, 1]
legs += [{"player": f"{H} ML", "pid": H, "stat": "win", "line": 0, "p_sim": float((mar > 0).mean()), "p_ml": pred["model"]["p_home"], "x": mar > 0},
         {"player": f"{A} ML", "pid": A, "stat": "win", "line": 0, "p_sim": float((mar < 0).mean()), "p_ml": 1 - pred["model"]["p_home"], "x": mar < 0},
         {"player": "Game total", "pid": "G", "stat": "over", "line": 47.5, "p_sim": float((tot > 47.5).mean()), "p_ml": None, "x": tot > 47.5},
         {"player": "Game total", "pid": "G", "stat": "under", "line": 47.5, "p_sim": float((tot < 47.5).mean()), "p_ml": None, "x": tot < 47.5}]
print(f"{A} @ {H}  sims={N} seed={SEED}  sim mean {pts[:,1].mean():.1f}-{pts[:,0].mean():.1f}  model {pred['model']['away_points']:.1f}-{pred['model']['home_points']:.1f}")
print(f"P({H} win) sim {(mar>0).mean():.3f} model {pred['model']['p_home']:.3f}; P(OT) {sim.overtime.mean():.3f}")
print("\nLEG LADDER (agreement = both models >= 0.55)")
for l in sorted(legs, key=lambda l: -l["p_sim"]):
    agree = l["p_ml"] is None or abs(l["p_ml"] - l["p_sim"]) < 0.12
    print(f"  {l['player']:22s} {l['stat']:8s} >{l['line']:<6} sim {l['p_sim']:.3f} ({odds(l['p_sim']):>5})  ml {('%.3f' % l['p_ml']) if l['p_ml'] is not None else '  —  '}  {'' if agree else 'DISAGREE'}")
cand = [l for l in legs if 0.55 <= l["p_sim"] <= 0.88 and (l["p_ml"] is None or (l["p_ml"] >= 0.55 and abs(l["p_ml"] - l["p_sim"]) < 0.12))]
out = []
for k in (3, 4):
    for combo in itertools.combinations(cand, k):
        if len({c["pid"] + c["stat"][:3] for c in combo}) < k or len({c["pid"] for c in combo}) < k:
            continue
        joint = np.logical_and.reduce([c["x"] for c in combo]).mean()
        indep = np.prod([c["p_sim"] for c in combo])
        out.append((k, joint, joint / indep, combo))
print("\nTOP CORRELATED SGPs (highest joint prob; lift = joint / product of legs)")
for k in (3, 4):
    best = sorted([o for o in out if o[0] == k], key=lambda o: (-o[1] * min(o[2], 1.3)))[:5]
    for _, joint, lift, combo in best:
        print(f"  {k}-leg  P={joint:.3f} fair {odds(joint):>5}  lift {lift:.2f}  | " + " + ".join(f"{c['player']} {c['stat']} >{c['line']}" for c in combo))
