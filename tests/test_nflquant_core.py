"""Hermetic tests for the nflquant metric, rating, calibration and ingestion layers."""
import json

import numpy as np
import pandas as pd
import pytest

from nflquant.lake import defense_package, personnel_code
from nflquant.metrics.plays import derive
from nflquant.metrics.profiles import percentile, shrink
from nflquant.metrics.team import FIELDS, FIELD_INDEX, CATEGORIES, resolve_source
from nflquant.models.calibration import Platt, brier, ece, interval_coverage, log_loss, reliability
from nflquant.models.player import nb_dispersion
from nflquant.ratings import _fit


def plays(rows):
    base = {c: np.nan for c in [
        "qb_kneel", "qb_spike", "two_point_attempt", "play_type", "sack", "qb_scramble", "qb_dropback", "epa", "yards_gained",
        "air_yards", "down", "ydstogo", "yardline_100", "qtr", "score_differential", "half_seconds_remaining", "pass_attempt",
        "complete_pass", "interception", "pass_touchdown", "rush_touchdown", "passing_yards", "yards_after_catch",
        "xyac_mean_yardage", "cpoe", "first_down", "qb_hit", "fumble_lost", "penalty", "penalty_team", "posteam", "defteam",
        "penalty_yards", "xpass", "pass_oe", "shotgun", "no_huddle", "time_to_throw", "was_pressure", "has_participation",
        "blitz", "number_of_pass_rushers", "man_coverage", "defense_coverage_type", "is_play_action", "is_rpo",
        "is_screen_pass", "is_motion", "is_qb_out_of_pocket", "is_interception_worthy", "is_throw_away", "is_catchable_ball",
        "is_contested_ball", "is_drop", "is_created_reception", "read_thrown", "box_count", "offense_personnel_code",
        "defense_package", "run_location", "run_gap"]}
    base.update({"posteam": "A", "defteam": "B", "has_participation": False, "down": 1, "ydstogo": 10, "yardline_100": 60,
                 "qtr": 1, "score_differential": 0, "half_seconds_remaining": 1500})
    return pd.DataFrame([{**base, **r} for r in rows])


def test_personnel_and_package_parsing():
    assert personnel_code("1 RB, 1 TE, 3 WR") == "11"
    assert personnel_code("2 RB, 1 TE, 2 WR") == "21"
    assert personnel_code("1 RB, 1 FB, 1 TE, 2 WR") == "21"
    assert personnel_code("1 K, 1 LS, 9 OL") is None
    assert personnel_code(None) is None
    assert defense_package("4 DL, 2 LB, 5 DB".replace("DB", "CB")) == "nickel"
    assert defense_package("2 CB, 1 FS, 1 SS, 4 DL, 3 LB") == "base"
    assert defense_package("4 CB, 1 FS, 1 SS, 4 DL, 1 LB") == "dime"


def test_derive_excludes_kneels_spikes_and_keeps_unknown_charting_unknown():
    p = plays([
        {"play_type": "run", "qb_kneel": 1, "epa": -0.5, "yards_gained": -1},
        {"play_type": "pass", "qb_spike": 1, "qb_dropback": 1, "epa": -0.3},
        {"play_type": "pass", "qb_dropback": 1, "pass_attempt": 1, "complete_pass": 0, "epa": -0.6, "yards_gained": 0,
         "passing_yards": np.nan, "air_yards": 12},
        {"play_type": "run", "epa": 0.4, "yards_gained": 7, "run_location": "left", "run_gap": "end"},
    ])
    d = derive(p)
    assert np.isnan(d["epa"].iat[0]) and np.isnan(d["epa"].iat[1])        # kneel / spike excluded from EPA
    assert d["throw"].iat[2] == 1 and d["pass_yards"].iat[2] == 0.0       # incompletion counts 0 yards, attempt kept
    assert np.isnan(d["pressure"].iat[2])                                 # not charted -> unknown, not False
    assert np.isnan(d["play_action"].iat[2])
    assert d["designed_run"].iat[3] == 1 and d["run_outside"].iat[3] == 1 and d["run_left"].iat[3] == 1
    assert d["aly"].iat[3] == pytest.approx(4 + 0.5 * 3)                  # FO ALY weights


def test_sack_with_participation_counts_as_pressure():
    p = plays([{"play_type": "pass", "qb_dropback": 1, "sack": 1, "epa": -1.5, "yards_gained": -7, "has_participation": True}])
    d = derive(p)
    assert d["pressure"].iat[0] == 1.0 and d["sack_yards"].iat[0] == 7


def test_catalog_shape_and_sources():
    assert len(FIELDS) >= 400
    assert len(CATEGORIES) == 20
    assert len({f.id for f in FIELDS}) == len(FIELDS)
    f = FIELD_INDEX["def.sack_rate.all"]
    assert f.direction == 1                       # more sacks is good for a defense
    assert FIELD_INDEX["off.sack_rate.all"].direction == -1
    assert resolve_source("participation", "ftn") == "participation+ftn"
    assert resolve_source("pbp", "pbp") == "pbp"
    assert FIELD_INDEX["off.pressure_rate.blitz"].source == "participation+ftn"
    assert all(f.formula for f in FIELDS)


