"""Team profile queries over the team-game field matrix.

Windows: season, last3, last5, last8, career-style multi-season ranges.
Game-level context splits (venue, weather, rest, opponent) are applied by selecting
team-game rows, so every value keeps its own exposure-weighted sample.

Shrinkage (hierarchical, empirical Bayes):
    binary fields   beta-binomial: shrunk = (n + k·m) / (d + k), k = m(1−m)/τ² − 1
    numeric fields  normal-normal: shrunk = m + B·(x − m), B = τ² / (τ² + σ²/d)
τ² is the method-of-moments between-team variance (floored), m the league rate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .team import COVERAGE, FIELDS, FIELD_INDEX, load_team_games

WINDOWS = {"season": None, "last3": 3, "last5": 5, "last8": 8}
CONTEXT = {
    "all": ("All games", lambda g: np.ones(len(g), bool)),
    "home": ("Home", lambda g: g["is_home"].to_numpy() == 1),
    "away": ("Away", lambda g: g["is_home"].to_numpy() == 0),
    "dome": ("Dome / closed roof", lambda g: g["roof"].isin(["dome", "closed"]).to_numpy()),
    "outdoor": ("Outdoors / open", lambda g: g["roof"].isin(["outdoors", "open"]).to_numpy()),
    "cold": ("Temperature <= 40F (outdoor)", lambda g: (pd.to_numeric(g["temp"], errors="coerce") <= 40).to_numpy()),
    "windy": ("Wind >= 15 mph (outdoor)", lambda g: (pd.to_numeric(g["wind"], errors="coerce") >= 15).to_numpy()),
    "short_rest": ("Rest <= 5 days", lambda g: _rest(g) <= 5),
    "long_rest": ("Rest >= 10 days", lambda g: _rest(g) >= 10),
    "division": ("Divisional games", lambda g: g["div_game"].to_numpy() == 1),
}


def _rest(g: pd.DataFrame) -> np.ndarray:
    return np.where(g["is_home"] == 1, g["home_rest"], g["away_rest"]).astype(float)


def select(season: int, through_week: int | None = None, regular_only: bool = False,
           before: str | None = None) -> pd.DataFrame:
    tg = load_team_games()
    g = tg[tg["season"] == season]
    g = g[g["points_for"].notna()]
    if regular_only:
        g = g[g["game_type"] == "REG"]
    if through_week is not None:
        g = g[g["week"] <= through_week]
    if before is not None:
        g = g[pd.to_datetime(g["kickoff"]) < pd.Timestamp(before)]
    return g


def window_rows(g: pd.DataFrame, window: str, context: str = "all", opponent: str | None = None) -> pd.DataFrame:
    g = g[CONTEXT[context][1](g)]
    if opponent:
        g = g[g["opponent"] == opponent]
    n = WINDOWS[window]
    if n:
        g = g.sort_values("kickoff").groupby("team", group_keys=False).tail(n)
    return g


def league_table(g: pd.DataFrame, field_ids: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Per-team Σn, Σd, Σq and game counts for the requested fields."""
    ids = field_ids or [f.id for f in FIELDS]
    grp = g.groupby("team")
    cols_n = [i + "__n" for i in ids]
    cols_d = [i + "__d" for i in ids]
    cols_q = [i + "__q" for i in ids if i + "__q" in g.columns]
    N = grp[cols_n].sum()
    N.columns = ids
    D = grp[cols_d].sum()
    D.columns = ids
    Q = grp[cols_q].sum() if cols_q else pd.DataFrame(index=N.index)
    Q.columns = [c[:-3] for c in cols_q]
    games = grp.size()
    return {"N": N, "D": D, "Q": Q, "games": games}


def shrink(n: np.ndarray, d: np.ndarray, q: np.ndarray | None, binary: bool):
    """Return (league mean, shrunk values, posterior sd, raw) for one field across teams."""
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.where(d > 0, n / d, np.nan)
        ok = d > 0
        if ok.sum() < 3:
            return np.nan, raw, np.full_like(raw, np.nan), raw
        m = n[ok].sum() / d[ok].sum()
        if binary:
            sampling = np.where(ok, m * (1 - m) / d, np.nan)
            sigma2 = np.full_like(raw, m * (1 - m))
        else:
            within = np.where(ok, q / d - raw ** 2, np.nan) if q is not None else np.full_like(raw, np.nan)
            pooled = np.nansum(within * d) / d[ok].sum() if q is not None else np.nanvar(raw)
            sigma2 = np.full_like(raw, max(pooled, 1e-12))
            sampling = sigma2 / d
        tau2 = max(np.nanvar(raw[ok]) - np.nanmean(sampling[ok]), 1e-4 * max(np.nanvar(raw[ok]), 1e-12))
        B = tau2 / (tau2 + sampling)
        shrunk = np.where(ok, m + B * (raw - m), np.nan)
        post_sd = np.sqrt(np.where(ok, 1.0 / (1.0 / tau2 + d / sigma2), np.nan))
    return m, shrunk, post_sd, raw


