"""Read-only service layer behind the API. No training happens here."""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from functools import lru_cache

import numpy as np
import pandas as pd

from . import lake, warehouse
from .config import settings
from .metrics import profiles
from .metrics.player import COVERAGE as PCOV, METRIC_INDEX, METRICS, POS_MAP, UNAVAILABLE, UNIVERSAL_GROUPS, catalog, load_metric_games

TIER_NOTE = {
    "OBSERVED": "copied from a cited source row", "DERIVED": "deterministic formula over observed rows",
    "MODELED": "fitted estimate (rating, shrinkage)", "PREDICTED": "pregame forecast", "SYNTHETIC": "illustrative only",
}


def _j(x):
    if isinstance(x, (np.floating, float)):
        return None if np.isnan(x) or np.isinf(x) else float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    return x


def clean(obj):
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return _j(obj)


# ------------------------------------------------------------------ status
def status() -> dict:
    cfg = settings()
    summary = json.loads((cfg.lake_dir / "summary.json").read_text()) if (cfg.lake_dir / "summary.json").exists() else {}
    manifest = json.loads((cfg.raw_dir / "manifest.json").read_text())["assets"] if (cfg.raw_dir / "manifest.json").exists() else {}
    by_release: dict = {}
    for k, v in manifest.items():
        r = by_release.setdefault(v.get("release"), {"assets": 0, "missing": 0, "bytes": 0, "seasons": [], "retrieved_at": None, "license": v.get("license")})
        if v.get("status") == "missing":
            r["missing"] += 1
            continue
        r["assets"] += 1
        r["bytes"] += v.get("bytes") or 0
        if v.get("season"):
            r["seasons"].append(v["season"])
        r["retrieved_at"] = max(filter(None, [r["retrieved_at"], v.get("retrieved_at")]), default=None)
    for r in by_release.values():
        r["seasons"] = [min(r["seasons"]), max(r["seasons"])] if r["seasons"] else None
    meta = _json(cfg.lake_dir / "production_meta.json")
    entities = warehouse.query("SELECT * FROM entity_status ORDER BY entity")
    return clean({"sources": by_release, "lake": summary, "production": meta, "entities": entities,
                  "missing_assets": [k for k, v in manifest.items() if v.get("status") == "missing"],
                  "tiers": TIER_NOTE, "runtime": "deterministic Python; no LLM or paid inference"})


def _json(path):
    return json.loads(path.read_text()) if path.exists() else None


# ------------------------------------------------------------------ teams
def teams() -> dict:
    tg = profiles.load_team_games()
    seasons = sorted(int(s) for s in tg["season"].dropna().unique())
    return {"teams": sorted(tg["team"].unique().tolist()), "seasons": seasons, "categories": _categories(),
            "windows": list(profiles.WINDOWS), "contexts": {k: v[0] for k, v in profiles.CONTEXT.items()},
            "field_count": len(profiles.FIELDS)}


def _categories():
    from .metrics.team import CATEGORIES
    return CATEGORIES


def team_profile(team, season, window="season", context="all", opponent=None, category=None, through_week=None):
    p = profiles.team_profile(team, season, window, context, through_week, opponent, [category] if category else None)
    p["context_fields"] = team_context(team, season, through_week)
    return clean(p)


