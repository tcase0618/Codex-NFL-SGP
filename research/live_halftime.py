"""Live halftime parlay builder.

Pulls the live ESPN summary, rebuilds the pregame spec (same usage/efficiency inputs and the
pregame anchoring to the game model), removes inactive players, then simulates ONLY the second
half from the real halftime state (score, second-half kickoff receiver). Each player's
full-game total = actual first-half stats + simulated second half, which is how books settle
live full-game props. Joint probabilities come from the same simulated second halves.

Limits: the second half is simulated from pregame team/player parameters (first-half
performance is not used to update efficiency); the simulator's marginals were less accurate
than the ML models in backtests and live in-game probabilities have no backtest of their own.
Usage: python -m research.live_halftime <espn_event_id> <nflverse_game_id>
"""
from __future__ import annotations

import itertools
import json
import sys

import numpy as np
import pandas as pd
import requests

from nflquant import service
from nflquant.sim.build import anchor, build_spec
from nflquant.sim.engine import Sim

N, SEED = 50000, 909


def american(p):
    return "—" if not 0 < p < 1 else (f"{-100 * p / (1 - p):+.0f}" if p >= 0.5 else f"+{100 * (1 - p) / p:.0f}")


def live_state(event: str) -> dict:
    d = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event}", timeout=30).json()
    c = d["header"]["competitions"][0]
    status = c["status"]
    score = {t["homeAway"]: int(t.get("score") or 0) for t in c["competitors"]}
    abbr = {t["homeAway"]: t["team"]["abbreviation"] for t in c["competitors"]}
    first_drive_team = d["drives"]["previous"][0]["team"]["abbreviation"]
    receiver2h = "home" if first_drive_team == abbr["away"] else "away"
    first_half = {}
    for t in d["boxscore"]["players"]:
        for s in t["statistics"]:
            lab = s["labels"]
            for a in s["athletes"]:
                st = dict(zip(lab, a["stats"]))
                rec = first_half.setdefault(a["athlete"]["id"], {"name": a["athlete"]["displayName"], "team": t["team"]["abbreviation"]})
                if s["name"] == "passing":
                    c_, att = st["C/ATT"].split("/")
                    rec.update(pass_comp=int(c_), pass_att=int(att), pass_yds=int(st["YDS"]), pass_td=int(st["TD"]), pass_int=int(st["INT"]))
                elif s["name"] == "rushing":
                    rec.update(rush_att=int(st["CAR"]), rush_yds=int(st["YDS"]), rush_td=int(st["TD"]))
                elif s["name"] == "receiving":
                    rec.update(rec=int(st["REC"]), rec_yds=int(st["YDS"]), rec_td=int(st["TD"]), targets=int(st.get("TGTS", 0)))
    inactive = [i["athlete"]["id"] for t in d.get("injuries", []) for i in t.get("injuries", [])
                if i.get("details", {}).get("fantasyStatus", {}).get("abbreviation") == "INACTIVE" or i.get("status") == "Out"]
    return {"status": status["type"]["description"], "period": status.get("period"), "score": score, "abbr": abbr,
            "receiver2h": receiver2h, "first_half": first_half, "inactive_espn": inactive, "raw": d}