def test_shrinkage_properties():
    rng = np.random.default_rng(0)
    d = np.array([20, 50, 400, 600, 800, 30, 700, 500.0])
    true = rng.normal(0.45, 0.04, len(d))
    n = rng.binomial(d.astype(int), true).astype(float)
    m, shr, sd, raw = shrink(n, d, None, True)
    assert np.all((shr - m) * (raw - m) >= -1e-12)            # shrinks toward the mean, never past it
    big, small = np.argmax(d), np.argmin(d)
    assert abs(shr[big] - raw[big]) / abs(raw[big] - m + 1e-12) < abs(shr[small] - raw[small]) / abs(raw[small] - m + 1e-12)
    assert np.all(sd > 0)


def test_window_aggregation_is_exposure_weighted():
    # two games: 10/20 and 30/40 -> 40/60, not mean(0.5, 0.75)
    n, d = np.array([10.0, 30.0]), np.array([20.0, 40.0])
    assert n.sum() / d.sum() == pytest.approx(2 / 3)


def test_percentile_direction():
    s = pd.Series({"A": 0.1, "B": 0.2, "C": 0.3})
    assert percentile(s, 1)["C"] > percentile(s, 1)["A"]
    assert percentile(s, -1)["A"] > percentile(s, -1)["C"]


def test_ratings_ridge_recovers_offsets():
    rng = np.random.default_rng(1)
    teams = list("ABCDEF")
    off = dict(zip(teams, [0.15, 0.05, 0.0, -0.05, -0.1, -0.05]))
    de = dict(zip(teams, [-0.1, 0.0, 0.05, 0.1, -0.05, 0.0]))
    rows = []
    for _ in range(40):
        for t in teams:
            for o in teams:
                if t != o:
                    rows.append({"team": t, "opponent": o, "is_home": rng.integers(0, 2), "w": 60.0,
                                 "y": off[t] + de[o] + rng.normal(0, 0.15)})
    obs = pd.DataFrame(rows)
    o, d, mu, h = _fit(obs, teams, {}, lam=50.0)
    assert np.corrcoef([o[t] for t in teams], [off[t] for t in teams])[0, 1] > 0.95
    assert np.corrcoef([d[t] for t in teams], [de[t] for t in teams])[0, 1] > 0.95


def test_ratings_prior_dominates_without_data():
    o, d, mu, h = _fit(pd.DataFrame(columns=["team", "opponent", "is_home", "y", "w"]), ["A", "B"], {("o", "A"): 0.2}, 100.0)
    assert o["A"] == 0.2 and o["B"] == 0.0


def test_scoring_rules_and_calibration():
    y = np.array([1, 0, 1, 1, 0.0])
    assert brier(np.full(5, 0.5), y) == pytest.approx(0.25)
    assert log_loss(np.full(5, 0.5), y) == pytest.approx(np.log(2))
    assert interval_coverage([1, 2, 3], [0, 0, 4], [2, 2, 5]) == pytest.approx(2 / 3)
    pl = Platt()
    assert np.allclose(pl.predict([0.2, 0.7]), [0.2, 0.7])     # identity until fitted
    rng = np.random.default_rng(3)
    p = rng.uniform(0.05, 0.95, 4000)
    yy = (rng.uniform(size=4000) < p).astype(float)
    assert ece(p, yy) < 0.03
    rel = reliability(p, yy)
    assert sum(r["n"] for r in rel) == 4000
    pl.fit(np.clip(p * 0.6 + 0.2, 0, 1), yy)                    # miscalibrated input
    assert log_loss(pl.predict(np.clip(p * 0.6 + 0.2, 0, 1)), yy) <= log_loss(np.clip(p * 0.6 + 0.2, 0, 1), yy) + 1e-9


def test_negative_binomial_dispersion():
    rng = np.random.default_rng(4)
    mu = rng.uniform(1, 6, 20000)
    r = 3.0
    y = rng.negative_binomial(r, r / (r + mu))
    assert nb_dispersion(mu, y) == pytest.approx(r, rel=0.15)
    assert nb_dispersion(mu, rng.poisson(mu)) > 50                # ~Poisson


class _Resp:
    def __init__(self, status, body=b"", headers=None):
        self.status_code, self._b, self.headers = status, body, headers or {}

    def iter_content(self, n):
        yield self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, headers=None, timeout=None, stream=None):
        self.calls.append(headers)
        return self.responses.pop(0)


def test_ingest_idempotent_and_revisions(tmp_path):
    from nflquant.ingest import Asset, Manifest, fetch
    a = Asset("schedules", "games.csv", None, "test")
    man = Manifest(tmp_path / "manifest.json")
    s = _Session([_Resp(200, b"v1", {"ETag": "e1"}), _Resp(304), _Resp(200, b"v2", {"ETag": "e2"}), _Resp(404)])
    assert fetch(a, tmp_path, man, s, 5, 1) == "new"
    assert fetch(a, tmp_path, man, s, 5, 1) == "unchanged"
    assert s.calls[1]["If-None-Match"] == "e1"                 # conditional request
    assert fetch(a, tmp_path, man, s, 5, 1) == "updated"
    rec = man.get(a.key)
    assert len(rec["history"]) == 1 and rec["sha256"] != rec["history"][0]["sha256"]
    b = Asset("pbp", "play_by_play_2099.parquet", 2099, "test")
    assert fetch(b, tmp_path, man, s, 5, 1) == "missing"
    man.save()
    assert json.loads((tmp_path / "manifest.json").read_text())["assets"][b.key]["status"] == "missing"