def team_context(team: str, season: int, through_week: int | None = None) -> list[dict]:
    """Categories 16-19: roster health, venue/rest, trends/regression, opponent strength (game-level fields)."""
    g = profiles.select(season, through_week)
    t = g[g["team"] == team].sort_values("kickoff")
    out = []
    if t.empty:
        return out
    def add(cat, label, value, sample, formula, unit="value"):
        out.append({"category": cat, "label": label, "value": _j(value), "sample": _j(sample), "formula": formula,
                    "unit": unit, "tier": "DERIVED"})
    R = "Roster strength, health and continuity"
    V = "Venue, weather, rest and schedule"
    T = "Performance trends and regression"
    S = "Opponent strength and matchups"
    inj = lake.load("injuries")
    ti = inj[(inj["team"] == team) & (inj["season"] == season)]
    if through_week:
        ti = ti[ti["week"] <= through_week]
    wk = ti.groupby("week")["report_status"].agg(lambda s: (s.isin(["Out", "Doubtful"])).sum())
    add(R, "Avg players Out/Doubtful per week", wk.mean() if len(wk) else None, len(wk), "mean weekly count of Out/Doubtful designations", "players")
    add(R, "Avg players Questionable per week", ti.groupby("week")["report_status"].agg(lambda s: (s == "Questionable").sum()).mean() if len(ti) else None, len(wk), "mean weekly Questionable count", "players")
    gf = pd.read_parquet(settings().lake_dir / "game_features.parquet")
    gs = gf[(gf["season"] == season) & ((gf["home_team"] == team) | (gf["away_team"] == team)) & gf["completed"]]
    qbs = pd.concat([gs.loc[gs.home_team == team, "home_qb_id"], gs.loc[gs.away_team == team, "away_qb_id"]])
    add(R, "Distinct projected starting QBs", qbs.nunique(), len(qbs), "unique pregame-projected starters", "QBs")
    inj_snaps = pd.concat([gs.loc[gs.home_team == team, ["home_inj_off", "home_inj_def"]].set_axis(["o", "d"], axis=1),
                           gs.loc[gs.away_team == team, ["away_inj_off", "away_inj_def"]].set_axis(["o", "d"], axis=1)])
    add(R, "Snap-weighted offensive injury load", inj_snaps["o"].mean() if len(inj_snaps) else None, len(inj_snaps), "Σ prior snap share of Out/Doubtful offensive players, mean per game", "snap units")
    add(R, "Snap-weighted defensive injury load", inj_snaps["d"].mean() if len(inj_snaps) else None, len(inj_snaps), "same for defense", "snap units")
    rest = np.where(t["is_home"] == 1, t["home_rest"], t["away_rest"]).astype(float)
    add(V, "Average rest days", np.nanmean(rest), len(t), "mean scheduled rest days", "days")
    add(V, "Games on short rest (<=5 days)", float(np.nansum(rest <= 5)), len(t), "count", "games")
    add(V, "Average away travel (miles)", t.loc[t["is_home"] == 0, "away_travel_miles"].mean(), int((t["is_home"] == 0).sum()), "great-circle miles, away games", "miles")
    add(V, "Dome/closed-roof games", float(t["roof"].isin(["dome", "closed"]).sum()), len(t), "count", "games")
    add(V, "Home record (win %)", _winpct(t[t["is_home"] == 1]), int((t["is_home"] == 1).sum()), "home wins / home games", "rate")
    add(V, "Away record (win %)", _winpct(t[t["is_home"] == 0]), int((t["is_home"] == 0).sum()), "away wins / away games", "rate")
    pf, pa = t["points_for"].sum(), t["points_against"].sum()
    pyth = pf ** 2.37 / (pf ** 2.37 + pa ** 2.37) if pf + pa > 0 else None
    add(T, "Pythagorean win % (exp 2.37)", pyth, len(t), "PF^2.37 / (PF^2.37 + PA^2.37)", "rate")
    add(T, "Actual − Pythagorean win %", (_winpct(t) - pyth) if pyth is not None else None, len(t), "positive = outperforming point differential (regression candidate)", "rate")
    close = t[(t["points_for"] - t["points_against"]).abs() <= 8]
    add(T, "One-score game win %", _winpct(close), len(close), "wins / games decided by <= 8", "rate")
    to_for = t["def.turnovers_pg.all__n"].sum() - t["off.turnovers_pg.all__n"].sum()
    add(T, "Turnover margin per game", to_for / len(t), len(t), "(takeaways − giveaways) / games; high |value| tends to regress", "per game")
    for k, w in (("last3", 3), ("last5", 5)):
        for fid, lab in (("off.epa_play.all", "Offensive EPA/play"), ("def.epa_play.all", "Defensive EPA/play allowed")):
            recent = t.tail(w)
            v = recent[fid + "__n"].sum() / max(recent[fid + "__d"].sum(), 1)
            s = t[fid + "__n"].sum() / max(t[fid + "__d"].sum(), 1)
            add(T, f"{lab}: {k} − season", v - s, recent[fid + "__d"].sum(), "recent window minus season rate", "EPA/play")
    r = pd.read_parquet(settings().lake_dir / "ratings.parquet")
    wk = int(t["week"].max()) + 1
    rr = r[(r["season"] == season) & (r["week"] == wk)]
    me = rr[rr["team"] == team]
    if len(me):
        for c, lab in (("off_epa", "Opponent-adjusted offensive EPA/play"), ("def_epa", "Opponent-adjusted defensive EPA/play allowed"),
                       ("off_ppd", "Opponent-adjusted points/drive"), ("def_ppd", "Opponent-adjusted points/drive allowed"), ("elo", "Elo (benchmark)")):
            v = float(me[c].iat[0])
            rank = int((rr[c] > v).sum() + 1) if not c.startswith("def") else int((rr[c] < v).sum() + 1)
            out.append({"category": S, "label": lab, "value": v, "rank": rank, "sample": len(t), "tier": "MODELED",
                        "formula": "ridge as-of rating, opponent + home adjusted (see nflquant.ratings)", "unit": "rating"})
        opp = t["opponent"].tolist()
        prior = r[(r["season"] == season) & (r["week"] == wk) & r["team"].isin(opp)]
        sos = prior.set_index("team").reindex(opp)
        add(S, "Strength of schedule (avg opp adj. EPA margin)", float((sos["off_epa"] - sos["def_epa"]).mean()), len(opp),
            "mean over opponents faced of (off rating − def rating), current ratings", "EPA/play")
    return clean(out)


def _winpct(t):
    if not len(t):
        return None
    m = t["points_for"] - t["points_against"]
    return float(((m > 0) + 0.5 * (m == 0)).mean())


def team_trend(team, season, field):
    return clean(profiles.trend(team, season, field))


def team_catalog():
    return profiles.field_catalog()


