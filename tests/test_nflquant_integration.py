"""Integration tests against the built lake/models. Skipped when artifacts are absent."""
import numpy as np
import pandas as pd
import pytest

from nflquant.config import settings

LAKE = settings().lake_dir
need = lambda *names: pytest.mark.skipif(not all((LAKE / n).exists() for n in names), reason="lake artifacts not built")


@need("team_games.parquet")
def test_team_matrix_covers_all_teams_and_seasons():
    tg = pd.read_parquet(LAKE / "team_games.parquet", columns=["season", "team", "game_id"])
    assert tg["team"].nunique() == 32
    assert tg["season"].min() == 2006
    assert (tg.groupby("game_id").size() == 2).all()


@need("team_games.parquet")
def test_participation_fields_are_not_covered_outside_their_seasons():
    from nflquant.metrics.profiles import team_profile
    p = team_profile("KC", 2012)
    f = {x["id"]: x for x in p["fields"]}
    assert f["off.pressure_rate.all"]["status"] == "not_covered"
    assert f["off.pressure_rate.all"]["value"] is None
    assert f["off.epa_play.all"]["status"] == "observed"


@need("ratings.parquet", "team_games.parquet")
def test_asof_uses_only_prior_games():
    from nflquant.asof import asof_fields
    a = asof_fields(["off.epa_play.all"])
    tg = pd.read_parquet(LAKE / "team_games.parquet", columns=["game_id", "team", "season", "kickoff", "off.epa_play.all__d"])
    x = a[(a["team"] == "BUF") & (a["season"] == 2023)].sort_values("kickoff")
    y = tg[(tg["team"] == "BUF") & (tg["season"] == 2023)].sort_values("kickoff")
    assert x["off.epa_play.all__sample"].iat[0] == 0
    assert np.allclose(x["off.epa_play.all__sample"].to_numpy()[1:], np.cumsum(y["off.epa_play.all__d"].to_numpy())[:-1])


@need("game_features.parquet")
def test_projected_qb_never_reads_actual_starter_columns():
    import inspect
    from nflquant import features
    src = inspect.getsource(features.projected_qbs)
    assert "home_qb_id" not in src and "away_qb_id" not in src  # schedule's actual-starter fields are not used


@need("game_oos_predictions.parquet")
def test_walk_forward_predictions_are_out_of_sample_and_calibrated():
    oos = pd.read_parquet(LAKE / "game_oos_predictions.parquet")
    assert oos["season"].min() >= 2010
    assert oos["game_id"].is_unique
    e = oos[oos["season"] >= 2012]
    from nflquant.models.calibration import ece, log_loss
    assert log_loss(e["p_home"], e["y_win"]) < 0.66
    assert ece(e["p_home"], e["y_win"]) < 0.05
    assert np.allclose(e["home_points"] - e["away_points"], e["margin_ensemble"])
    assert np.allclose(e["home_points"] + e["away_points"], e["total_ensemble"])


@need("player_dataset.parquet")
def test_player_candidates_exclude_out_players():
    d = pd.read_parquet(LAKE / "player_dataset.parquet", columns=["report_status", "last_kickoff", "kickoff"])
    assert not d["report_status"].isin(["Out", "Doubtful"]).any()
    last, ko = pd.to_datetime(d["last_kickoff"]), pd.to_datetime(d["kickoff"])
    assert not (last >= ko).any()                 # no history at or after kickoff (leakage)
    assert last.isna().mean() < 0.001             # only projected starters with no prior NFL game lack history


@need("upcoming_game_predictions.parquet", "game_oos_predictions.parquet")
def test_api_endpoints():
    from fastapi.testclient import TestClient
    from nflquant.api import app
    c = TestClient(app)
    assert c.get("/api/q/status").status_code == 200
    t = c.get("/api/q/teams").json()
    assert len(t["teams"]) == 32 and t["field_count"] >= 400
    prof = c.get("/api/q/team/profile", params={"team": "DET", "season": 2025}).json()
    assert len(prof["fields"]) >= 400 and prof["games"] > 0
    sch = c.get("/api/q/schedule", params={"season": 2025, "week": 10}).json()
    gid = sch["games"][0]["game_id"]
    pred = c.get("/api/q/game/prediction", params={"game_id": gid}).json()
    assert 0 < pred["model"]["p_home"] < 1 and pred["source"] == "walk_forward_pregame"
    sim = c.get("/api/q/game/simulate", params={"game_id": gid, "n": 1000, "seed": 1}).json()
    assert sim["n_sims"] == 1000 and all(v is not False for v in sim["consistency"].values())
    assert abs(sim["home_points"]["mean"] - pred["model"]["home_points"]) < 1.5   # anchored to the game model
    assert c.get("/api/q/team/profile", params={"team": "DET", "season": 2025, "window": "bogus"}).status_code == 400
    assert c.get("/api/q/game/prediction", params={"game_id": "nope"}).status_code == 404
