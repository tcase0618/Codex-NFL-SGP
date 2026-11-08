"""Normalized SQLite warehouse (portable DDL; PostgreSQL-compatible types).

SQLite is used because the existing stack is SQLite-based, the terminal is a
single-node read-mostly service, and it keeps deployment free of database ops.
Large analytical tables (plays, participation, team-game field matrix) live in
Parquet under ``data/lake``; the ``plays``/``drives``/``play_events``/``formations``/
``personnel``/``player_participation`` entities are exposed as registered lake
datasets (``lake_datasets``) rather than duplicated row-by-row.

Tables whose source data is not publicly available are created with their
proposed schema and listed in ``entity_status`` as ``reserved`` (e.g. licensed
PFF-style ``play_charting`` grades, true coach play-calling attribution).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import pandas as pd

from .config import settings

DDL = """
CREATE TABLE IF NOT EXISTS data_sources (
  source_key TEXT PRIMARY KEY, url TEXT, release TEXT, season INTEGER, sha256 TEXT, bytes INTEGER,
  retrieved_at TEXT, checked_at TEXT, etag TEXT, last_modified TEXT, license TEXT, status TEXT, revisions INTEGER);
CREATE TABLE IF NOT EXISTS teams (team TEXT PRIMARY KEY, historical_codes TEXT);
CREATE TABLE IF NOT EXISTS players (
  player_id TEXT PRIMARY KEY, display_name TEXT, position TEXT, position_group TEXT, height REAL, weight REAL,
  birth_date TEXT, college TEXT, rookie_season INTEGER, last_season INTEGER, latest_team TEXT, status TEXT,
  draft_year INTEGER, draft_round INTEGER, draft_pick INTEGER, draft_team TEXT, years_of_experience INTEGER,
  pfr_id TEXT, espn_id TEXT, headshot TEXT);
CREATE TABLE IF NOT EXISTS games (
  game_id TEXT PRIMARY KEY, season INTEGER, week INTEGER, game_type TEXT, kickoff TEXT, home_team TEXT,
  away_team TEXT, home_score REAL, away_score REAL, result REAL, total REAL, overtime REAL, home_rest REAL,
  away_rest REAL, roof TEXT, surface TEXT, stadium_id TEXT, div_game REAL, away_travel_miles REAL,
  spread_line REAL, total_line REAL, home_moneyline REAL, away_moneyline REAL);
CREATE TABLE IF NOT EXISTS weather (game_id TEXT PRIMARY KEY, roof TEXT, temp REAL, wind REAL, source TEXT);
CREATE TABLE IF NOT EXISTS team_seasons (season INTEGER, team TEXT, games INTEGER, wins REAL, points_for REAL,
  points_against REAL, PRIMARY KEY (season, team));
CREATE TABLE IF NOT EXISTS player_seasons (player_id TEXT, season INTEGER, team TEXT, position TEXT, games INTEGER,
  PRIMARY KEY (player_id, season, team));
CREATE TABLE IF NOT EXISTS player_games (player_id TEXT, game_id TEXT, season INTEGER, week INTEGER, team TEXT,
  opponent TEXT, position TEXT, stats_json TEXT, PRIMARY KEY (player_id, game_id));
CREATE TABLE IF NOT EXISTS depth_charts (season INTEGER, week INTEGER, team TEXT, player_id TEXT, position TEXT,
  depth INTEGER, snapshot_time TEXT);
CREATE TABLE IF NOT EXISTS injuries (season INTEGER, week INTEGER, team TEXT, player_id TEXT, position TEXT,
  report_status TEXT, practice_status TEXT, primary_injury TEXT, date_modified TEXT);
CREATE TABLE IF NOT EXISTS coaches (season INTEGER, team TEXT, head_coach TEXT, games INTEGER, PRIMARY KEY (season, team));
CREATE TABLE IF NOT EXISTS lake_datasets (entity TEXT PRIMARY KEY, path TEXT, rows INTEGER, description TEXT, built_at TEXT);
CREATE TABLE IF NOT EXISTS play_charting (game_id TEXT, play_id REAL, provider TEXT, field TEXT, value TEXT,
  license TEXT, imported_at TEXT);
