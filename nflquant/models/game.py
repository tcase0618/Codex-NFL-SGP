"""Game outcome models: margin and total points, walk-forward by season.

Model ladder (simpler models are benchmarks, the ensemble is selected only if it
beats them out-of-sample):
    hfa       mean home margin / mean total from training seasons
    elo       linear map of Elo difference (benchmark)
    ridge     regularized linear regression on pregame features
    xgb       gradient-boosted trees (depth/trees tuned on the last training season)
    ensemble  inverse-MSE weighted blend of ridge + xgb (weights from prior OOS seasons)

Win probability = P(margin > 0) under Normal(μ, σ) with σ from prior OOS
residuals, then Platt-recalibrated on prior seasons' OOS predictions only.
Home/away expected points = (total ± margin) / 2, so the score, total, margin and
win probability are generated from one set of assumptions.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

from .. import warehouse
from ..config import settings
from ..features import load_game_features
from .calibration import Platt, brier, ece, interval_coverage, log_loss, reliability

MARGIN_FEATURES = ["net_epa", "net_pass", "net_rush", "net_sr", "net_ppd", "net_qb", "elo_diff", "rest_diff",
                   "is_neutral", "away_travel_miles", "tz_shift_away", "altitude_ft", "div_game", "is_playoff",
                   "net_inj_off", "net_inj_def", "injury_data", "early_season", "min_games", "net_pressure",
                   "home_qb_dropbacks", "away_qb_dropbacks"]
TOTAL_FEATURES = ["sum_epa", "sum_pass", "sum_rush", "sum_sr", "sum_ppd", "sum_qb", "dome", "temp_f", "wind_mph",
                  "is_playoff", "sum_inj", "injury_data", "early_season", "season_c", "div_game", "altitude_ft",
                  "net_epa", "min_games"]
FEATURE_DOC = {
    "net_epa": "(home off EPA + away def EPA allowed) − (away off + home def), as-of ratings",
    "net_pass": "same construction for dropback EPA", "net_rush": "same for designed-run EPA",
    "net_sr": "same for success rate", "net_ppd": "same for points per drive",
    "net_qb": "projected home QB shrunk EPA/dropback − away QB", "elo_diff": "home Elo − away Elo (pregame)",
    "rest_diff": "home rest days − away rest days", "is_neutral": "neutral site flag",
    "away_travel_miles": "great-circle miles from away team's home stadium", "tz_shift_away": "time-zone hours shifted by away team",
    "altitude_ft": "venue altitude", "div_game": "divisional game", "is_playoff": "postseason game",
    "net_inj_off": "home − away snap-weighted Out/Doubtful offensive players", "net_inj_def": "same for defense",
    "injury_data": "injury+snap data available (2012+)", "early_season": "week <= 4", "min_games": "min games played this season",
    "net_pressure": "net pressure-rate matchup (2016-2025, else missing)", "home_qb_dropbacks": "projected home QB prior dropbacks",
    "away_qb_dropbacks": "projected away QB prior dropbacks", "sum_epa": "sum of both offensive matchup EPA ratings",
    "sum_pass": "sum dropback EPA matchups", "sum_rush": "sum rush EPA matchups", "sum_sr": "sum success matchups",
    "sum_ppd": "sum points-per-drive matchups", "sum_qb": "sum of projected QB ratings", "dome": "dome/closed roof",
    "temp_f": "schedule temperature (70 in domes)", "wind_mph": "schedule wind (0 in domes)", "sum_inj": "total injury load",
    "season_c": "season − 2015 (scoring environment drift)",
}
XGB_GRID = [dict(max_depth=2, n_estimators=400), dict(max_depth=3, n_estimators=300), dict(max_depth=2, n_estimators=800)]
XGB_BASE = dict(learning_rate=0.03, subsample=0.8, colsample_bytree=0.8, min_child_weight=15, reg_lambda=5.0,
                objective="reg:squarederror", n_jobs=4, random_state=7)
FIRST_TEST = 2010
CODE_VERSION = "game-models-1.0"


def prepare(f: pd.DataFrame) -> pd.DataFrame:
    f = f.copy()
    f["season_c"] = f["season"] - 2015
    f["y_margin"] = f["result"]
    f["y_total"] = f["total"]
    f["y_win"] = np.where(f["result"] > 0, 1.0, np.where(f["result"] < 0, 0.0, 0.5))
    return f


def _ridge(X, y):
    med = X.median().fillna(0.0)
    cols = list(X.columns)
    m = make_pipeline(StandardScaler(), Ridge(alpha=50.0)).fit(X.fillna(med), y)
    return lambda Z: m.predict(Z[cols].fillna(med)), m


def _xgb(X, y, params):
    cols = list(X.columns)
    m = XGBRegressor(**XGB_BASE, **params).fit(X, y)
    return lambda Z: m.predict(Z[cols]), m


def _tune_xgb(train: pd.DataFrame, feats: list[str], target: str) -> dict:
    last = train["season"].max()
    tr, va = train[train["season"] < last], train[train["season"] == last]
    if len(tr) < 500 or len(va) < 100:
        return XGB_GRID[0]
    best, best_mse = XGB_GRID[0], np.inf
    for params in XGB_GRID:
        pred, _ = _xgb(tr[feats], tr[target], params)
        mse = float(np.mean((pred(va[feats]) - va[target]) ** 2))
        if mse < best_mse:
            best, best_mse = params, mse
    return best


@dataclass
class SeasonFit:
    season: int
    margin: dict
    total: dict
    sigma_margin: float
    sigma_total: float
    weights: dict
    platt: Platt
    xgb_params: dict
    models: dict


def fit_for(train: pd.DataFrame, oos_hist: pd.DataFrame | None) -> SeasonFit:
    """Fit all ladder models on ``train`` (completed games strictly before the target season)."""
    train = train[train["y_margin"].notna()]
    out_m, out_t, models = {}, {}, {}
    nn = train["is_neutral"] == 0
    hfa = float(train.loc[nn, "y_margin"].mean())
    mean_total = float(train["y_total"].mean())
    out_m["hfa"] = lambda Z, h=hfa: np.where(Z["is_neutral"] == 1, 0.0, h)
    out_t["hfa"] = lambda Z, t=mean_total: np.full(len(Z), t)
    e = np.polyfit(train["elo_diff"].fillna(0), train["y_margin"], 1)
    out_m["elo"] = lambda Z, e=e: np.polyval(e, Z["elo_diff"].fillna(0))
    out_t["elo"] = out_t["hfa"]
    out_m["ridge"], models["ridge_margin"] = _ridge(train[MARGIN_FEATURES], train["y_margin"])
    out_t["ridge"], models["ridge_total"] = _ridge(train[TOTAL_FEATURES], train["y_total"])
    pm = _tune_xgb(train, MARGIN_FEATURES, "y_margin")
    pt = _tune_xgb(train, TOTAL_FEATURES, "y_total")
    out_m["xgb"], models["xgb_margin"] = _xgb(train[MARGIN_FEATURES], train["y_margin"], pm)
    out_t["xgb"], models["xgb_total"] = _xgb(train[TOTAL_FEATURES], train["y_total"], pt)
    weights = {"ridge": 0.5, "xgb": 0.5}
    sigma_m, sigma_t = 13.5, 13.5
    platt = Platt()
    if oos_hist is not None and len(oos_hist) > 200:
        recent = oos_hist[oos_hist["season"] >= oos_hist["season"].max() - 2]
        inv = {k: 1.0 / np.mean((recent[f"margin_{k}"] - recent["y_margin"]) ** 2) for k in ("ridge", "xgb")}
        z = sum(inv.values())
        weights = {k: v / z for k, v in inv.items()}
        ens = weights["ridge"] * oos_hist["margin_ridge"] + weights["xgb"] * oos_hist["margin_xgb"]
        sigma_m = float(np.std(oos_hist["y_margin"] - ens))
        tens = weights["ridge"] * oos_hist["total_ridge"] + weights["xgb"] * oos_hist["total_xgb"]
        sigma_t = float(np.std(oos_hist["y_total"] - tens))
        raw = 1 - norm.cdf(0, loc=ens, scale=sigma_m)
        platt.fit(raw, oos_hist["y_win"])
    out_m["ensemble"] = lambda Z, w=weights: w["ridge"] * out_m["ridge"](Z) + w["xgb"] * out_m["xgb"](Z)
    out_t["ensemble"] = lambda Z, w=weights: w["ridge"] * out_t["ridge"](Z) + w["xgb"] * out_t["xgb"](Z)
    return SeasonFit(int(train["season"].max()) + 1, out_m, out_t, sigma_m, sigma_t, weights, platt,
                     {"margin": pm, "total": pt}, models)


def predict(fit: SeasonFit, Z: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"game_id": Z["game_id"].to_numpy(), "season": Z["season"].to_numpy(), "week": Z["week"].to_numpy()})
    for k in ("hfa", "elo", "ridge", "xgb", "ensemble"):
        out[f"margin_{k}"] = fit.margin[k](Z)
        out[f"total_{k}"] = fit.total[k](Z)
    raw = 1 - norm.cdf(0, loc=out["margin_ensemble"], scale=fit.sigma_margin)
    out["p_home_raw"] = raw
    out["p_home"] = fit.platt.predict(raw)
    elo_sigma = fit.sigma_margin
    out["p_home_elo"] = 1 - norm.cdf(0, loc=out["margin_elo"], scale=elo_sigma)
    out["p_home_hfa"] = 1 - norm.cdf(0, loc=out["margin_hfa"], scale=elo_sigma)
    out["sigma_margin"] = fit.sigma_margin
    out["sigma_total"] = fit.sigma_total
    out["home_points"] = (out["total_ensemble"] + out["margin_ensemble"]) / 2
    out["away_points"] = (out["total_ensemble"] - out["margin_ensemble"]) / 2
    for q, z in (("p10", -1.2816), ("p25", -0.6745), ("p75", 0.6745), ("p90", 1.2816)):
        out[f"margin_{q}"] = out["margin_ensemble"] + z * fit.sigma_margin
        out[f"total_{q}"] = out["total_ensemble"] + z * fit.sigma_total
    return out


def walk_forward(verbose: bool = True) -> pd.DataFrame:
    f = prepare(load_game_features())
    done = f[f["y_margin"].notna()]
    preds = []
    oos = None
    fits = {}
    for S in range(FIRST_TEST, int(f["season"].max()) + 1):
        train = done[done["season"] < S]
        test = done[done["season"] == S]
        if test.empty:
            continue
        fit = fit_for(train, oos)
        fits[S] = fit
        p = predict(fit, test).merge(test[["game_id", "y_margin", "y_total", "y_win", "spread_line", "total_line",
                                           "home_moneyline", "away_moneyline", "home_team", "away_team", "is_neutral",
                                           "game_type", "home_qb_source", "away_qb_source", "temp_f", "wind_mph", "dome",
                                           "net_inj_off", "net_inj_def", "early_season"]], on="game_id")
        p["calibrated"] = fit.platt.model is not None
        preds.append(p)
        oos = pd.concat(preds, ignore_index=True)
        if verbose:
            mae = np.mean(np.abs(p["margin_ensemble"] - p["y_margin"]))
            print(f"walk-forward {S}: n={len(p)} margin MAE {mae:.2f} logloss {log_loss(p['p_home'], p['y_win']):.4f} "
                  f"weights {fit.weights} xgb {fit.xgb_params}", flush=True)
    oos = pd.concat(preds, ignore_index=True)
    oos.to_parquet(settings().lake_dir / "game_oos_predictions.parquet", index=False)
    return oos


def market_probs(df: pd.DataFrame) -> np.ndarray:
    """Vig-removed home win probability from moneylines (benchmark only)."""
    def implied(ml):
        ml = np.asarray(ml, float)
        return np.where(ml < 0, -ml / (-ml + 100), 100 / (ml + 100))
    h, a = implied(df["home_moneyline"]), implied(df["away_moneyline"])
    return h / (h + a)


def evaluate(oos: pd.DataFrame, first_eval: int = 2012) -> dict:
    e = oos[oos["season"] >= first_eval].copy()
    res = {"n_games": int(len(e)), "seasons": [int(e.season.min()), int(e.season.max())], "models": {}}
    for k in ("hfa", "elo", "ridge", "xgb", "ensemble"):
        res["models"][k] = {
            "margin_mae": float(np.mean(np.abs(e[f"margin_{k}"] - e["y_margin"]))),
            "margin_rmse": float(np.sqrt(np.mean((e[f"margin_{k}"] - e["y_margin"]) ** 2))),
            "total_mae": float(np.mean(np.abs(e[f"total_{k}"] - e["y_total"]))),
        }
    res["models"]["hfa"]["log_loss"] = log_loss(e["p_home_hfa"], e["y_win"])
    res["models"]["elo"]["log_loss"] = log_loss(e["p_home_elo"], e["y_win"])
    res["models"]["elo"]["brier"] = brier(e["p_home_elo"], e["y_win"])
    res["models"]["ensemble"].update({
        "log_loss_raw": log_loss(e["p_home_raw"], e["y_win"]), "log_loss": log_loss(e["p_home"], e["y_win"]),
        "brier": brier(e["p_home"], e["y_win"]), "ece": ece(e["p_home"], e["y_win"]),
        "accuracy": float(np.mean((e["p_home"] > 0.5) == (e["y_win"] == 1))),
        "home_points_mae": float(np.mean(np.abs(e["home_points"] - (e["y_total"] + e["y_margin"]) / 2))),
        "away_points_mae": float(np.mean(np.abs(e["away_points"] - (e["y_total"] - e["y_margin"]) / 2))),
        "margin_80pi_coverage": interval_coverage(e["y_margin"], e["margin_p10"], e["margin_p90"]),
        "margin_50pi_coverage": interval_coverage(e["y_margin"], e["margin_p25"], e["margin_p75"]),
        "total_80pi_coverage": interval_coverage(e["y_total"], e["total_p10"], e["total_p90"]),
    })
    res["reliability"] = reliability(e["p_home"], e["y_win"])
    mk = e.dropna(subset=["home_moneyline", "away_moneyline", "spread_line"])
    if len(mk):
        pm = market_probs(mk)
        res["market_benchmark"] = {
            "n_games": int(len(mk)), "note": "Closing lines from nflverse schedules; benchmark only, never a model input.",
            "margin_mae_market": float(np.mean(np.abs(mk["spread_line"] - mk["y_margin"]))),
            "margin_mae_model": float(np.mean(np.abs(mk["margin_ensemble"] - mk["y_margin"]))),
            "total_mae_market": float(np.mean(np.abs(mk["total_line"] - mk["y_total"]))),
            "total_mae_model": float(np.mean(np.abs(mk["total_ensemble"] - mk["y_total"]))),
            "log_loss_market": log_loss(pm, mk["y_win"]), "log_loss_model": log_loss(mk["p_home"], mk["y_win"]),
            "brier_market": brier(pm, mk["y_win"]), "brier_model": brier(mk["p_home"], mk["y_win"]),
        }
    seg = []
    def add(name, mask):
        s = e[mask]
        if len(s) >= 30:
            seg.append({"segment": name, "n": int(len(s)), "log_loss": log_loss(s["p_home"], s["y_win"]),
                        "brier": brier(s["p_home"], s["y_win"]),
                        "margin_mae": float(np.mean(np.abs(s["margin_ensemble"] - s["y_margin"]))),
                        "total_mae": float(np.mean(np.abs(s["total_ensemble"] - s["y_total"])))})
    for s in sorted(e["season"].unique()):
        add(f"season {s}", e["season"] == s)
    add("home favorite (model)", e["margin_ensemble"] > 0)
    add("away favorite (model)", e["margin_ensemble"] <= 0)
    add("close (|pred margin| < 3)", e["margin_ensemble"].abs() < 3)
    add("lopsided (|pred margin| >= 7)", e["margin_ensemble"].abs() >= 7)
    add("weeks 1-4", e["early_season"] == 1)
    add("weeks 5+", e["early_season"] == 0)
    add("playoffs", e["game_type"] != "REG")
    add("dome", e["dome"] == 1)
    add("outdoor wind >= 15", e["wind_mph"] >= 15)
    add("outdoor temp <= 40", e["temp_f"] <= 40)
    add("QB switched by injury report", (e["home_qb_source"] == "injury_replacement") | (e["away_qb_source"] == "injury_replacement"))
    add("heavy injury imbalance (|net off snaps| >= 1)", e["net_inj_off"].abs() >= 1)
    add("neutral site", e["is_neutral"] == 1)
    res["segments"] = seg
    return res


def train_production(verbose: bool = True) -> tuple[SeasonFit, pd.DataFrame, dict]:
    """Fit on every completed game and predict all not-yet-played games."""
    f = prepare(load_game_features())
    oos = pd.read_parquet(settings().lake_dir / "game_oos_predictions.parquet")
    done = f[f["y_margin"].notna()]
    fit = fit_for(done, oos)
    upcoming = f[f["y_margin"].isna() & f["home_off_epa"].notna()]
    preds = predict(fit, upcoming) if len(upcoming) else pd.DataFrame()
    version = model_version("game_ensemble", done)
    meta = {"model_version": version, "trained_through": str(pd.to_datetime(done["kickoff"]).max()),
            "n_train": int(len(done)), "weights": fit.weights, "sigma_margin": fit.sigma_margin,
            "sigma_total": fit.sigma_total, "platt": fit.platt.params(), "xgb_params": fit.xgb_params}
    if len(preds):
        preds = preds.merge(upcoming[["game_id", "home_team", "away_team", "kickoff", "home_qb_id", "away_qb_id",
                                      "home_qb_source", "away_qb_source"]], on="game_id")
    return fit, preds, meta


def shap_explain(fit: SeasonFit, row: pd.DataFrame) -> list[dict]:
    """TreeSHAP contributions of the XGB margin model plus ridge linear contributions."""
    import xgboost as xgb
    booster = fit.models["xgb_margin"].get_booster()
    contrib = booster.predict(xgb.DMatrix(row[MARGIN_FEATURES]), pred_contribs=True)[0]
    out = [{"feature": f, "xgb_shap": float(c), "value": _f(row[f].iat[0]), "description": FEATURE_DOC.get(f, f)}
           for f, c in zip(MARGIN_FEATURES, contrib[:-1])]
    pipe = fit.models["ridge_margin"]
    sc, rg = pipe.named_steps["standardscaler"], pipe.named_steps["ridge"]
    x = row[MARGIN_FEATURES].fillna(pd.Series(sc.mean_, index=MARGIN_FEATURES)).to_numpy()[0]
    lin = (x - sc.mean_) / sc.scale_ * rg.coef_
    for o, l in zip(out, lin):
        o["ridge_contribution"] = float(l)
    return sorted(out, key=lambda o: -abs(o["xgb_shap"] * fit.weights["xgb"] + o["ridge_contribution"] * fit.weights["ridge"]))


def _f(x):
    try:
        x = float(x)
        return None if np.isnan(x) else x
    except (TypeError, ValueError):
        return None


def model_version(model_id: str, train: pd.DataFrame) -> str:
    h = hashlib.sha256(pd.util.hash_pandas_object(train[["game_id", "y_margin"]], index=False).values.tobytes()).hexdigest()[:10]
    return f"{model_id}-{CODE_VERSION}-{h}"


def record(oos: pd.DataFrame, results: dict) -> None:
    """Persist walk-forward predictions and validation metrics to the warehouse."""
    warehouse.init()
    created = warehouse.now()
    with warehouse.connect() as con:
        con.execute("DELETE FROM model_predictions WHERE model_version LIKE 'game_wf-%'")
        rows = []
        for _, r in oos.iterrows():
            mv = f"game_wf-{CODE_VERSION}-test{int(r['season'])}"
            rows.append((mv, r["game_id"], "home", "margin", r["margin_ensemble"], r["sigma_margin"], r["margin_p10"],
                         r["margin_p25"], r["margin_ensemble"], r["margin_p75"], r["margin_p90"], r["p_home"], created,
                         f"seasons < {int(r['season'])}; as-of ratings before week {int(r['week'])}"))
            rows.append((mv, r["game_id"], "game", "total", r["total_ensemble"], r["sigma_total"], r["total_p10"],
                         r["total_p25"], r["total_ensemble"], r["total_p75"], r["total_p90"], None, created,
                         f"seasons < {int(r['season'])}"))
        con.executemany("INSERT OR REPLACE INTO model_predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        for S in sorted(oos["season"].unique()):
            con.execute("INSERT OR REPLACE INTO model_versions VALUES (?,?,?,?,?,?,?,?)", (
                f"game_wf-{CODE_VERSION}-test{int(S)}", "game_ensemble", created, f"{FIRST_TEST - 3}-{int(S) - 1}",
                json.dumps({"xgb_grid": XGB_GRID, "xgb_base": XGB_BASE, "ridge_alpha": 50.0}), None, CODE_VERSION,
                "walk-forward test season"))
        con.execute("DELETE FROM validation_results WHERE model_version = ?", (f"game_wf-{CODE_VERSION}",))
        vr = []
        for model, m in results["models"].items():
            for metric, v in m.items():
                vr.append((f"game_wf-{CODE_VERSION}", "walk_forward", f"model:{model}", metric, v, results["n_games"], created))
        for s in results["segments"]:
            for metric in ("log_loss", "brier", "margin_mae", "total_mae"):
                vr.append((f"game_wf-{CODE_VERSION}", "walk_forward", s["segment"], metric, s[metric], s["n"], created))
        con.executemany("INSERT OR REPLACE INTO validation_results VALUES (?,?,?,?,?,?,?)", vr)
        con.execute("DELETE FROM model_features WHERE model_id IN ('game_margin','game_total')")
        con.executemany("INSERT INTO model_features VALUES (?,?,?)",
                        [("game_margin", f, FEATURE_DOC.get(f, f)) for f in MARGIN_FEATURES] +
                        [("game_total", f, FEATURE_DOC.get(f, f)) for f in TOTAL_FEATURES])


def run() -> dict:
    oos = walk_forward()
    res = evaluate(oos)
    record(oos, res)
    (settings().lake_dir / "game_validation.json").write_text(json.dumps(res, indent=1, default=float))
    return res


if __name__ == "__main__":
    r = run()
    print(json.dumps({k: v for k, v in r.items() if k in ("n_games", "models", "market_benchmark")}, indent=1, default=float))