# ------------------------------------------------------------------ players
@lru_cache(maxsize=1)
def _players() -> pd.DataFrame:
    pl = lake.load("players")
    pl["pos"] = pl["position"].map(POS_MAP)
    return pl


@lru_cache(maxsize=1)
def _player_games() -> pd.DataFrame:
    return lake.load("player_games")


def players(q: str = "", team: str = "", position: str = "", season: int | None = None, limit: int = 60) -> list[dict]:
    pg = _player_games()
    season = season or int(pg["season"].max())
    cur = pg[pg["season"] == season].sort_values("week").groupby("player_id").tail(1)[["player_id", "team", "position", "player_display_name"]]
    games = pg[pg["season"] == season].groupby("player_id").size().rename("games")
    cur = cur.join(games, on="player_id")
    if q:
        cur = cur[cur["player_display_name"].str.contains(q, case=False, na=False)]
    if team:
        cur = cur[cur["team"] == team]
    if position:
        cur = cur[cur["position"].map(lambda p: POS_MAP.get(p, p)) == position]
    cur = cur.sort_values("games", ascending=False).head(limit)
    return clean([{"player_id": r.player_id, "name": r.player_display_name, "team": r.team, "position": r.position,
                   "role": POS_MAP.get(r.position, r.position), "games": int(r.games)} for r in cur.itertuples()])


WINDOWS_P = {"season": None, "last3": 3, "last5": 5, "last10": 10, "career": "career"}


def player_profile(player_id: str, season: int, window: str = "season") -> dict:
    pl = _players()
    ident = pl[pl["gsis_id"] == player_id]
    if ident.empty:
        raise KeyError(player_id)
    ident = ident.iloc[0]
    role = POS_MAP.get(ident["position"], ident["position"])
    pg = _player_games()
    mine = pg[pg["player_id"] == player_id].sort_values(["season", "week"])
    cur_team = mine["team"].iat[-1] if len(mine) else ident["latest_team"]
    pm = load_metric_games()
    rows = pm[pm["player_id"] == player_id]
    if window == "career":
        sel = rows[rows["season"] <= season]
    else:
        sel = rows[rows["season"] == season]
        n = WINDOWS_P.get(window)
        if isinstance(n, int):
            keep = sel.drop_duplicates("game_id").sort_values("kickoff").tail(n)["game_id"]
            sel = sel[sel["game_id"].isin(keep)]
    games_n = sel["game_id"].nunique()
    # peers: same role, same season, for percentiles
    peer_ids = set(pl.loc[pl["pos"] == role, "gsis_id"])
    peers = pm[(pm["season"] == season) & pm["player_id"].isin(peer_ids)]
    pagg = peers.groupby(["player_id", "metric"])[["n", "d"]].sum()
    pgames = peers.groupby("player_id")["game_id"].nunique()
    agg = sel.groupby("metric")[["n", "d"]].sum()
    metrics = []
    for m in METRICS:
        applicable = role in m.positions or (role in ("RB", "WR", "TE") and m.role in ("kick_returner", "punt_returner") and m.key.startswith("r_"))
        if not applicable:
            continue
        if m.key in agg.index:
            n, d = agg.loc[m.key, "n"], agg.loc[m.key, "d"]
            val = n if m.agg == "sum" else (n / d if d else None)
            sample = d
        else:
            val, sample = None, 0
        pct = None
        if val is not None and m.key in pagg.index.get_level_values(1):
            pv = pagg.xs(m.key, level=1)
            if m.agg == "sum":
                pvals = pv["n"]
            else:
                q = pv[pv["d"] >= max(5, np.percentile(pv["d"], 25))] if len(pv) > 8 else pv
                pvals = q["n"] / q["d"]
            if len(pvals) >= 5 and m.direction != 0:
                pct = float(((pvals < val).sum() + 0.5 * (pvals == val).sum()) / len(pvals) * 100)
                if m.direction < 0:
                    pct = 100 - pct
            elif len(pvals) >= 5:
                pct = float(((pvals < val).sum() + 0.5 * (pvals == val).sum()) / len(pvals) * 100)
        covered = _covered(m.source, season)
        metrics.append({"id": m.key, "label": m.label, "group": m.group, "group_label": UNIVERSAL_GROUPS[m.group],
                        "unit": m.unit, "direction": m.direction, "source": m.source, "coverage": PCOV[m.source],
                        "formula": m.formula, "aggregation": m.agg, "value": _j(val), "sample": _j(sample),
                        "percentile": pct, "status": "observed" if val is not None else ("not_covered" if not covered else "no_sample"),
                        "tier": "DERIVED"})
    box = _box(mine, season, window)
    universal = _universal(ident, mine, sel, role, season, metrics)
    unavailable = [{"label": l, "reason": r} for l, pos, r in UNAVAILABLE if pos == role]
    ngs = _ngs(player_id, season)
    trend = _trend_series(rows, mine, role, season)
    proj = upcoming_projection(player_id)
    return clean({"player_id": player_id, "name": ident["display_name"], "position": ident["position"], "role": role,
                  "team": cur_team, "season": season, "window": window, "games": int(games_n), "identity": universal["identity"],
                  "universal": universal["fields"], "metrics": metrics, "box": box, "unavailable": unavailable,
                  "ngs": ngs, "trend": trend, "projection": proj,
                  "data_reliability": _reliability(games_n, metrics)})


