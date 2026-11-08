"""Player stat projections, walk-forward by season.

Dependency order made explicit in the features:
    availability  -> injury report status (Out/Doubtful removed; Questionable flagged),
                     practice participation, projected starting QB flag
    snaps/role    -> EWMA snap %, share of team targets / carries / attempts
    team volume   -> pregame team expected points & margin (walk-forward game model),
                     team pass/rush ratings, opponent defensive ratings
    efficiency    -> EWMA yards per opportunity, catch rate, TD rate (shrunk by EWMA)
    outcomes      -> count models (Poisson objective) and yardage models (Tweedie);
                     10/25/50/75/90 quantiles from multi-quantile gradient boosting.

Candidate pool for game g: players who recorded a stat for the team in any of its
previous three games, minus players listed Out/Doubtful for g's week. A projected
player who then records nothing is scored as 0 (that miss is real forecast error).
Baseline: the player's EWMA of the stat (half-life 4 games) — the model must beat it.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from .. import lake, warehouse
from ..config import settings
from .calibration import brier, ece, interval_coverage, reliability

HALF_LIFE = 4.0
FIRST_TRAIN = 2010
FIRST_TEST = 2016
QUANTILES = [0.1, 0.25, 0.5, 0.75, 0.9]

STATS = {
    "attempts": "attempts", "completions": "completions", "passing_yards": "passing_yards",
    "passing_tds": "passing_tds", "interceptions": "passing_interceptions", "sacks_taken": "sacks_suffered",
    "carries": "carries", "rushing_yards": "rushing_yards", "rushing_tds": "rushing_tds",
    "targets": "targets", "receptions": "receptions", "receiving_yards": "receiving_yards",
    "receiving_tds": "receiving_tds", "explosive_receptions": "explosive_receptions",
    "tackles": "tackles", "tackles_for_loss": "def_tackles_for_loss", "sacks": "def_sacks",
    "def_interceptions": "def_interceptions", "pass_breakups": "def_pass_defended", "forced_fumbles": "def_fumbles_forced",
    "fg_att": "fg_att", "fg_made": "fg_made", "xp_made": "pat_made", "punts": "pt_att",
    "kick_returns": "kickoff_returns", "kick_return_yards": "kickoff_return_yards",
    "punt_returns": "punt_returns", "punt_return_yards": "punt_return_yards",
}
TARGETS = {
    # target: (positions, kind)
    "attempts": ({"QB"}, "count"), "completions": ({"QB"}, "count"), "passing_yards": ({"QB"}, "yards"),
    "passing_tds": ({"QB"}, "count"), "interceptions": ({"QB"}, "count"), "sacks_taken": ({"QB"}, "count"),
    "carries": ({"QB", "RB", "WR"}, "count"), "rushing_yards": ({"QB", "RB", "WR"}, "yards"),
    "rushing_tds": ({"QB", "RB"}, "count"),
    "targets": ({"RB", "WR", "TE"}, "count"), "receptions": ({"RB", "WR", "TE"}, "count"),
    "receiving_yards": ({"RB", "WR", "TE"}, "yards"), "receiving_tds": ({"RB", "WR", "TE"}, "count"),
    "explosive_receptions": ({"RB", "WR", "TE"}, "count"),
    "tackles": ({"DL", "LB", "DB"}, "count"), "tackles_for_loss": ({"DL", "LB", "DB"}, "count"),
    "sacks": ({"DL", "LB", "DB"}, "count"), "def_interceptions": ({"LB", "DB"}, "count"),
    "pass_breakups": ({"LB", "DB"}, "count"), "forced_fumbles": ({"DL", "LB", "DB"}, "count"),
    "fg_att": ({"K"}, "count"), "fg_made": ({"K"}, "count"), "xp_made": ({"K"}, "count"), "punts": ({"P"}, "count"),
    "kick_returns": ({"KR"}, "count"), "kick_return_yards": ({"KR"}, "yards"),
    "punt_returns": ({"PR"}, "count"), "punt_return_yards": ({"PR"}, "yards"),
}
TEAM_TOTALS = ["attempts", "carries", "targets"]


def _group(pos: str | None, pg_group: str | None) -> str | None:
    if pos in ("QB", "RB", "WR", "TE", "K", "P"):
        return pos
    if pos == "FB":
        return "RB"
    if pg_group in ("DL", "LB", "DB"):
        return pg_group
    return None


def explosive_receptions() -> pd.DataFrame:
    frames = []
    for s in range(settings().first_season, settings().last_season + 1):
        p = lake.load_plays([s])
        if p.empty:
            continue
        p = p[(p["complete_pass"] == 1) & p["receiver_player_id"].notna() & (p["yards_gained"] >= 20)]
        frames.append(p.groupby(["game_id", "receiver_player_id"]).size().rename("explosive_receptions").reset_index())
    return pd.concat(frames).rename(columns={"receiver_player_id": "player_id"})


def base_table() -> pd.DataFrame:
    pg = lake.load("player_games")
    games = lake.load("games")[["game_id", "kickoff", "home_team", "away_team", "game_type"]]
    pg = pg.merge(games, on="game_id", how="inner")
    pg = pg.merge(explosive_receptions(), on=["game_id", "player_id"], how="left")
    pg["explosive_receptions"] = pg["explosive_receptions"].fillna(0)
    pg["tackles"] = pg["def_tackles_solo"].fillna(0) + pg["def_tackle_assists"].fillna(0)
    pg["group"] = [_group(p, g) for p, g in zip(pg["position"], pg["position_group"])]
    for c in set(STATS.values()) | {"offense_pct", "defense_pct", "st_pct", "receiving_air_yards"}:
        if c in pg.columns:
            pg[c] = pd.to_numeric(pg[c], errors="coerce")
    pg = pg[pg["group"].notna() | (pg["kickoff_returns"].fillna(0) > 0) | (pg["punt_returns"].fillna(0) > 0)]
    tt = pg.groupby(["game_id", "team"])[TEAM_TOTALS].sum().add_prefix("team_").reset_index()
    pg = pg.merge(tt, on=["game_id", "team"], how="left")
    for t in TEAM_TOTALS:
        pg[f"share_{t}"] = pg[t] / pg[f"team_{t}"].replace(0, np.nan)
    pg["kickoff"] = pd.to_datetime(pg["kickoff"])
    return pg.sort_values(["player_id", "kickoff"])


HIST_COLS = sorted(set(STATS.values()) | {"offense_pct", "defense_pct", "st_pct", "share_attempts", "share_carries",
                                          "share_targets", "receiving_air_yards"})


def player_history(pg: pd.DataFrame) -> pd.DataFrame:
    """Post-game EWMA state for each player after each game (used for the *next* game)."""
    h = pg[["player_id", "kickoff", "game_id", "team", "season", "group", "position"] + HIST_COLS].copy()
    g = h.groupby("player_id", sort=False)
    for c in HIST_COLS:
        h[f"ew_{c}"] = g[c].transform(lambda x: x.ewm(halflife=HALF_LIFE, ignore_na=True).mean())
    h["games_career"] = g.cumcount() + 1
    h["games_season"] = h.groupby(["player_id", "season"]).cumcount() + 1
    for c in ("passing_yards", "rushing_yards", "receiving_yards", "targets", "carries", "attempts"):
        h[f"std_{c}"] = g[c].transform(lambda x: x.rolling(8, min_periods=3).std())
    h["ew_ypt"] = h["ew_receiving_yards"] / h["ew_targets"].replace(0, np.nan)
    h["ew_ypc"] = h["ew_rushing_yards"] / h["ew_carries"].replace(0, np.nan)
    h["ew_ypa"] = h["ew_passing_yards"] / h["ew_attempts"].replace(0, np.nan)
    h["ew_catch"] = h["ew_receptions"] / h["ew_targets"].replace(0, np.nan)
    h["ew_comp"] = h["ew_completions"] / h["ew_attempts"].replace(0, np.nan)
    h["last_kickoff"] = h["kickoff"]
    h["last_team"] = h["team"]
    return h.drop(columns=HIST_COLS + ["kickoff", "game_id", "team", "season"])


def candidates(pg: pd.DataFrame) -> pd.DataFrame:
    """(game, team, player) rows for players who appeared for the team in its previous 3 games."""
    games = lake.load("games")
    games = games[games["season"] >= FIRST_TRAIN - 1]
    tg = pd.concat([games[["game_id", "season", "week", "kickoff", "home_team"]].rename(columns={"home_team": "team"}),
                    games[["game_id", "season", "week", "kickoff", "away_team"]].rename(columns={"away_team": "team"})])
    tg["kickoff"] = pd.to_datetime(tg["kickoff"])
    tg = tg.sort_values(["team", "kickoff"])
    appear = pg[["game_id", "team", "player_id"]].drop_duplicates()
    rows = []
    for team, grp in tg.groupby("team"):
        ids = grp["game_id"].tolist()
        app = appear[appear["team"] == team].groupby("game_id")["player_id"].apply(set).to_dict()
        for i, gid in enumerate(ids):
            if i == 0:
                continue
            pool = set().union(*[app.get(x, set()) for x in ids[max(0, i - 3): i]])
            for pid in pool:
                rows.append((gid, team, pid))
    c = pd.DataFrame(rows, columns=["game_id", "team", "player_id"])
    return c.merge(tg[["game_id", "team", "season", "week", "kickoff"]], on=["game_id", "team"])


def build_dataset(verbose: bool = True) -> pd.DataFrame:
    pg = base_table()
    hist = player_history(pg).sort_values("last_kickoff")
    cand = candidates(pg).sort_values("kickoff")
    if verbose:
        print("player candidates", len(cand), flush=True)
    d = pd.merge_asof(cand, hist, left_on="kickoff", right_on="last_kickoff", by="player_id",
                      allow_exact_matches=False, direction="backward")
    d = d[d["last_team"] == d["team"]]  # player's most recent game was for this team
    d["days_since_last"] = (d["kickoff"] - d["last_kickoff"]).dt.days
    # Availability: official injury report for the game week
    inj = lake.load("injuries")[["season", "week", "team", "gsis_id", "report_status", "practice_status"]]
    inj = inj.rename(columns={"gsis_id": "player_id"}).drop_duplicates(["season", "week", "team", "player_id"], keep="last")
    d = d.merge(inj, on=["season", "week", "team", "player_id"], how="left")
    d = d[~d["report_status"].isin(["Out", "Doubtful"])]
    d["questionable"] = (d["report_status"] == "Questionable").astype(float)
    ps = d["practice_status"].astype("string")
    d["practice_dnp"] = ps.str.contains("Did Not", na=False).astype(float)
    d["practice_limited"] = ps.str.contains("Limited", na=False).astype(float)
    # Team context from the walk-forward game model (pregame) and as-of ratings
    gp = pd.read_parquet(settings().lake_dir / "game_oos_predictions.parquet")[["game_id", "home_points", "away_points", "margin_ensemble"]]
    gf = pd.read_parquet(settings().lake_dir / "game_features.parquet")
    gf = gf[["game_id", "home_team", "away_team", "home_qb_id", "away_qb_id", "home_off_pass", "home_off_rush",
             "away_off_pass", "away_off_rush", "home_def_pass", "home_def_rush", "away_def_pass", "away_def_rush",
             "home_def_pressure", "away_def_pressure", "dome", "wind_mph", "temp_f", "is_neutral"]]
    gf = gf.merge(gp, on="game_id", how="left")
    d = d.merge(gf, on="game_id", how="left")
    home = d["team"] == d["home_team"]
    d["is_home"] = home.astype(float)
    pick = lambda h, a: np.where(home, d[h], d[a])
    d["team_pts_pred"] = pick("home_points", "away_points")
    d["opp_pts_pred"] = pick("away_points", "home_points")
    d["team_margin_pred"] = np.where(home, d["margin_ensemble"], -d["margin_ensemble"])
    d["team_off_pass"] = pick("home_off_pass", "away_off_pass")
    d["team_off_rush"] = pick("home_off_rush", "away_off_rush")
    d["opp_def_pass"] = pick("away_def_pass", "home_def_pass")
    d["opp_def_rush"] = pick("away_def_rush", "home_def_rush")
    d["opp_def_pressure"] = pick("away_def_pressure", "home_def_pressure")
    d["opp_off_pass"] = pick("away_off_pass", "home_off_pass")
    d["opp_off_rush"] = pick("away_off_rush", "home_off_rush")
    d["projected_qb"] = (d["player_id"] == pick("home_qb_id", "away_qb_id")).astype(float)
    d = d.drop(columns=["home_points", "away_points", "margin_ensemble", "home_team", "away_team", "home_qb_id", "away_qb_id",
                        "home_off_pass", "home_off_rush", "away_off_pass", "away_off_rush", "home_def_pass",
                        "home_def_rush", "away_def_pass", "away_def_rush", "home_def_pressure", "away_def_pressure"])
    # Labels: actual next-game stats (0 when absent)
    lab = pg[["game_id", "player_id"] + list(set(STATS.values()))].drop_duplicates(["game_id", "player_id"])
    lab = lab.rename(columns={v: f"y_{k}" for k, v in STATS.items()})
    for k, v in STATS.items():
        if f"y_{k}" not in lab.columns and v in pg.columns:
            lab[f"y_{k}"] = pg[v]
    d = d.merge(lab, on=["game_id", "player_id"], how="left")
    d["played"] = d["y_targets"].notna().astype(float)
    for k in STATS:
        d[f"y_{k}"] = d[f"y_{k}"].fillna(0.0) if f"y_{k}" in d else 0.0
    d["is_returner_kr"] = (d["ew_kickoff_returns"] > 0.3).astype(float)
    d["is_returner_pr"] = (d["ew_punt_returns"] > 0.3).astype(float)
    d.to_parquet(settings().lake_dir / "player_dataset.parquet", index=False)
    return d


FEATURES = [c for c in [f"ew_{c}" for c in HIST_COLS] + [
    "games_career", "games_season", "std_passing_yards", "std_rushing_yards", "std_receiving_yards", "std_targets",
    "std_carries", "std_attempts", "ew_ypt", "ew_ypc", "ew_ypa", "ew_catch", "ew_comp", "days_since_last",
    "questionable", "practice_dnp", "practice_limited", "is_home", "team_pts_pred", "opp_pts_pred", "team_margin_pred",
    "team_off_pass", "team_off_rush", "opp_def_pass", "opp_def_rush", "opp_def_pressure", "opp_off_pass", "opp_off_rush",
    "projected_qb", "dome", "wind_mph", "temp_f", "is_neutral"]]
POS_FEATURES = ["pos_QB", "pos_RB", "pos_WR", "pos_TE", "pos_DL", "pos_LB", "pos_DB"]
BASELINE = {k: f"ew_{v}" for k, v in STATS.items()}


def rows_for(d: pd.DataFrame, target: str) -> pd.DataFrame:
    pos, _ = TARGETS[target]
    if pos == {"KR"}:
        return d[d["is_returner_kr"] == 1]
    if pos == {"PR"}:
        return d[d["is_returner_pr"] == 1]
    return d[d["group"].isin(pos)]


def _xgb(kind: str, quantile: bool = False):
    common = dict(n_estimators=350, learning_rate=0.05, max_depth=4, min_child_weight=20, subsample=0.8,
                  colsample_bytree=0.8, reg_lambda=5.0, n_jobs=4, random_state=11, tree_method="hist")
    if quantile:
        return XGBRegressor(objective="reg:quantileerror", quantile_alpha=np.array(QUANTILES), **common)
    if kind == "count":
        return XGBRegressor(objective="count:poisson", max_delta_step=0.7, **common)
    return XGBRegressor(objective="reg:squarederror", **common)  # yards can be negative


def _X(df: pd.DataFrame) -> pd.DataFrame:
    X = df[FEATURES].copy()
    for p in ("QB", "RB", "WR", "TE", "DL", "LB", "DB"):
        X[f"pos_{p}"] = (df["group"] == p).astype(float)
    return X


def nb_dispersion(mu: np.ndarray, y: np.ndarray) -> float:
    """Method-of-moments NB size r from var = μ + μ²/r (∞ -> Poisson)."""
    excess = np.mean((y - mu) ** 2 - mu)
    m2 = np.mean(mu ** 2)
    return float(m2 / excess) if excess > 1e-9 else 1e6


def walk_forward(d: pd.DataFrame | None = None, targets: list[str] | None = None, verbose: bool = True) -> pd.DataFrame:
    d = d if d is not None else pd.read_parquet(settings().lake_dir / "player_dataset.parquet")
    d = d[d["season"] >= FIRST_TRAIN]
    d = d[d["game_id"].isin(set(lake.load("games").query("completed").game_id))]  # unplayed games are not labels
    out = []
    last = int(d["season"].max())
    for target in targets or list(TARGETS):
        _, kind = TARGETS[target]
        rows = rows_for(d, target)
        for S in range(FIRST_TEST, last + 1):
            tr, te = rows[rows["season"] < S], rows[rows["season"] == S]
            if len(tr) < 500 or te.empty:
                continue
            m = _xgb(kind).fit(_X(tr), tr[f"y_{target}"])
            mu = np.clip(m.predict(_X(te)), 0, None)
            res = pd.DataFrame({"game_id": te["game_id"].to_numpy(), "player_id": te["player_id"].to_numpy(),
                                "team": te["team"].to_numpy(), "season": S, "week": te["week"].to_numpy(),
                                "group": te["group"].to_numpy(), "target": target, "kind": kind,
                                "y": te[f"y_{target}"].to_numpy(), "pred": mu,
                                "baseline": te[BASELINE[target]].fillna(0).to_numpy(), "played": te["played"].to_numpy(),
                                "questionable": te["questionable"].to_numpy(), "is_home": te["is_home"].to_numpy(),
                                "opp_def_pass": te["opp_def_pass"].to_numpy(), "dome": te["dome"].to_numpy(),
                                "wind_mph": te["wind_mph"].to_numpy()})
            # quantiles: fitted directly for yards; NB-implied for counts
            if kind == "yards":
                q = _xgb(kind, quantile=True).fit(_X(tr), tr[f"y_{target}"]).predict(_X(te))
                q = np.sort(q, axis=1)
                for i, a in enumerate(QUANTILES):
                    res[f"q{int(a * 100)}"] = q[:, i]
            else:
                mu_tr = np.clip(m.predict(_X(tr)), 0, None)
                r = nb_dispersion(mu_tr, tr[f"y_{target}"].to_numpy())
                res["nb_size"] = r
                from scipy.stats import nbinom
                pr = r / (r + np.maximum(mu, 1e-9))
                for a in QUANTILES:
                    res[f"q{int(a * 100)}"] = nbinom.ppf(a, r, pr)
                res["p_ge1"] = 1 - nbinom.pmf(0, r, pr)
            out.append(res)
            if verbose:
                mae, bmae = np.mean(np.abs(res["pred"] - res["y"])), np.mean(np.abs(res["baseline"] - res["y"]))
                print(f"{target:22s} {S} n={len(te):6d} MAE {mae:7.3f} baseline {bmae:7.3f}", flush=True)
    oos = pd.concat(out, ignore_index=True)
    oos.to_parquet(settings().lake_dir / "player_oos_predictions.parquet", index=False)
    return oos


def evaluate(oos: pd.DataFrame) -> dict:
    res = {"targets": {}, "segments": []}
    for target, g in oos.groupby("target"):
        m = {"n": int(len(g)), "kind": TARGETS[target][1], "mae": float(np.mean(np.abs(g["pred"] - g["y"]))),
             "baseline_mae": float(np.mean(np.abs(g["baseline"] - g["y"]))),
             "rmse": float(np.sqrt(np.mean((g["pred"] - g["y"]) ** 2))),
             "mae_played_only": float(np.mean(np.abs(g.loc[g.played == 1, "pred"] - g.loc[g.played == 1, "y"]))),
             "bias": float(np.mean(g["pred"] - g["y"])),
             "coverage_80": interval_coverage(g["y"], g["q10"], g["q90"]),
             "coverage_50": interval_coverage(g["y"], g["q25"], g["q75"]),
             "seasons": [int(g.season.min()), int(g.season.max())]}
        m["skill_vs_baseline"] = 1 - m["mae"] / m["baseline_mae"] if m["baseline_mae"] else None
        if "p_ge1" in g and g["p_ge1"].notna().any():
            yb = (g["y"] >= 1).astype(float)
            m["p_ge1_brier"] = brier(g["p_ge1"], yb)
            m["p_ge1_ece"] = ece(g["p_ge1"], yb)
            m["p_ge1_reliability"] = reliability(g["p_ge1"], yb)
            m["p_ge1_base_rate"] = float(yb.mean())
        res["targets"][target] = m
        for name, mask in (("home", g.is_home == 1), ("away", g.is_home == 0), ("questionable", g.questionable == 1),
                           ("healthy", g.questionable == 0), ("vs top-10 pass D", g.opp_def_pass <= g.opp_def_pass.quantile(0.31)),
                           ("vs bottom-10 pass D", g.opp_def_pass >= g.opp_def_pass.quantile(0.69)),
                           ("dome", g.dome == 1), ("wind >= 15", g.wind_mph >= 15)):
            s = g[mask]
            if len(s) >= 50:
                res["segments"].append({"target": target, "segment": name, "n": int(len(s)),
                                        "mae": float(np.mean(np.abs(s.pred - s.y))),
                                        "baseline_mae": float(np.mean(np.abs(s.baseline - s.y)))})
        for grp, s in g.groupby("group"):
            res["segments"].append({"target": target, "segment": f"position {grp}", "n": int(len(s)),
                                    "mae": float(np.mean(np.abs(s.pred - s.y))), "baseline_mae": float(np.mean(np.abs(s.baseline - s.y)))})
        for season, s in g.groupby("season"):
            res["segments"].append({"target": target, "segment": f"season {season}", "n": int(len(s)),
                                    "mae": float(np.mean(np.abs(s.pred - s.y))), "baseline_mae": float(np.mean(np.abs(s.baseline - s.y)))})
    return res


class ProductionModels:
    """Models fit on all completed seasons; used for upcoming-game projections."""

    def __init__(self, d: pd.DataFrame):
        self.d = d
        self.models, self.qmodels, self.nb = {}, {}, {}
        done = d[d["game_id"].isin(set(lake.load("games").query("completed").game_id))]
        for target, (_, kind) in TARGETS.items():
            rows = rows_for(done, target)
            if len(rows) < 500:
                continue
            self.models[target] = _xgb(kind).fit(_X(rows), rows[f"y_{target}"])
            if kind == "yards":
                self.qmodels[target] = _xgb(kind, quantile=True).fit(_X(rows), rows[f"y_{target}"])
            else:
                mu = np.clip(self.models[target].predict(_X(rows)), 0, None)
                self.nb[target] = nb_dispersion(mu, rows[f"y_{target}"].to_numpy())

    def predict(self, rows: pd.DataFrame) -> pd.DataFrame:
        from scipy.stats import nbinom
        out = []
        for target, m in self.models.items():
            r = rows_for(rows, target)
            if r.empty:
                continue
            mu = np.clip(m.predict(_X(r)), 0, None)
            res = pd.DataFrame({"game_id": r["game_id"].to_numpy(), "player_id": r["player_id"].to_numpy(),
                                "team": r["team"].to_numpy(), "group": r["group"].to_numpy(), "target": target, "mean": mu})
            if target in self.qmodels:
                q = np.sort(self.qmodels[target].predict(_X(r)), axis=1)
                for i, a in enumerate(QUANTILES):
                    res[f"q{int(a * 100)}"] = q[:, i]
            else:
                size = self.nb[target]
                pr = size / (size + np.maximum(mu, 1e-9))
                for a in QUANTILES:
                    res[f"q{int(a * 100)}"] = nbinom.ppf(a, size, pr)
                res["p_ge1"] = 1 - nbinom.pmf(0, size, pr)
            out.append(res)
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def record(res: dict) -> None:
    warehouse.init()
    created = warehouse.now()
    mv = "player_wf-1.0"
    with warehouse.connect() as con:
        con.execute("DELETE FROM validation_results WHERE model_version = ?", (mv,))
        rows = []
        for t, m in res["targets"].items():
            for k, v in m.items():
                if isinstance(v, (int, float)) and v is not None:
                    rows.append((mv, "walk_forward", f"target:{t}", k, float(v), m["n"], created))
        for s in res["segments"]:
            rows.append((mv, "walk_forward", f"{s['target']}|{s['segment']}", "mae", s["mae"], s["n"], created))
            rows.append((mv, "walk_forward", f"{s['target']}|{s['segment']}", "baseline_mae", s["baseline_mae"], s["n"], created))
        con.executemany("INSERT OR REPLACE INTO validation_results VALUES (?,?,?,?,?,?,?)", rows)
        con.execute("INSERT OR REPLACE INTO model_versions VALUES (?,?,?,?,?,?,?,?)", (
            mv, "player_projections", created, f"{FIRST_TRAIN}-season before test", json.dumps({"half_life": HALF_LIFE}),
            None, "player-models-1.0", "walk-forward by season; XGB Poisson/Tweedie + multi-quantile"))


def run(rebuild: bool = True) -> dict:
    d = build_dataset() if rebuild else None
    oos = walk_forward(d)
    res = evaluate(oos)
    record(res)
    (settings().lake_dir / "player_validation.json").write_text(json.dumps(res, indent=1, default=float))
    return res


if __name__ == "__main__":
    r = run()
    print(json.dumps({t: {k: v for k, v in m.items() if k not in ("p_ge1_reliability",)} for t, m in r["targets"].items()}, indent=1, default=float))
