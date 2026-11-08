"""Construct pregame simulation specs, anchor them to the game model, and summarize.

Consistency contract
--------------------
* Team volume/efficiency rates: as-of team field matrix (games before kickoff),
  shrunk toward last season and league rates.
* Player usage/efficiency: the player projection model's predicted means for the
  same game (target share = pred targets / Σ team pred targets; catch logit and
  yards-per-reception multipliers from pred receptions / pred targets, etc.).
* Points anchoring: per-offense efficiency scalars are iterated so simulated mean
  points equal the game model's expected points for each team. Player box scores,
  team totals, score distribution and win probability are then all read from the
  same simulated games, so Σ player receiving yards = team passing yards in every
  simulation by construction.
"""
from __future__ import annotations

import time
from functools import lru_cache

import numpy as np
import pandas as pd

from .. import lake
from ..config import settings
from ..metrics.team import load_team_games
from .engine import PLAYER_STATS, TEAM_STATS, GameSpec, Side, Sim, _logit, ot_mode
from .league import league_for

PRIOR_K = {"sack": 200, "scramble": 200, "comp": 300, "int": 600, "ypc_pass": 150, "ypc_rush": 200, "proe": 250}


@lru_cache(maxsize=1)
def _games() -> pd.DataFrame:
    g = lake.load("games")
    g["kickoff"] = pd.to_datetime(g["kickoff"])
    return g.set_index("game_id")


def _ratio(rows: pd.DataFrame, fid: str, kind: str = "n") -> tuple[float, float]:
    return float(rows[fid + "__n"].sum()), float(rows[fid + "__d"].sum())


def team_rates(team: str, season: int, kickoff: pd.Timestamp, side: str, lg) -> dict:
    """As-of offensive (side='off') or defensive-allowed (side='def') rates, shrunk."""
    tg = load_team_games()
    tg = tg[(tg["team"] == team) & tg["points_for"].notna()]
    cur = tg[(tg["season"] == season) & (pd.to_datetime(tg["kickoff"]) < kickoff)]
    prev = tg[tg["season"] == season - 1]
    r = lg.rates

    def est(fid, league_rate, k):
        n1, d1 = _ratio(cur, fid)
        n0, d0 = _ratio(prev, fid)
        prior = (n0 + 0.5 * k * league_rate) / (d0 + 0.5 * k) if d0 > 0 else league_rate
        prior = 0.5 * prior + 0.5 * league_rate
        return (n1 + k * prior) / (d1 + k)

    p = f"{side}."
    out = {
        "sack": _logit(est(p + "sack_rate.all", r["sack"], PRIOR_K["sack"])) - _logit(r["sack"]),
        "scramble": _logit(est(p + "scramble_rate.all", r["scramble"], PRIOR_K["scramble"])) - _logit(r["scramble"]),
        "comp": _logit(est(p + "comp_pct.all", r["complete"], PRIOR_K["comp"])) - _logit(r["complete"]),
        "int": _logit(est(p + "int_rate.all", r["int"], PRIOR_K["int"])) - _logit(r["int"]),
    }
    # yards per completion = Σ pass yards on throws / completions
    def ypc_pass(rows):
        y, _ = _ratio(rows, p + "ypa.all")
        c, _ = _ratio(rows, p + "comp_pct.all")
        return y, c
    y1, c1 = ypc_pass(cur)
    y0, c0 = ypc_pass(prev)
    lg_y = r["ypcomp"]
    prior = (y0 + 0.5 * PRIOR_K["ypc_pass"] * lg_y) / (c0 + 0.5 * PRIOR_K["ypc_pass"]) if c0 else lg_y
    prior = 0.5 * prior + 0.5 * lg_y
    out["comp_yds"] = float(np.clip(((y1 + PRIOR_K["ypc_pass"] * prior) / (c1 + PRIOR_K["ypc_pass"])) / lg_y, 0.8, 1.2))
    out["rush_yds"] = float(np.clip(est(p + "ypc.all", r["ypc"], PRIOR_K["ypc_rush"]) / r["ypc"], 0.8, 1.2))
    if side == "off":
        n1, d1 = _ratio(cur, "off.proe.all")
        n0, d0 = _ratio(prev, "off.proe.all")
        proe = (n1 + 0.5 * n0) / (d1 + 0.5 * d0 + PRIOR_K["proe"]) if (d1 + d0) else 0.0
        out["pass_offset"] = float(proe / 0.24)
    return out


