"""NFL slot scan: joint-sim 3/5/8 SGP tickets for the given nflverse game ids, logged to ledger/paper_ledger.jsonl.

Rules: leg probabilities from the 50k joint sim with ML agreement (sgp_combos.py); one ticket per size per game;
a leg on at most 2 tickets per game; game PASSED if a projected starting QB is listed Out/Doubtful on ESPN
(Codex-NFL-SGP#1) or the game has already kicked off.
Usage: .venv/bin/python -m research.slot_sgp "2026-11-15 13:00ET" 2026_10_BUF_NYJ 2026_10_CAR_NO ...
"""
import json, os, subprocess, sys, tempfile, urllib.request, unicodedata, re
from datetime import datetime, timezone
from nflquant import service

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
norm = lambda s: re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()).strip()

def espn_event(home, away, kickoff):
    d = kickoff.astimezone(timezone.utc).strftime("%Y%m%d")
    for day in {d, (kickoff.astimezone(timezone.utc)).strftime("%Y%m%d")}:
        j = json.load(urllib.request.urlopen(f"{ESPN}/scoreboard?dates={day}", timeout=20))
        for e in j["events"]:
            ab = {t["homeAway"]: t["team"]["abbreviation"] for t in e["competitions"][0]["competitors"]}
            # nflverse -> ESPN abbreviation aliases (WAS is WSH on ESPN, LA is LAR)
            alias = {"LA": "LAR", "WAS": "WSH"}
            if ab.get("home") in (home, alias.get(home, home)) and ab.get("away") in (away, alias.get(away, away)):
                return e
    return None

INJ = {}

def qb_check(pred, ev):
    flags = []
    if ev is None: return ["no ESPN event match"]
    s = json.load(urllib.request.urlopen(f"{ESPN}/summary?event={ev['id']}", timeout=20))
    inj = {norm(i["athlete"]["displayName"]): i.get("status", "") for t in s.get("injuries", []) for i in t.get("injuries", [])}
    INJ.clear(); INJ.update(inj)
    for side, q in pred["projected_qbs"].items():
        name = (q or {}).get("name")
        if not name: flags.append(f"{side} QB unknown"); continue
        st = inj.get(norm(name), "")
        if st.lower() in ("out", "doubtful", "injured reserve"): flags.append(f"{side} QB {name} {st}")
    return flags

def main():
    slot, gids = sys.argv[1], sys.argv[2:]
    now = datetime.now(timezone.utc); rows = []
    for gid in gids:
        pred = service.game_prediction(gid)
        ko = datetime.fromisoformat(str(pred.get("kickoff") or pred.get("gameday")).replace("Z", "+00:00"))
        if ko.tzinfo is None: ko = ko.replace(tzinfo=timezone.utc)
        ev = espn_event(pred["home"], pred["away"], ko)
        if ev and ev["status"]["type"]["state"] != "pre":
            rows.append({"type": "ticket", "slot": slot, "game_id": gid, "status": "PASS", "note": "already started", "logged_at": now.isoformat()}); print(gid, "SKIP started"); continue
        flags = qb_check(pred, ev)
        qbs = {k: (v or {}).get("name") for k, v in pred["projected_qbs"].items()}
        if flags:
            rows.append({"type": "ticket", "slot": slot, "game_id": gid, "status": "PASS", "note": "QB check: " + "; ".join(flags), "logged_at": now.isoformat()}); print(gid, "PASS", flags); continue
        out = tempfile.mktemp(suffix=".json")
        subprocess.run([sys.executable, "research/sgp_combos.py", gid, out], check=True, capture_output=True, env={**os.environ, "PYTHONPATH": "."})
        combos = json.load(open(out)); used = {}
        print(f"== {pred['away']}@{pred['home']} QBs {qbs} model {pred['model']['away_points']:.1f}-{pred['model']['home_points']:.1f}")
        for k in (3, 5, 8):
            if not any(r.get("game_id") == gid and r.get("name") == f"{k}-leg" for r in rows) and not [c for c in combos if c["k"] == k]:
                pass
            found = False
            for c in [c for c in combos if c["k"] == k]:
                legs = [l.rsplit("|", 1)[0] for l in c["legs"]]
                # every leg's player must be off the ESPN injury report (Out/Doubtful/Questionable) - Codex-NFL-SGP#1
                if any(INJ.get(norm(l.split("|")[0]), "").lower() in ("out", "doubtful", "questionable", "injured reserve") for l in c["legs"]):
                    continue
                if all(used.get(x, 0) < 2 for x in legs):
                    for x in legs: used[x] = used.get(x, 0) + 1
                    L = [{"player": a, "stat": b, "line": float(cc), "p_sim": float(p)} for a, b, cc, p in (l.split("|") for l in c["legs"])]
                    rows.append({"type": "ticket", "slot": slot, "game_id": gid, "matchup": f"{pred['away']}@{pred['home']}", "name": f"{k}-leg",
                                 "logged_at": now.isoformat(), "legs": L, "joint_sim": round(c["joint"], 4), "lift": round(c["lift"], 2),
                                 "fair_decimal": round(1 / c["joint"], 2), "projected_qbs": qbs, "espn_event": ev["id"] if ev else None, "status": "OPEN"})
                    found = True
                    print(f"  {k}-leg P={c['joint']:.3f} fair {1/c['joint']:.2f} lift {c['lift']:.2f} | " + " ; ".join(f"{x['player']} {x['stat']} o{x['line']}" for x in L))
                    break
            if not found:
                rows.append({"type": "ticket", "slot": slot, "game_id": gid, "name": f"{k}-leg", "status": "PASS", "note": "no qualifying combination within exposure cap", "logged_at": now.isoformat()})
                print(f"  {k}-leg PASS (no qualifying combination)")
    if os.environ.get("DRYRUN"): print("DRYRUN: not logged"); return
    os.makedirs("ledger", exist_ok=True)
    open("ledger/paper_ledger.jsonl", "a").write("".join(json.dumps(r) + "\n" for r in rows))
    print("logged", sum(r["status"] == "OPEN" for r in rows), "tickets;", sum(r["status"] == "PASS" for r in rows), "pass")

if __name__ == "__main__":
    main()