def _covered(src, season):
    lo, hi = {"pbp": (2006, 9999), "participation": (2016, 2025), "ftn": (2022, 9999), "participation+ftn": (2022, 2025)}[src]
    return lo <= season <= hi


BOX = ["completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions", "sacks_suffered", "carries",
       "rushing_yards", "rushing_tds", "targets", "receptions", "receiving_yards", "receiving_tds", "def_tackles_solo",
       "def_tackle_assists", "def_tackles_for_loss", "def_sacks", "def_qb_hits", "def_interceptions", "def_pass_defended",
       "def_fumbles_forced", "fg_made", "fg_att", "pat_made", "pt_att", "kickoff_returns", "kickoff_return_yards",
       "punt_returns", "punt_return_yards", "offense_snaps", "offense_pct", "defense_snaps", "defense_pct", "st_snaps"]


def _box(mine, season, window):
    s = mine[mine["season"] == season] if window != "career" else mine[mine["season"] <= season]
    cols = [c for c in BOX if c in s.columns]
    games = [{"week": int(r["week"]), "season": int(r["season"]), "opponent": r["opponent_team"], "team": r["team"],
              **{c: _j(r[c]) for c in cols}} for _, r in s.iterrows()]
    totals = {c: _j(pd.to_numeric(s[c], errors="coerce").sum()) for c in cols if not c.endswith("_pct")}
    seasons = mine.groupby("season")[[c for c in cols if not c.endswith("_pct")]].sum(numeric_only=True)
    seasons["games"] = mine.groupby("season").size()
    return {"games": games, "totals": totals, "career_by_season": [{"season": int(k), **{c: _j(v) for c, v in r.items()}} for k, r in seasons.iterrows()]}


