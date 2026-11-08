"""FastAPI service for the Case NFL Quant terminal (read-only; no LLM at runtime).

Run:  uvicorn nflquant.api:app --host 127.0.0.1 --port 8780
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import service

STATIC = Path(__file__).resolve().parents[1] / "terminal" / "quant"
app = FastAPI(title="Case NFL Quant Intelligence", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data: https://static.www.nfl.com; "
                                               "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
    return resp


def _wrap(fn, *a, **k):
    try:
        return JSONResponse(fn(*a, **k))
    except KeyError as e:
        raise HTTPException(404, f"not found: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(503, f"artifact missing — run the pipeline step that builds it ({e.filename})")


@app.get("/api/q/status")
def status():
    return _wrap(service.status)


@app.get("/api/q/teams")
def teams():
    return _wrap(service.teams)


@app.get("/api/q/team/catalog")
def team_catalog():
    return _wrap(service.team_catalog)


@app.get("/api/q/team/profile")
def team_profile(team: str, season: int, window: str = "season", context: str = "all", opponent: str | None = None,
                 category: str | None = None, through_week: int | None = None):
    if window not in ("season", "last3", "last5", "last8"):
        raise HTTPException(400, "unknown window")
    return _wrap(service.team_profile, team, season, window, context, opponent, category, through_week)


@app.get("/api/q/team/trend")
def team_trend(team: str, season: int, field: str):
    return _wrap(service.team_trend, team, season, field)


@app.get("/api/q/players")
def players(q: str = "", team: str = "", position: str = "", season: int | None = None, limit: int = Query(60, le=300)):
    return _wrap(service.players, q[:60], team, position, season, limit)


@app.get("/api/q/player/catalog")
def player_catalog():
    return _wrap(service.player_catalog)


@app.get("/api/q/player/profile")
def player_profile(player: str, season: int, window: str = "season"):
    if window not in service.WINDOWS_P:
        raise HTTPException(400, "unknown window")
    return _wrap(service.player_profile, player, season, window)


@app.get("/api/q/schedule")
def schedule(season: int | None = None, week: int | None = None):
    return _wrap(service.schedule, season, week)


@app.get("/api/q/game/prediction")
def game_prediction(game_id: str):
    return _wrap(service.game_prediction, game_id)


@app.get("/api/q/game/simulate")
def simulate(game_id: str, n: int = Query(10000, ge=500, le=50000), seed: int = 2026, remove: str = ""):
    ids = [x for x in remove.split(",") if x][:6]
    return _wrap(service.simulate, game_id, n, seed, ids)


@app.get("/api/q/game/matchup")
def matchup(game_id: str):
    return _wrap(service.matchup, game_id)


@app.get("/api/q/audit")
def audit():
    return _wrap(service.audit)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