CREATE TABLE IF NOT EXISTS metric_definitions (metric_id TEXT PRIMARY KEY, scope TEXT, label TEXT, category TEXT,
  unit TEXT, direction INTEGER, formula TEXT, denominator TEXT, source TEXT, coverage TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS player_metrics (player_id TEXT, season INTEGER, window TEXT, metric_id TEXT, value REAL,
  sample REAL, percentile REAL, PRIMARY KEY (player_id, season, window, metric_id));
CREATE TABLE IF NOT EXISTS team_metrics (team TEXT, season INTEGER, window TEXT, metric_id TEXT, value REAL,
  sample REAL, shrunk REAL, percentile REAL, PRIMARY KEY (team, season, window, metric_id));
CREATE TABLE IF NOT EXISTS matchup_features (game_id TEXT PRIMARY KEY, features_json TEXT, built_at TEXT);
CREATE TABLE IF NOT EXISTS model_features (model_id TEXT, feature TEXT, description TEXT, PRIMARY KEY (model_id, feature));
CREATE TABLE IF NOT EXISTS model_versions (model_version TEXT PRIMARY KEY, model_id TEXT, created_at TEXT,
  train_seasons TEXT, params_json TEXT, data_hash TEXT, code_version TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS model_predictions (model_version TEXT, game_id TEXT, entity TEXT, target TEXT,
  mean REAL, sd REAL, p10 REAL, p25 REAL, p50 REAL, p75 REAL, p90 REAL, prob REAL, created_at TEXT,
  information_cutoff TEXT, PRIMARY KEY (model_version, game_id, entity, target));
CREATE TABLE IF NOT EXISTS simulation_runs (run_id TEXT PRIMARY KEY, game_id TEXT, n_sims INTEGER, seed INTEGER,
  model_version TEXT, created_at TEXT, summary_json TEXT, elapsed_s REAL);
CREATE TABLE IF NOT EXISTS simulation_events (run_id TEXT, sim INTEGER, seq INTEGER, event_json TEXT);
CREATE TABLE IF NOT EXISTS validation_results (model_version TEXT, evaluation TEXT, segment TEXT, metric TEXT,
  value REAL, n INTEGER, created_at TEXT, PRIMARY KEY (model_version, evaluation, segment, metric));
CREATE TABLE IF NOT EXISTS entity_status (entity TEXT PRIMARY KEY, status TEXT, note TEXT);
"""

ENTITY_STATUS = {
    "players": ("populated", "nflverse players.parquet"),
    "player_seasons": ("populated", "derived from player_games"),
    "player_games": ("populated", "nflverse stats_player weekly + PFR snap counts (2012+)"),
    "teams": ("populated", "32 franchises, historical codes mapped"),
    "team_seasons": ("populated", "derived from games"),
    "games": ("populated", "nflverse schedules 2006+"),
    "drives": ("lake", "derived from plays (fixed_drive) in team metric engine"),
    "plays": ("lake", "data/lake/plays/{season}.parquet"),
    "play_events": ("lake", "player-event columns on plays (passer/receiver/rusher/tacklers/sacks/PBU)"),
    "player_participation": ("lake", "on-field GSIS IDs 2016-2025; 2026 not yet published upstream"),
    "personnel": ("lake", "offense/defense personnel strings 2016-2025"),
    "formations": ("lake", "offense_formation 2016-2025; shotgun/no-huddle all seasons"),
    "depth_charts": ("populated", "nflverse weekly depth charts; 2025+ timestamped snapshots"),
    "injuries": ("populated", "official injury reports 2009+"),
    "coaches": ("populated", "head coach per team-season from schedules; coordinators not in public feed"),
    "play_charting": ("reserved", "licensed PFF-style grades/assignments not available; FTN public charting is joined on plays"),
    "player_metrics": ("populated", "materialized on demand by the player metric engine"),
    "team_metrics": ("populated", "materialized season snapshots of the team field library"),
    "matchup_features": ("populated", "pregame game features"),
    "weather": ("populated", "schedule temp/wind/roof; not a verified pregame forecast"),
    "metric_definitions": ("populated", "team + player catalogs"),
    "data_sources": ("populated", "ingestion manifest"),
    "model_features": ("populated", "feature lists per model"),
    "model_versions": ("populated", "one row per trained model version"),
    "model_predictions": ("populated", "walk-forward pregame predictions"),
    "simulation_runs": ("populated", "per simulation request"),
    "simulation_events": ("reserved", "event logs stored only for debug runs (size)"),
    "validation_results": ("populated", "backtest metrics"),
}


@contextmanager
def connect():
    path = settings().warehouse
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init() -> None:
    with connect() as con:
        con.executescript(DDL)
        con.executemany("INSERT OR REPLACE INTO entity_status VALUES (?,?,?)",
                        [(k, v[0], v[1]) for k, v in ENTITY_STATUS.items()])


def load_core(verbose: bool = True) -> dict:
    from . import lake
    from .metrics.team import FIELDS
    init()
    cfg = settings()
    manifest = json.loads((cfg.raw_dir / "manifest.json").read_text())["assets"]
    counts = {}
    with connect() as con:
        con.execute("DELETE FROM data_sources")
        con.executemany("INSERT INTO data_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (k, v.get("url"), v.get("release"), v.get("season"), v.get("sha256"), v.get("bytes"), v.get("retrieved_at"),
             v.get("checked_at"), v.get("etag"), v.get("last_modified"), v.get("license"), v.get("status"),
             1 + len(v.get("history", []))) for k, v in manifest.items()])
        g = lake.load("games")
        cols = ["game_id", "season", "week", "game_type", "kickoff", "home_team", "away_team", "home_score", "away_score",
                "result", "total", "overtime", "home_rest", "away_rest", "roof", "surface", "stadium_id", "div_game",
                "away_travel_miles", "spread_line", "total_line", "home_moneyline", "away_moneyline"]
        gg = g[cols].copy()
        gg["kickoff"] = gg["kickoff"].astype(str)
        con.execute("DELETE FROM games")
        con.executemany(f"INSERT INTO games VALUES ({','.join('?' * len(cols))})", _rows(gg))
        con.execute("DELETE FROM weather")
        con.executemany("INSERT INTO weather VALUES (?,?,?,?,?)",
                        _rows(g[["game_id", "roof", "temp", "wind"]].assign(source="nflverse schedules")))
        hist = {}
        for c in ("home_team", "away_team"):
            for cur, orig in zip(g[c], g[c + "_orig"]):
                hist.setdefault(cur, set()).add(orig)
        con.execute("DELETE FROM teams")
        con.executemany("INSERT INTO teams VALUES (?,?)", [(t, ",".join(sorted(v))) for t, v in sorted(hist.items())])
        done = g[g["result"].notna()]
        ts = pd.concat([
            pd.DataFrame({"season": done.season, "team": done.home_team, "w": (done.result > 0) + 0.5 * (done.result == 0),
                          "pf": done.home_score, "pa": done.away_score}),
            pd.DataFrame({"season": done.season, "team": done.away_team, "w": (done.result < 0) + 0.5 * (done.result == 0),
                          "pf": done.away_score, "pa": done.home_score})])
        ts = ts.groupby(["season", "team"]).agg(games=("w", "size"), wins=("w", "sum"), pf=("pf", "sum"), pa=("pa", "sum")).reset_index()
        con.execute("DELETE FROM team_seasons")
        con.executemany("INSERT INTO team_seasons VALUES (?,?,?,?,?,?)", _rows(ts))
        coaches = pd.concat([g[["season", "home_team", "home_coach"]].set_axis(["season", "team", "coach"], axis=1),
                             g[["season", "away_team", "away_coach"]].set_axis(["season", "team", "coach"], axis=1)])
        coaches = coaches.dropna().groupby(["season", "team"])["coach"].agg(lambda x: x.mode().iat[0]).reset_index()
        cg = coaches.merge(ts[["season", "team", "games"]], on=["season", "team"], how="left")
        con.execute("DELETE FROM coaches")
        con.executemany("INSERT INTO coaches VALUES (?,?,?,?)", _rows(cg))
        pl = lake.load("players")
        pcols = ["gsis_id", "display_name", "position", "position_group", "height", "weight", "birth_date", "college_name",
                 "rookie_season", "last_season", "latest_team", "status", "draft_year", "draft_round", "draft_pick",
                 "draft_team", "years_of_experience", "pfr_id", "espn_id", "headshot"]
        con.execute("DELETE FROM players")
        con.executemany(f"INSERT OR REPLACE INTO players VALUES ({','.join('?' * len(pcols))})",
                        _rows(pl[pcols].dropna(subset=["gsis_id"])))
        pg = lake.load("player_games")
        ps = pg.groupby(["player_id", "season", "team"]).agg(position=("position", "first"), games=("game_id", "nunique")).reset_index()
        con.execute("DELETE FROM player_seasons")
        con.executemany("INSERT OR REPLACE INTO player_seasons VALUES (?,?,?,?,?)", _rows(ps))
        inj = lake.load("injuries")
        con.execute("DELETE FROM injuries")
        con.executemany("INSERT INTO injuries VALUES (?,?,?,?,?,?,?,?,?)", _rows(inj[[
            "season", "week", "team", "gsis_id", "position", "report_status", "practice_status",
            "report_primary_injury", "date_modified"]]))
        con.execute("DELETE FROM metric_definitions")
        con.executemany("INSERT INTO metric_definitions VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
            (f.id, "team", f.label, f.category, f.unit, f.direction, f.formula, f.denominator, f.source, f.coverage(),
             "implemented") for f in FIELDS])
        try:
            from .metrics.player import catalog_rows
            con.executemany("INSERT OR REPLACE INTO metric_definitions VALUES (?,?,?,?,?,?,?,?,?,?,?)", catalog_rows())
        except ImportError:
            pass
        con.execute("DELETE FROM lake_datasets")
        for entity, path, desc in (("plays", "lake/plays", "pbp + participation + FTN per season"),
                                   ("team_games", "lake/team_games.parquet", "team-game field matrix"),
                                   ("ratings", "lake/ratings.parquet", "as-of opponent-adjusted ratings"),
                                   ("game_features", "lake/game_features.parquet", "pregame game features"),
                                   ("player_games", "lake/player_games.parquet", "player box + snaps")):
            con.execute("INSERT INTO lake_datasets VALUES (?,?,?,?,?)", (entity, path, None, desc, now()))
        for t in ("games", "players", "player_seasons", "injuries", "teams", "team_seasons", "coaches", "metric_definitions", "data_sources"):
            counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    if verbose:
        print(counts)
    return counts


def _rows(df: pd.DataFrame):
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].astype(str)
    df = df.astype(object).where(pd.notna(df), None)
    return [tuple(r) for r in df.itertuples(index=False, name=None)]


def query(sql: str, params=()) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(sql, params)]


if __name__ == "__main__":
    load_core()