def _universal(ident, mine, sel, role, season, metrics):
    by = {m["id"]: m for m in metrics}
    s = mine[mine["season"] == season]
    age = None
    if isinstance(ident.get("birth_date"), str):
        try:
            age = (pd.Timestamp(f"{season}-09-01") - pd.Timestamp(ident["birth_date"])).days / 365.25
        except Exception:
            age = None
    ht, wt = ident.get("height"), ident.get("weight")
    identity = {"height_in": _j(ht), "weight_lb": _j(wt), "bmi": _j(703 * wt / ht ** 2) if ht and wt else None, "age": age,
                "college": ident.get("college_name"), "draft_year": _j(ident.get("draft_year")), "draft_round": _j(ident.get("draft_round")),
                "draft_pick": _j(ident.get("draft_pick")), "rookie_season": _j(ident.get("rookie_season")),
                "experience": _j(ident.get("years_of_experience")), "career_seasons": int(mine["season"].nunique()),
                "career_games": int(len(mine)), "headshot": ident.get("headshot")}
    def f(group, label, value, sample=None, formula="", unit=""):
        return {"group": group, "group_label": UNIVERSAL_GROUPS[group], "label": label, "value": _j(value), "sample": _j(sample), "formula": formula, "unit": unit}
    n = len(s)
    offp = pd.to_numeric(s.get("offense_pct"), errors="coerce") if "offense_pct" in s else pd.Series(dtype=float)
    defp = pd.to_numeric(s.get("defense_pct"), errors="coerce") if "defense_pct" in s else pd.Series(dtype=float)
    sn = lambda c: float(pd.to_numeric(s[c], errors="coerce").sum()) if c in s else 0.0
    touches = sn("carries") + sn("receptions")
    scrimmage = sn("rushing_yards") + sn("receiving_yards")
    offense = role in ("QB", "RB", "WR", "TE")
    defense = role in ("EDGE", "DT", "LB", "CB", "S")
    fields = [f("B", "Games with a recorded stat", n, n, "player-game rows", "games")]
    if offense or role == "OL":
        fields.append(f("B", "Offensive snap share", offp.mean() if offp.notna().any() else None, offp.notna().sum(), "mean PFR offense %", "rate"))
    if defense:
        fields.append(f("B", "Defensive snap share", defp.mean() if defp.notna().any() else None, defp.notna().sum(), "mean PFR defense %", "rate"))
    fields.append(f("B", "Special-teams snaps", sn("st_snaps"), n, "Σ PFR special-teams snaps", "snaps"))
    if offense:
        fields += [
            f("B", "Opportunities per game", (sn("attempts") + sn("sacks_suffered") + sn("carries") + sn("targets")) / n if n else None, n, "(dropbacks + carries + targets) / games", "per game"),
            f("C", "Yards from scrimmage", scrimmage, n, "rush + receiving yards", "yards"),
            f("C", "Total touchdowns", sn("rushing_tds") + sn("receiving_tds") + sn("passing_tds"), n, "rush + rec + pass TD", "TD"),
            f("C", "Fantasy points (PPR)", sn("fantasy_points_ppr"), n, "nflverse PPR scoring", "points"),
            f("D", "Total EPA (pass + rush + rec)", sn("passing_epa") + sn("rushing_epa") + sn("receiving_epa"), n, "Σ nflverse EPA columns", "EPA"),
            f("D", "EPA per opportunity", (sn("passing_epa") + sn("rushing_epa") + sn("receiving_epa")) / max(1, sn("attempts") + sn("sacks_suffered") + sn("carries") + sn("targets")), n, "total EPA / opportunities", "EPA"),
            f("E", "20+ yard plays", sn("passing_20") + sn("rushing_20") + sn("receiving_20"), n, "nflverse 20+ yard play counts", "plays"),
            f("F", "Fumbles lost", sn("rushing_fumbles_lost") + sn("receiving_fumbles_lost") + sn("sack_fumbles_lost"), n, "Σ fumbles lost", "fumbles"),
        ]
        if role != "QB":
            fields += [f("B", "Target share", s["target_share"].mean() if "target_share" in s and n else None, n, "mean weekly share of team targets", "rate"),
                       f("B", "Air-yards share", s["air_yards_share"].mean() if "air_yards_share" in s and n else None, n, "mean weekly share of team air yards", "rate"),
                       f("C", "Touches", touches, n, "carries + receptions", "touches")]
        else:
            fields.append(f("F", "Interceptions thrown", sn("passing_interceptions"), n, "INT", "INT"))
    if defense:
        tk = sn("def_tackles_solo") + sn("def_tackle_assists")
        fields += [
            f("C", "Tackles (solo + assists)", tk, n, "box tackles", "tackles"),
            f("C", "Sacks", sn("def_sacks"), n, "box sacks", "sacks"),
            f("C", "QB hits", sn("def_qb_hits"), n, "box QB hits", "hits"),
            f("E", "Takeaways (INT + forced fumbles)", sn("def_interceptions") + sn("def_fumbles_forced"), n, "INT + FF", "plays"),
            f("D", "Tackles per game", tk / n if n else None, n, "tackles / games", "per game"),
            f("D", "Havoc plays (TFL + sacks + PBU + FF + INT)", sn("def_tackles_for_loss") + sn("def_sacks") + sn("def_pass_defended") + sn("def_fumbles_forced") + sn("def_interceptions"), n, "Σ disruptive credits", "plays"),
        ]
    if role == "K":
        fields += [f("C", "FG made / attempted", sn("fg_made"), sn("fg_att"), "box FG", "FG"), f("C", "XP made", sn("pat_made"), n, "box XP", "XP")]
    if role == "P":
        fields += [f("C", "Punts", sn("pt_att"), n, "box punts", "punts"), f("D", "Net punt yards per punt", sn("pt_net_yards") / max(1, sn("pt_att")), n, "box net yards / punts", "yards")]
    fields.append(f("F", "Penalties", sn("penalties"), n, "accepted penalties", "penalties"))
    ep = (pd.to_numeric(s.get("passing_epa"), errors="coerce").fillna(0) + pd.to_numeric(s.get("rushing_epa"), errors="coerce").fillna(0)
          + pd.to_numeric(s.get("receiving_epa"), errors="coerce").fillna(0)) if n else pd.Series(dtype=float)
    if n >= 3 and offense:
        fields.append(f("I", "Game EPA standard deviation", ep.std(), n, "SD of per-game total EPA", "EPA"))
        fields.append(f("I", "Coefficient of variation (scrimmage yds)", (pd.to_numeric(s.get("rushing_yards"), errors="coerce").fillna(0) + pd.to_numeric(s.get("receiving_yards"), errors="coerce").fillna(0)).pipe(lambda x: x.std() / x.mean() if x.mean() else None), n, "SD / mean of per-game scrimmage yards", "ratio"))
        slope = np.polyfit(np.arange(n), ep.to_numpy(), 1)[0]
        fields.append(f("I", "EPA trend slope (per game)", slope, n, "OLS slope of per-game EPA over the season", "EPA/game"))
        for k in (3, 5, 10):
            fields.append(f("I", f"Rolling {k}-game EPA per game", ep.tail(k).mean(), min(k, n), f"mean EPA of last {k} games", "EPA"))
    opp = pd.read_parquet(settings().lake_dir / "ratings.parquet")
    if n:
        o = opp.merge(s[["season", "week", "opponent_team"]].rename(columns={"opponent_team": "team"}), on=["season", "week", "team"])
        col = {"QB": "def_pass", "WR": "def_pass", "TE": "def_pass", "RB": "def_rush"}.get(role, "off_epa")
        fields.append(f("H", "Avg opponent rating faced (as-of)", o[col].mean() if len(o) else None, len(o),
                        f"mean pregame opponent {col} (higher = weaker defense for def_*)", "rating"))
        if role in ("QB", "WR", "TE", "RB"):
            per = (ep.sum() / max(1, sn("attempts") + sn("sacks_suffered") + sn("carries") + sn("targets")))
            fields.append(f("H", "Opponent-adjusted EPA per opportunity", per - (o[col].mean() if len(o) else 0), n,
                            "EPA/opportunity − mean opponent defensive rating faced", "EPA"))
    # J: replacement & uncertainty
    if role in ("QB", "RB", "WR", "TE"):
        rep = {"QB": -0.08, "RB": -0.12, "WR": 0.0, "TE": 0.0}[role]
        opps = sn("attempts") + sn("sacks_suffered") + sn("carries") + sn("targets")
        fields.append(f("J", "EPA over replacement", ep.sum() - rep * opps, n, f"total EPA − {rep} × opportunities (replacement EPA/opp by role)", "EPA"))
        fields.append(f("J", "Points over replacement (≈EPA)", ep.sum() - rep * opps, n, "EPA is in expected points", "points"))
        sd = ep.std() / np.sqrt(n) if n > 1 else None
        fields.append(f("J", "Std. error of EPA per game", sd, n, "SD / √games", "EPA"))
    return {"identity": identity, "fields": fields}


