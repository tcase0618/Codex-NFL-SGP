"""Production inference artifacts (training separated from serving).

``build()`` fits production models on every completed game and writes:
    lake/upcoming_game_predictions.parquet     game model output for unplayed games
    lake/upcoming_player_predictions.parquet   player projections for unplayed games
    models/game_production.pkl                 fitted ladder (for TreeSHAP explanations)
    lake/production_meta.json                  versions, training cutoff, timestamps

Serving (API) reads these artifacts; it never trains.
"""
from __future__ import annotations

import json
import pickle

import pandas as pd

from . import lake, warehouse
from .config import settings
from .models import game as game_model
from .models import player as player_model


def build(verbose: bool = True) -> dict:
    cfg = settings()
    fit, preds, meta = game_model.train_production(verbose)
    (cfg.model_dir).mkdir(parents=True, exist_ok=True)
    (cfg.model_dir / "game_production.pkl").write_bytes(pickle.dumps(fit))
    preds.to_parquet(cfg.lake_dir / "upcoming_game_predictions.parquet", index=False)
    if verbose:
        print("production game predictions", len(preds), flush=True)
    d = pd.read_parquet(cfg.lake_dir / "player_dataset.parquet")
    pm = player_model.ProductionModels(d)
    done = set(lake.load("games").query("completed").game_id)
    upcoming_rows = d[~d["game_id"].isin(done)]
    pp = pm.predict(upcoming_rows) if len(upcoming_rows) else pd.DataFrame()
    pp.to_parquet(cfg.lake_dir / "upcoming_player_predictions.parquet", index=False)
    meta.update({"player_model_version": "player-models-1.0", "n_upcoming_games": int(preds["game_id"].nunique()) if len(preds) else 0,
                 "n_upcoming_player_rows": int(len(pp)), "built_at": warehouse.now(),
                 "information_cutoff": meta["trained_through"],
                 "injury_note": "Upcoming-week injury statuses are included only if the official report has been published upstream."})
    (cfg.lake_dir / "production_meta.json").write_text(json.dumps(meta, indent=1, default=float))
    warehouse.init()
    with warehouse.connect() as con:
        con.execute("INSERT OR REPLACE INTO model_versions VALUES (?,?,?,?,?,?,?,?)", (
            meta["model_version"], "game_ensemble", meta["built_at"], f"all completed games through {meta['trained_through']}",
            json.dumps({"weights": meta["weights"], "sigma_margin": meta["sigma_margin"], "platt": meta["platt"]}, default=float),
            meta["model_version"].split("-")[-1], game_model.CODE_VERSION, "production fit"))
        rows = []
        for _, r in preds.iterrows():
            rows.append((meta["model_version"], r["game_id"], "home", "margin", r["margin_ensemble"], r["sigma_margin"],
                         r["margin_p10"], r["margin_p25"], r["margin_ensemble"], r["margin_p75"], r["margin_p90"], r["p_home"],
                         meta["built_at"], meta["information_cutoff"]))
        con.executemany("INSERT OR REPLACE INTO model_predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return meta


_CACHE: dict = {}


def game_fit():
    if "fit" not in _CACHE:
        _CACHE["fit"] = pickle.loads((settings().model_dir / "game_production.pkl").read_bytes())
    return _CACHE["fit"]


if __name__ == "__main__":
    print(json.dumps(build(), indent=1, default=float))
