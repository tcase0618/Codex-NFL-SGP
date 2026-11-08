"""Trained unit-matchup models (Phase 6).

Play-level gradient-boosted models predict per-play outcomes from the as-of
offensive unit profile, the as-of opposing defensive unit profile, the projected
QB's prior efficiency and the game situation:

    dropbacks: EPA, success, pressure (2016-2025), sack, explosive (>=20), INT
    designed runs: EPA, success, stuff (<=0 yds), explosive (>=10)

A matchup's *impact* is the model prediction for (this offense, this defense)
minus the prediction for (this offense, league-average defense) — evaluated on
a fixed league situation mix, so the number is a trained conditional effect, not
a difference of rankings. Coverage and box splits are obtained by fixing the
charted indicator (man/zone, light/loaded box) and weighting by the opponent's
as-of usage rate.

Validation: train seasons < 2024, test 2024-2025 (held out), reporting log loss /
Brier vs the base rate and R² for EPA.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from xgboost import XGBClassifier, XGBRegressor

from .. import lake
from ..asof import asof_fields
from ..config import settings
from ..features import load_game_features
from ..metrics.plays import derive
from .calibration import brier, log_loss

PASS_OFF = ["off.pass_epa.all", "off.sack_rate.all", "off.pressure_rate.all", "off.pass_epa.man", "off.pass_epa.zone",
            "off.pass_epa.pressure", "off.pass_epa.clean", "off.explosive_pass.all", "off.int_rate.all", "off.adot.all",
            "off.comp_pct.all", "off.pass_epa.blitz", "off.pass_epa.play_action"]
PASS_DEF = ["def.pass_epa.all", "def.sack_rate.all", "def.pressure_rate.all", "def.man_rate.all", "def.blitz_rate.all",
            "def.pass_epa.man", "def.pass_epa.zone", "def.explosive_pass.all", "def.int_rate.all", "def.two_high_rate.all",
            "def.comp_pct.all"]
RUN_OFF = ["off.rush_epa.all", "off.rush_success.all", "off.stuff_rate.all", "off.ypc.all", "off.rush_epa.light_box",
           "off.rush_epa.loaded_box", "off.explosive_run.all", "off.aly.all", "off.rush_epa.inside_run", "off.rush_epa.outside_run"]
RUN_DEF = ["def.rush_epa.all", "def.rush_success.all", "def.stuff_rate.all", "def.ypc.all", "def.box_avg.all",
           "def.loaded_box_rate.all", "def.explosive_run.all", "def.aly.all"]
SITUATION = ["down", "ydstogo", "yardline_100", "score_differential", "half_seconds_remaining", "qtr"]
PASS_TARGETS = {"epa": "reg", "success": "clf", "pressure": "clf", "sack": "clf", "explosive_pass": "clf", "interception": "clf"}
RUN_TARGETS = {"epa": "reg", "success": "clf", "stuff": "clf", "explosive_run": "clf"}
TEST_SEASONS = (2024, 2025)


def _asof() -> pd.DataFrame:
    return asof_fields(sorted(set(PASS_OFF + PASS_DEF + RUN_OFF + RUN_DEF)), k=120.0)


def play_frame(seasons) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = lake.load_plays(seasons)
    p = p[p["posteam"].notna() & p["defteam"].notna()].reset_index(drop=True)
    d = derive(p)
    a = _asof()
    off = a[["game_id", "team"] + PASS_OFF + RUN_OFF].rename(columns={"team": "posteam"})
    de = a[["game_id", "team"] + PASS_DEF + RUN_DEF].rename(columns={"team": "defteam"})
    gf = load_game_features()[["game_id", "home_team", "home_qb_epa", "away_qb_epa"]]
    base = pd.concat([p[["game_id", "season", "posteam", "defteam"] + SITUATION], d[
        ["dropback", "designed_run", "epa", "success", "pressure", "sack", "explosive_pass", "interception", "stuff",
         "explosive_run", "man", "light_box", "loaded_box"]]], axis=1)
    base = base.merge(off, on=["game_id", "posteam"], how="left").merge(de, on=["game_id", "defteam"], how="left")
    base = base.merge(gf, on="game_id", how="left")
    base["qb_epa"] = np.where(base["posteam"] == base["home_team"], base["home_qb_epa"], base["away_qb_epa"])
    passes = base[(base["dropback"] == 1) & base["epa"].notna()].copy()
    runs = base[(base["designed_run"] == 1) & base["epa"].notna()].copy()
    return passes, runs


PASS_X = SITUATION + PASS_OFF + PASS_DEF + ["qb_epa", "man"]
RUN_X = SITUATION + RUN_OFF + RUN_DEF + ["light_box", "loaded_box"]


def _model(kind):
    common = dict(n_estimators=300, learning_rate=0.05, max_depth=4, min_child_weight=50, subsample=0.8,
                  colsample_bytree=0.8, reg_lambda=5.0, n_jobs=4, random_state=3, tree_method="hist")
    return XGBRegressor(**common) if kind == "reg" else XGBClassifier(**common, eval_metric="logloss")


def train(verbose: bool = True) -> dict:
    passes, runs = play_frame(range(2016, 2026))
    models, report = {}, {"held_out_seasons": list(TEST_SEASONS), "pass": {}, "run": {}}
    for name, frame, X, targets in (("pass", passes, PASS_X, PASS_TARGETS), ("run", runs, RUN_X, RUN_TARGETS)):
        for t, kind in targets.items():
            f = frame[frame[t].notna()]
            tr, te = f[~f["season"].isin(TEST_SEASONS)], f[f["season"].isin(TEST_SEASONS)]
            m = _model(kind).fit(tr[X], tr[t])
            if kind == "reg":
                pred = m.predict(te[X])
                r2 = 1 - np.mean((te[t] - pred) ** 2) / np.var(te[t])
                report[name][t] = {"n_train": int(len(tr)), "n_test": int(len(te)), "r2": float(r2),
                                   "rmse": float(np.sqrt(np.mean((te[t] - pred) ** 2)))}
            else:
                pred = m.predict_proba(te[X])[:, 1]
                base = np.full(len(te), tr[t].mean())
                report[name][t] = {"n_train": int(len(tr)), "n_test": int(len(te)), "log_loss": log_loss(pred, te[t]),
                                   "log_loss_base_rate": log_loss(base, te[t]), "brier": brier(pred, te[t]),
                                   "brier_base_rate": brier(base, te[t])}
            # refit on all seasons for production
            models[(name, t)] = _model(kind).fit(f[X], f[t])
            if verbose:
                print("matchup", name, t, report[name][t], flush=True)
    sit = {"pass": passes[SITUATION].sample(3000, random_state=1), "run": runs[SITUATION].sample(3000, random_state=1)}
    import pickle
    path = settings().model_dir / "matchup_models.pkl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps({"models": models, "situations": sit, "report": report}))
    (settings().lake_dir / "matchup_validation.json").write_text(json.dumps(report, indent=1))
    return report


_CACHE = {}


def _load():
    if "m" not in _CACHE:
        import pickle
        _CACHE["m"] = pickle.loads((settings().model_dir / "matchup_models.pkl").read_bytes())
    return _CACHE["m"]


def game_matchups(game_id: str) -> dict:
    """Trained matchup estimates for both offenses in a game, with league-average-opponent baselines."""
    bundle = _load()
    models, sit = bundle["models"], bundle["situations"]
    a = _asof()
    row = a[a["game_id"] == game_id]
    if row.empty:
        raise KeyError(game_id)
    season = int(row["season"].iat[0])
    league = a[a["season"] == season][PASS_DEF + RUN_DEF].mean()
    gf = load_game_features().set_index("game_id").loc[game_id]
    out = {"game_id": game_id, "season": season, "sides": [], "report": bundle["report"],
           "method": "play-level XGB on as-of unit profiles; impact = this defense − league-average defense"}
    for _, o in row.iterrows():
        team, opp = o["team"], o["opponent"]
        drow = row[row["team"] == opp].iloc[0]
        qb = gf["home_qb_epa"] if team == gf["home_team"] else gf["away_qb_epa"]
        side = {"offense": team, "defense": opp, "pass": {}, "run": {}, "coverage": {}, "box": {}}
        for unit, OFF, DEF, X, targets in (("pass", PASS_OFF, PASS_DEF, PASS_X, PASS_TARGETS), ("run", RUN_OFF, RUN_DEF, RUN_X, RUN_TARGETS)):
            S = sit[unit].copy()
            for c in OFF:
                S[c] = o[c]
            def frame(defvals, extra=None):
                F = S.copy()
                for c in DEF:
                    F[c] = defvals[c]
                if unit == "pass":
                    F["qb_epa"] = qb
                    F["man"] = np.nan
                else:
                    F["light_box"], F["loaded_box"] = np.nan, np.nan
                for k, v in (extra or {}).items():
                    F[k] = v
                return F[X]
            this, avg = frame(drow), frame(league)
            for t, kind in targets.items():
                m = models[(unit, t)]
                f = (lambda Z: m.predict(Z)) if kind == "reg" else (lambda Z: m.predict_proba(Z)[:, 1])
                v, b = float(np.mean(f(this))), float(np.mean(f(avg)))
                side[unit][t] = {"predicted": v, "vs_league_avg_defense": b, "impact": v - b}
            if unit == "pass":
                m = models[("pass", "epa")]
                for label, val in (("man", 1.0), ("zone", 0.0)):
                    side["coverage"][label] = float(np.mean(m.predict(frame(drow, {"man": val}))))
                side["coverage"]["opponent_man_rate"] = float(drow["def.man_rate.all"])
                side["coverage"]["weighted"] = side["coverage"]["opponent_man_rate"] * side["coverage"]["man"] + (1 - side["coverage"]["opponent_man_rate"]) * side["coverage"]["zone"]
            else:
                m = models[("run", "epa")]
                for label, lb, ld in (("light_box", 1.0, 0.0), ("normal_box", 0.0, 0.0), ("loaded_box", 0.0, 1.0)):
                    side["box"][label] = float(np.mean(m.predict(frame(drow, {"light_box": lb, "loaded_box": ld}))))
                side["box"]["opponent_loaded_rate"] = float(drow["def.loaded_box_rate.all"])
        side["inputs"] = {"offense": {c: _num(o[c]) for c in PASS_OFF + RUN_OFF},
                          "defense": {c: _num(drow[c]) for c in PASS_DEF + RUN_DEF},
                          "samples": {c: _num(o[c + "__sample"]) for c in ("off.pass_epa.all", "off.rush_epa.all")}}
        out["sides"].append(side)
    return out


def _num(x):
    try:
        x = float(x)
        return None if np.isnan(x) else x
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    print(json.dumps(train(), indent=1))