def _ngs(pid, season):
    out = {}
    for name in ("ngs_passing", "ngs_receiving", "ngs_rushing"):
        try:
            d = lake.load(name)
        except Exception:
            continue
        r = d[(d["player_gsis_id"] == pid) & (d["season"] == season) & (d["week"] == 0)]
        if len(r):
            out[name] = {k: _j(v) for k, v in r.iloc[0].items() if k not in ("player_gsis_id",) and not isinstance(v, str) or k in ("team_abbr", "player_position")}
    return out


def _trend_series(rows, mine, role, season):
    keys = {"QB": ["qb_epa_db", "qb_cpoe", "qb_adot"], "RB": ["ru_epa", "ru_success", "re_targets"],
            "WR": ["re_targets", "re_ypt", "re_epa"], "TE": ["re_targets", "re_ypt", "re_epa"]}.get(role, ["df_tackles", "dn_pass_epa"])
    s = rows[rows["season"] == season]
    out = {}
    for k in keys:
        g = s[s["metric"] == k].sort_values("kickoff")
        m = METRIC_INDEX[k]
        out[k] = {"label": m.label, "points": [{"week": int(r.week), "opponent": r.opponent,
                                                  "value": _j(r.n if m.agg == "sum" else (r.n / r.d if r.d else None)), "sample": _j(r.d)}
                                                 for r in g.itertuples()]}
    return out


def _reliability(games, metrics):
    obs = sum(1 for m in metrics if m["status"] == "observed")
    return {"games": int(games), "metrics_observed": obs, "metrics_applicable": len(metrics),
            "grade": "high" if games >= 8 else "medium" if games >= 4 else "low",
            "note": "Reliability reflects sample size and source coverage, not a performance grade."}


def upcoming_projection(player_id: str) -> dict | None:
    up = _player_up()
    if up.empty:
        return None
    r = up[up["player_id"] == player_id]
    if r.empty:
        return None
    gid = r.sort_values("game_id")["game_id"].iat[0]
    g = _games().set_index("game_id").loc[gid]
    r = r[r["game_id"] == gid]
    stats = {x["target"]: {k: _j(x.get(k)) for k in ("mean", "q10", "q25", "q50", "q75", "q90", "p_ge1")} for _, x in r.iterrows()}
    return {"game_id": gid, "matchup": f"{g['away_team']} @ {g['home_team']}", "kickoff": str(g["kickoff"]), "stats": stats,
            "tier": "PREDICTED", "model": "player-models-1.0 production fit"}


def player_catalog():
    return catalog()


# ------------------------------------------------------------------ games & predictions
@lru_cache(maxsize=1)
def _games():
    g = lake.load("games")
    g["kickoff"] = pd.to_datetime(g["kickoff"])
    return g


@lru_cache(maxsize=1)
def _oos():
    return pd.read_parquet(settings().lake_dir / "game_oos_predictions.parquet")


@lru_cache(maxsize=1)
def _upcoming():
    p = settings().lake_dir / "upcoming_game_predictions.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


@lru_cache(maxsize=1)
def _player_oos():
    return pd.read_parquet(settings().lake_dir / "player_oos_predictions.parquet")


@lru_cache(maxsize=1)
def _player_up():
    p = settings().lake_dir / "upcoming_player_predictions.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def schedule(season: int | None = None, week: int | None = None) -> dict:
    g = _games()
    if season is None:
        up = g[~g["completed"]]
        season = int(up["season"].min()) if len(up) else int(g["season"].max())
    s = g[g["season"] == season]
    if week is None:
        up = s[~s["completed"]]
        week = int(up["week"].min()) if len(up) else int(s["week"].max())
    w = s[s["week"] == week].sort_values("kickoff")
    oos, upc = _oos().set_index("game_id"), _upcoming()
    upc = upc.set_index("game_id") if len(upc) else upc
    games = []
    for _, r in w.iterrows():
        src = None
        if r["game_id"] in oos.index:
            p, src = oos.loc[r["game_id"]], "walk_forward_pregame"
        elif len(upc) and r["game_id"] in upc.index:
            p, src = upc.loc[r["game_id"]], "production_pregame"
        else:
            p = None
        games.append({"game_id": r["game_id"], "kickoff": r["kickoff"], "home": r["home_team"], "away": r["away_team"],
                      "completed": bool(r["completed"]), "home_score": _j(r["home_score"]), "away_score": _j(r["away_score"]),
                      "prediction_source": src,
                      "p_home": _j(p["p_home"]) if p is not None else None,
                      "home_points": _j(p["home_points"]) if p is not None else None,
                      "away_points": _j(p["away_points"]) if p is not None else None,
                      "margin": _j(p["margin_ensemble"]) if p is not None else None,
                      "total": _j(p["total_ensemble"]) if p is not None else None})
    weeks = sorted(int(x) for x in s["week"].unique())
    return clean({"season": season, "week": week, "weeks": weeks, "seasons": sorted(int(x) for x in g["season"].unique()), "games": games})