def side_from_projections(team: str, proj: pd.DataFrame, qb_id: str | None, lg) -> Side:
    """Build player allocation from per-player predicted means (columns: player_id, group, target, mean)."""
    p = proj[proj["team"] == team].pivot_table(index=["player_id", "group"], columns="target", values="mean", aggfunc="first").reset_index()
    for c in ("targets", "receptions", "receiving_yards", "carries", "rushing_yards", "attempts", "tackles", "tackles_for_loss",
              "sacks", "def_interceptions", "pass_breakups", "forced_fumbles", "fg_att", "punts", "kick_returns", "punt_returns"):
        if c not in p:
            p[c] = 0.0
    p = p.fillna(0.0)
    r = lg.rates
    qbs = p[p["group"] == "QB"].sort_values("attempts", ascending=False)
    if qb_id is None or qb_id not in set(qbs["player_id"]):
        qb_id = qbs["player_id"].iat[0] if len(qbs) else None
    rec = p[p["group"].isin(["RB", "WR", "TE"]) & (p["targets"] > 0.15)].sort_values("targets", ascending=False).head(12)
    receivers = []
    for _, x in rec.iterrows():
        cr = np.clip((x["receptions"] + 3 * r["complete"]) / (x["targets"] + 3), 0.2, 0.95)
        ypr = (x["receiving_yards"] + 2 * r["ypcomp"]) / (x["receptions"] + 2)
        receivers.append((x["player_id"], float(x["targets"]), float(_logit(cr) - _logit(r["complete"])),
                          float(np.clip(ypr / r["ypcomp"], 0.5, 1.8))))
    rush = p[(p["group"].isin(["RB", "WR"]) & (p["carries"] > 0.15)) | (p["player_id"] == qb_id)].sort_values("carries", ascending=False).head(8)
    rushers = []
    for _, x in rush.iterrows():
        car = float(x["carries"])
        if x["player_id"] == qb_id:
            car = max(0.0, car - 0.55 * max(qbs["attempts"].max() if len(qbs) else 0, 30) * r["scramble"] / max(1 - r["scramble"], 1e-6))
        if car <= 0.1:
            continue
        ypc = (x["rushing_yards"] + 5 * r["ypc"]) / (x["carries"] + 5) if x["carries"] > 0 else r["ypc"]
        rushers.append((x["player_id"], car, float(np.clip(ypc / r["ypc"], 0.5, 1.8))))
    de = p[p["group"].isin(["DL", "LB", "DB"])].sort_values("tackles", ascending=False).head(24)
    defenders = [(x["player_id"], float(x["tackles"]), float(x["tackles_for_loss"]), float(x["sacks"]),
                  float(x["def_interceptions"]), float(x["pass_breakups"]), float(x["forced_fumbles"])) for _, x in de.iterrows()]
    pick = lambda grp, col: (lambda d: d["player_id"].iat[0] if len(d) else None)(p[p["group"] == grp].sort_values(col, ascending=False))
    kr = p.sort_values("kick_returns", ascending=False)
    pr = p.sort_values("punt_returns", ascending=False)
    return Side(team=team, qb=qb_id, receivers=receivers, rushers=rushers, defenders=defenders,
                kicker=pick("K", "fg_att"), punter=pick("P", "punts"),
                kick_returner=kr["player_id"].iat[0] if len(kr) and kr["kick_returns"].iat[0] > 0.2 else None,
                punt_returner=pr["player_id"].iat[0] if len(pr) and pr["punt_returns"].iat[0] > 0.2 else None)


def build_spec(game_id: str, proj: pd.DataFrame, qb: dict | None = None, through_current: bool = True) -> GameSpec:
    g = _games().loc[game_id]
    season, week = int(g["season"]), int(g["week"])
    lg = league_for(season, week if through_current and week > 1 else None)
    sides = []
    for team in (g["home_team"], g["away_team"]):
        s = side_from_projections(team, proj, (qb or {}).get(team), lg)
        off = team_rates(team, season, g["kickoff"], "off", lg)
        s.pass_offset = off.pop("pass_offset", 0.0)
        s.off = off
        s.deff = team_rates(team, season, g["kickoff"], "def", lg)
        sides.append(s)
    return GameSpec(season, str(g["game_type"]), sides[0], sides[1], lg)


def anchor(spec: GameSpec, targets: tuple[float, float], n: int = 3000, iters: int = 5, seed: int = 1) -> list[dict]:
    """Iterate efficiency scalars so simulated mean points match the game model."""
    log = []
    for it in range(iters):
        sim = Sim(spec, n, seed + it).run()
        pts = sim.ts["points"].mean(0)
        log.append({"iter": it, "eff_home": spec.home.eff, "eff_away": spec.away.eff, "sim_home": float(pts[0]),
                    "sim_away": float(pts[1]), "target_home": targets[0], "target_away": targets[1]})
        for side, i in ((spec.home, 0), (spec.away, 1)):
            ratio = max(targets[i], 3.0) / max(pts[i], 3.0)
            side.eff = float(np.clip(side.eff * ratio ** 0.75, 0.55, 1.8))
        if max(abs(pts[0] - targets[0]), abs(pts[1] - targets[1])) < 0.35:
            break
    return log


