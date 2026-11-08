"""End-to-end pipeline in dependency order. Idempotent; every step can be rerun alone.

    python -m nflquant.pipeline                       # full refresh
    python -m nflquant.pipeline --steps lake,team     # selected steps
    python -m nflquant.pipeline --refresh             # scheduled refresh: ingest (conditional GET) + rebuild + production

Steps: ingest -> lake -> team -> ratings -> features -> warehouse -> player_metrics ->
       game_models -> player_models -> matchup -> production -> sim_backtest
"""
from __future__ import annotations

import argparse
import json
import time

STEPS = ["ingest", "lake", "team", "ratings", "features", "warehouse", "player_metrics", "game_models", "player_models",
         "matchup", "production", "sim_backtest"]
REFRESH = ["ingest", "lake", "team", "ratings", "features", "warehouse", "player_metrics", "game_models", "player_dataset",
           "production"]


def run_step(name: str) -> object:
    if name == "ingest":
        from . import ingest
        return ingest.run(verbose=False)
    if name == "lake":
        from . import lake
        return lake.run(verbose=False)
    if name == "team":
        from .metrics import team
        team.load_team_games.cache_clear()
        return team.build(verbose=False).shape
    if name == "ratings":
        from . import ratings
        return ratings.build(verbose=False).shape
    if name == "features":
        from . import features
        features.qb_games.cache_clear()
        return features.build_game_features(verbose=False).shape
    if name == "warehouse":
        from . import warehouse
        return warehouse.load_core(verbose=False)
    if name == "player_metrics":
        from .metrics import player
        return player.build(verbose=False).shape
    if name == "game_models":
        from .models import game
        r = game.run()
        return {k: r["models"]["ensemble"][k] for k in ("log_loss", "brier", "margin_mae")}
    if name == "player_dataset":
        from .models import player
        return player.build_dataset(verbose=False).shape
    if name == "player_models":
        from .models import player
        r = player.run()
        return {k: round(v["skill_vs_baseline"], 3) for k, v in r["targets"].items()}
    if name == "matchup":
        from .models import matchup
        return matchup.train(verbose=False)
    if name == "production":
        from . import production
        return production.build(verbose=False)
    if name == "sim_backtest":
        from .sim import backtest
        return backtest.run(verbose=False)
    raise ValueError(name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default=",".join(STEPS))
    ap.add_argument("--refresh", action="store_true", help="scheduled refresh (no full re-backtest of player models)")
    a = ap.parse_args()
    steps = REFRESH if a.refresh else a.steps.split(",")
    for s in steps:
        t = time.time()
        out = run_step(s)
        print(f"[{time.time() - t:7.1f}s] {s}: {json.dumps(out, default=str)[:300]}", flush=True)


if __name__ == "__main__":
    main()