def game_prediction(game_id: str) -> dict:
    g = _games().set_index("game_id").loc[game_id]
    oos, upc = _oos(), _upcoming()
    if game_id in set(oos["game_id"]):
        p = oos[oos["game_id"] == game_id].iloc[0]
        source = "walk_forward_pregame"
        pp = _player_oos()
        pp = pp[pp["game_id"] == game_id].rename(columns={"pred": "mean"})
    elif len(upc) and game_id in set(upc["game_id"]):
        p = upc[upc["game_id"] == game_id].iloc[0]
        source = "production_pregame"
        pp = _player_up()
        pp = pp[pp["game_id"] == game_id]
    else:
        raise KeyError(f"no pregame prediction for {game_id}")
    names = _players().set_index("gsis_id")["display_name"].to_dict()
    proj = []
    for (pid, team, grp), r in pp.groupby(["player_id", "team", "group"]):
        stats = {}
        for _, x in r.iterrows():
            stats[x["target"]] = {k: _j(x.get(k)) for k in ("mean", "q10", "q25", "q50", "q75", "q90", "p_ge1", "y") if k in x}
        proj.append({"player_id": pid, "name": names.get(pid, pid), "team": team, "group": grp, "stats": stats})
    gf = pd.read_parquet(settings().lake_dir / "game_features.parquet").set_index("game_id")
    feats = gf.loc[game_id] if game_id in gf.index else None
    explain = None
    if source == "production_pregame":
        try:
            from .models.game import shap_explain
            from .production import game_fit
            explain = shap_explain(game_fit(), gf.loc[[game_id]].assign(season_c=lambda x: x["season"] - 2015))[:12]
        except Exception:
            explain = None
    inj = lake.load("injuries")
    week_inj = inj[(inj["season"] == g["season"]) & (inj["week"] == g["week"]) & inj["team"].isin([g["home_team"], g["away_team"]])
                   & inj["report_status"].notna()]
    out = {
        "game_id": game_id, "season": int(g["season"]), "week": int(g["week"]), "kickoff": g["kickoff"],
        "home": g["home_team"], "away": g["away_team"], "completed": bool(g["completed"]), "source": source,
        "tier": "PREDICTED", "information_cutoff": f"games before {g['kickoff']}",
        "actual": {"home": _j(g["home_score"]), "away": _j(g["away_score"])} if g["completed"] else None,
        "model": {k: _j(p[k]) for k in ("p_home", "p_home_raw", "home_points", "away_points", "margin_ensemble", "total_ensemble",
                                         "sigma_margin", "sigma_total", "margin_p10", "margin_p25", "margin_p75", "margin_p90",
                                         "total_p10", "total_p25", "total_p75", "total_p90", "margin_ridge", "margin_xgb",
                                         "margin_elo", "total_ridge", "total_xgb") if k in p},
        "market_benchmark": {"spread_line": _j(g["spread_line"]), "total_line": _j(g["total_line"]),
                             "note": "nflverse schedule line; displayed for comparison only, never a model input"},
        "projected_qbs": {"home": {"id": feats["home_qb_id"], "name": names.get(feats["home_qb_id"]), "source": feats["home_qb_source"],
                                   "epa_db_prior": _j(feats["home_qb_epa"])} if feats is not None else None,
                          "away": {"id": feats["away_qb_id"], "name": names.get(feats["away_qb_id"]), "source": feats["away_qb_source"],
                                   "epa_db_prior": _j(feats["away_qb_epa"])} if feats is not None else None},
        "features": {k: _j(feats[k]) for k in ("net_epa", "net_pass", "net_rush", "net_qb", "elo_diff", "rest_diff", "away_travel_miles",
                                               "net_inj_off", "net_inj_def", "temp_f", "wind_mph", "dome", "div_game")} if feats is not None else None,
        "explanations": explain, "player_projections": proj,
        "injury_report": [{"team": r["team"], "player": r["full_name"], "position": r["position"], "status": r["report_status"],
                           "practice": r["practice_status"]} for _, r in week_inj.iterrows()],
    }
    return clean(out)


_SIM_CACHE: "OrderedDict[str, dict]" = OrderedDict()