def main():
    event, gid = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else ("401873050", "2026_09_TB_CHI")
    L = live_state(event)
    if L["status"] != "Halftime":
        print(f"WARNING: game status is '{L['status']}', not Halftime — second-half simulation assumes a half boundary.")
    pl = service._players()
    espn2gsis = {str(e): g for e, g in zip(pl["espn_id"].astype(str), pl["gsis_id"]) if e and e != "nan"}
    names = pl.set_index("gsis_id")["display_name"].to_dict()
    pred = service.game_prediction(gid)
    inactive = {espn2gsis.get(str(i)) for i in L["inactive_espn"]} - {None}
    rows = [{"game_id": gid, "player_id": p["player_id"], "team": p["team"], "group": p["group"], "target": t, "mean": s["mean"] or 0}
            for p in pred["player_projections"] if p["player_id"] not in inactive for t, s in p["stats"].items()]
    proj = pd.DataFrame(rows)
    qb = {pred["home"]: pred["projected_qbs"]["home"]["id"], pred["away"]: pred["projected_qbs"]["away"]["id"]}
    spec = build_spec(gid, proj, qb)
    anchor(spec, (pred["model"]["home_points"], pred["model"]["away_points"]), n=4000, iters=5)
    H, A = pred["home"], pred["away"]
    start = {"quarter": 3, "clock": 900.0, "score": (L["score"]["home"], L["score"]["away"]),
             "receiver": 0 if L["receiver2h"] == "home" else 1}
    sim = Sim(spec, N, SEED).run(start=start)
    fh = {espn2gsis.get(k): v for k, v in L["first_half"].items() if espn2gsis.get(k)}
    final_h, final_a = sim.score[:, 0], sim.score[:, 1]
    mar, tot = final_h - final_a, final_h + final_a
    print(f"LIVE {A} {L['score']['away']} @ {H} {L['score']['home']} ({L['status']}); 2H kickoff to {L['abbr'][L['receiver2h']]}; "
          f"inactive removed: {len(inactive)}")
    print(f"Final projection {A} {final_a.mean():.1f} - {H} {final_h.mean():.1f}; P({H} win) {(mar > 0).mean():.3f}; "
          f"total median {np.median(tot):.0f}; P(OT) {sim.overtime.mean():.3f}")
    legs = []
    def add(label, x):
        p = float(x.mean())
        if 0.05 < p < 0.97:
            legs.append({"leg": label, "p": p, "x": x, "key": label.split(" ")[0] + label.split(" ")[1] if len(label.split()) > 1 else label})
    add(f"{H} ML", mar > 0)
    add(f"{A} ML", mar < 0)
    for ln in (2.5, 3.5, 6.5, 7.5, 10.5, 13.5):
        add(f"{H} -{ln}", mar > ln)
        add(f"{A} +{ln}", mar < ln)
    for ln in np.arange(35.5, 56, 3):
        add(f"Total over {ln}", tot > ln)
        add(f"Total under {ln}", tot < ln)
    add(f"{A} team total over 13.5", final_a > 13.5)
    add(f"{A} team total over 16.5", final_a > 16.5)
    add(f"{H} team total over 23.5", final_h > 23.5)
    add(f"{H} team total over 27.5", final_h > 27.5)
    STAT = {"pass_yds": [174.5, 199.5, 224.5, 249.5, 274.5, 299.5], "rush_yds": [29.5, 39.5, 49.5, 59.5, 69.5], "rec_yds": [39.5, 49.5, 59.5, 69.5, 79.5, 99.5],
            "rec": [2.5, 3.5, 4.5, 5.5, 6.5], "pass_td": [1.5, 2.5], "pass_int": [0.5, 1.5, 3.5]}
    for pid, j in sim.pidx.items():
        name = names.get(pid, pid)
        base = fh.get(pid, {})
        for stat, lines in STAT.items():
            full = sim.ps[stat][:, j] + base.get(stat, 0)
            if sim.ps[stat][:, j].mean() < 0.3 and base.get(stat, 0) == 0:
                continue
            for ln in lines:
                if ln < base.get(stat, 0):
                    continue  # already settled
                add(f"{name} {stat} o{ln} (1H {base.get(stat, 0)})", full > ln)
        td2h = sim.ps["rec_td"][:, j] + sim.ps["rush_td"][:, j]
        had = base.get("rec_td", 0) + base.get("rush_td", 0)
        if had == 0 and td2h.mean() > 0.12:
            add(f"{name} anytime TD (none in 1H)", td2h > 0)
    print("\nLEG LADDER (full-game, settled on final stats)")
    for l in sorted(legs, key=lambda l: -l["p"]):
        print(f"  {l['leg']:52s} P {l['p']:.3f}  fair {american(l['p'])}")
    json.dump({"state": {k: v for k, v in L.items() if k not in ("raw",)},
               "legs": [{"leg": l["leg"], "p": l["p"]} for l in legs]}, open("data/live/halftime_legs.json", "w"), default=str, indent=1)
    np.save("data/live/halftime_matrix.npy", np.stack([l["x"] for l in legs]))
    return legs


if __name__ == "__main__":
    main()