def percentile(values: pd.Series, direction: int) -> pd.Series:
    """Directional mid-rank percentile (100 = best). Neutral fields rank by raw value."""
    v = values if direction >= 0 else -values
    r = v.rank(method="average")
    n = v.notna().sum()
    return (r - 0.5) / n * 100 if n else r


def team_profile(team: str, season: int, window: str = "season", context: str = "all",
                 through_week: int | None = None, opponent: str | None = None,
                 categories: list[str] | None = None, min_sample: int = 0) -> dict:
    g = window_rows(select(season, through_week), window, context, opponent)
    if team not in set(g["team"]):
        return {"team": team, "season": season, "window": window, "context": context, "fields": [],
                "games": 0, "note": "No completed games in this selection."}
    fl = [f for f in FIELDS if not categories or f.category in categories]
    t = league_table(g, [f.id for f in fl])
    rows = []
    for f in fl:
        n = t["N"][f.id].to_numpy(dtype=float)
        d = t["D"][f.id].to_numpy(dtype=float)
        q = t["Q"][f.id].to_numpy(dtype=float) if f.id in t["Q"].columns else None
        m, shr, sd, raw = shrink(n, d, q, f.binary)
        idx = t["N"].index.get_loc(team)
        lo, hi = COVERAGE[f.source]
        covered = season >= lo and (hi is None or season <= hi)
        sample = float(d[idx])
        available = covered and sample > 0
        series = pd.Series(np.where(d > max(min_sample, 0), raw, np.nan), index=t["N"].index)
        pct = percentile(series, f.direction if f.direction != 0 else 1)
        rows.append({
            "id": f.id, "label": f.label, "category": f.category, "side": f.side, "unit": f.unit,
            "direction": f.direction, "source": f.source, "coverage": f.coverage(), "formula": f.formula,
            "denominator": f.denominator, "value": _num(raw[idx]) if available else None,
            "shrunk": _num(shr[idx]) if available else None, "posterior_sd": _num(sd[idx]) if available else None,
            "league": _num(m) if available else None, "sample": sample, "games": int(t["games"].get(team, 0)),
            "rank": int(series.rank(ascending=f.direction < 0, method="min")[team]) if available and f.direction != 0 and not np.isnan(series[team]) else None,
            "percentile": _num(pct[team]) if available else None,
            "status": "observed" if available else ("not_covered" if not covered else "no_sample"),
            "tier": "DERIVED",
        })
    return {"team": team, "season": season, "window": window, "context": context, "opponent": opponent,
            "games": int(t["games"].get(team, 0)), "fields": rows,
            "teams_in_cohort": int(len(t["games"])), "window_label": window, "context_label": CONTEXT[context][0]}


def _num(x):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(x) or np.isinf(x) else round(x, 5)


def trend(team: str, season: int, field_id: str, through_week: int | None = None) -> list[dict]:
    """Per-game series with rolling 3/5 exposure-weighted values."""
    g = select(season, through_week)
    g = g[g["team"] == team].sort_values("kickoff")
    n, d = g[field_id + "__n"].to_numpy(float), g[field_id + "__d"].to_numpy(float)
    out = []
    for i in range(len(g)):
        def roll(k):
            a, b = n[max(0, i - k + 1): i + 1].sum(), d[max(0, i - k + 1): i + 1].sum()
            return _num(a / b) if b > 0 else None
        out.append({"week": int(g["week"].iat[i]), "opponent": g["opponent"].iat[i], "value": _num(n[i] / d[i]) if d[i] > 0 else None,
                    "sample": float(d[i]), "roll3": roll(3), "roll5": roll(5), "season_to_date": roll(10 ** 6),
                    "points_for": _num(g["points_for"].iat[i]), "points_against": _num(g["points_against"].iat[i])})
    return out


def field_catalog() -> list[dict]:
    return [{"id": f.id, "label": f.label, "category": f.category, "side": f.side, "unit": f.unit,
             "direction": f.direction, "source": f.source, "coverage": f.coverage(), "formula": f.formula,
             "denominator": f.denominator, "kind": f.kind, "binary": f.binary} for f in FIELDS]


def get(field_id: str):
    return FIELD_INDEX[field_id]