QUANTS = (10, 25, 50, 75, 90)


def summarize(sim: Sim, spec: GameSpec, names: dict | None = None, hist_players: int = 60) -> dict:
    ts, ps = sim.ts, sim.ps
    h, a = ts["points"][:, 0].astype(float), ts["points"][:, 1].astype(float)
    margin, total = h - a, h + a
    def dist(x):
        return {"mean": float(x.mean()), "median": float(np.median(x)), "sd": float(x.std()),
                **{f"p{q}": float(np.percentile(x, q)) for q in QUANTS}}
    ot = sim.overtime
    team = {}
    for i, side in enumerate((spec.home, spec.away)):
        team[side.team] = {k: dist(ts[k][:, i].astype(float)) for k in TEAM_STATS}
        team[side.team]["points_per_drive"] = float(ts["points"][:, i].sum() / max(1, ts["drives"][:, i].sum()))
        team[side.team]["pass_ratio"] = float(ts["dropbacks"][:, i].sum() / max(1, ts["plays"][:, i].sum()))
        team[side.team]["eff_scalar"] = side.eff
    scores, counts = np.unique(np.stack([h, a], 1), axis=0, return_counts=True)
    top = np.argsort(-counts)[:12]
    out = {
        "n_sims": sim.n, "steps": sim.steps, "home": spec.home.team, "away": spec.away.team,
        "win_prob_home": float(np.mean(margin > 0)), "win_prob_away": float(np.mean(margin < 0)),
        "tie_prob": float(np.mean(margin == 0)), "ot_prob": float(ot.mean()),
        "ot_rules": ot_mode(spec.season, spec.game_type)[0],
        "home_points": dist(h), "away_points": dist(a), "margin": dist(margin), "total": dist(total),
        "margin_hist": _hist(margin, -45, 45), "total_hist": _hist(total, 0, 90),
        "top_scores": [{"home": int(scores[i, 0]), "away": int(scores[i, 1]), "prob": float(counts[i] / sim.n)} for i in top],
        "team": team, "players": [],
        "consistency": {
            "sum_receiving_eq_team_passing": bool(np.all(ps["rec_yds"].sum(1) == ts["pass_yds"].sum(1))),
            "sum_rushing_eq_team_rushing": bool(np.all(ps["rush_yds"].sum(1) == ts["rush_yds"].sum(1))) if len(sim.players) else None,
            "sum_points_check": bool(np.all(sim.score.sum(1) == ts["points"].sum(1))),
        },
    }
    side_of = {}
    for i, side in enumerate((spec.home, spec.away)):
        for pid in [side.qb] + [r[0] for r in side.receivers] + [r[0] for r in side.rushers] + [d[0] for d in side.defenders] + \
                   [side.kicker, side.punter, side.kick_returner, side.punt_returner]:
            if pid:
                side_of.setdefault(pid, side.team)
    for pid, j in sim.pidx.items():
        stats = {}
        for k in PLAYER_STATS:
            x = ps[k][:, j].astype(float)
            if not x.any():
                continue
            d = dist(x)
            d["p_ge1"] = float(np.mean(x >= 1))
            if k in ("pass_yds", "rush_yds", "rec_yds"):
                d["hist"] = _hist(x, float(np.percentile(x, 1)), float(np.percentile(x, 99)), 30)
            else:
                vals, cnt = np.unique(x, return_counts=True)
                d["pmf"] = [{"v": float(v), "p": float(c / sim.n)} for v, c in zip(vals[:25], cnt[:25])]
            stats[k] = d
        if stats:
            out["players"].append({"player_id": pid, "name": (names or {}).get(pid, pid), "team": side_of.get(pid), "stats": stats})
    return out


def _hist(x, lo, hi, bins=45):
    if hi <= lo:
        hi = lo + 1
    c, e = np.histogram(x, bins=bins, range=(lo, hi))
    return {"edges": [float(v) for v in e], "counts": [int(v) for v in c]}


def run_game(game_id: str, proj: pd.DataFrame, targets: tuple[float, float], n: int = 10000, seed: int = 2026,
             qb: dict | None = None, names: dict | None = None, n_anchor: int = 3000) -> dict:
    n = int(min(max(n, 100), 50000))
    t0 = time.time()
    spec = build_spec(game_id, proj, qb)
    log = anchor(spec, targets, n=min(n_anchor, n))
    sim = Sim(spec, n, seed).run()
    res = summarize(sim, spec, names)
    res.update({"game_id": game_id, "seed": seed, "anchor_log": log, "elapsed_s": round(time.time() - t0, 2),
                "anchor_targets": {"home": targets[0], "away": targets[1]},
                "league_window": list(spec.league.windows)})
    return res