def simulate(game_id: str, n: int = 10000, seed: int = 2026, remove: list[str] | None = None) -> dict:
    """Seeded Monte Carlo for one game. ``remove`` = player IDs ruled out (injury scenario)."""
    from .sim.build import run_game
    n = {1000: 1000, 10000: 10000, 50000: 50000}.get(int(n), min(max(int(n), 500), 50000))
    key = hashlib.sha256(json.dumps([game_id, n, seed, sorted(remove or [])]).encode()).hexdigest()[:16]
    if key in _SIM_CACHE:
        return _SIM_CACHE[key]
    pred = game_prediction(game_id)
    rows = []
    for p in pred["player_projections"]:
        if remove and p["player_id"] in remove:
            continue
        for t, s in p["stats"].items():
            rows.append({"game_id": game_id, "player_id": p["player_id"], "team": p["team"], "group": p["group"], "target": t, "mean": s["mean"] or 0.0})
    proj = pd.DataFrame(rows)
    targets = (pred["model"]["home_points"], pred["model"]["away_points"])
    qb = {pred["home"]: (pred["projected_qbs"]["home"] or {}).get("id"), pred["away"]: (pred["projected_qbs"]["away"] or {}).get("id")}
    if remove:
        qb = {k: (None if v in remove else v) for k, v in qb.items()}
        targets = _injury_adjusted_targets(pred, remove, targets)
    names = _players().set_index("gsis_id")["display_name"].to_dict()
    res = run_game(game_id, proj, targets, n=n, seed=seed, qb=qb, names=names)
    res["scenario"] = {"removed": remove or [], "anchor_targets": {"home": targets[0], "away": targets[1]},
                       "note": "Removed players' usage is redistributed by re-normalizing the remaining projected shares."}
    res["tier"] = "PREDICTED (simulated)"
    res["actual"] = pred["actual"]
    res = clean(res)
    with warehouse.connect() as con:
        con.execute("INSERT OR REPLACE INTO simulation_runs VALUES (?,?,?,?,?,?,?,?)", (
            key, game_id, n, seed, pred["source"], warehouse.now(),
            json.dumps({k: res[k] for k in ("win_prob_home", "tie_prob", "ot_prob", "home_points", "away_points", "scenario")}, default=float),
            res["elapsed_s"]))
    _SIM_CACHE[key] = res
    while len(_SIM_CACHE) > 24:
        _SIM_CACHE.popitem(last=False)
    return res


def _injury_adjusted_targets(pred, remove, targets):
    """Shift expected points when a projected QB is removed, using the game model's QB coefficient."""
    h, a = targets
    for side, key in (("home", 0), ("away", 1)):
        q = pred["projected_qbs"][side]
        if q and q.get("id") in remove:
            delta = (q.get("epa_db_prior") or 0) - (-0.08)  # replacement-level prior
            pts = 35.0 * delta  # ≈ 35 dropbacks × EPA/db ≈ points
            if key == 0:
                h -= pts
            else:
                a -= pts
    return (max(h, 3.0), max(a, 3.0))


# ------------------------------------------------------------------ matchups & audit
def matchup(game_id: str) -> dict:
    from .models.matchup import game_matchups
    g = _games().set_index("game_id").loc[game_id]
    season, week = int(g["season"]), int(g["week"])
    m = game_matchups(game_id)
    key = ["off.epa_play.all", "off.pass_epa.all", "off.rush_epa.all", "off.success.all", "off.pressure_rate.all",
           "off.sack_rate.all", "off.explosive.all", "off.points_per_drive.all", "off.rz_td_rate.all", "off.third_conv.all",
           "off.pass_epa.man", "off.pass_epa.zone", "off.pass_epa.pressure", "off.rush_epa.light_box", "off.rush_epa.loaded_box",
           "def.epa_play.all", "def.pass_epa.all", "def.rush_epa.all", "def.pressure_rate.all", "def.man_rate.all",
           "def.blitz_rate.all", "def.two_high_rate.all", "def.sack_rate.all", "def.explosive.all", "def.points_per_drive.all",
           "st.fg_pct.all", "st.punt_net.all", "st.st_epa.all"]
    sides = {}
    for t in (g["home_team"], g["away_team"]):
        prof = profiles.team_profile(t, season, "season", "all", week - 1 if week > 1 else None)
        idx = {f["id"]: f for f in prof["fields"]}
        sides[t] = {k: idx.get(k) for k in key}
    m["profiles"] = sides
    m["through_week"] = week - 1
    m["unavailable"] = ["WR vs specific CB shadow matchups (no public coverage assignment)",
                        "Edge vs individual tackle win rates (no public rusher-blocker attribution)"]
    return clean(m)


def audit() -> dict:
    cfg = settings()
    out = {k: _json(cfg.lake_dir / f"{k}_validation.json") for k in ("game", "player", "matchup", "sim")}
    out["versions"] = warehouse.query("SELECT * FROM model_versions ORDER BY created_at DESC LIMIT 60")
    out["production"] = _json(cfg.lake_dir / "production_meta.json")
    if out["player"]:
        out["player"]["targets"] = {k: {kk: vv for kk, vv in v.items()} for k, v in out["player"]["targets"].items()}
    return clean(out)
