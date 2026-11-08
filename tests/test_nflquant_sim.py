"""Hermetic simulator tests with a synthetic League (no data files needed)."""
import numpy as np
import pytest

from nflquant.sim.engine import GameSpec, Side, Sim, ot_mode
from nflquant.sim.league import League, fourth_design, pass_design
from nflquant.sim.build import summarize


def league(season=2024):
    rng = np.random.default_rng(0)
    k4 = fourth_design([1], [50], [0], [1800]).shape[1]
    clock = {(c, h): np.full(200, 28.0 if h == 0 else 12.0, np.float32) for c in ("run", "sack", "scramble", "complete", "incomplete") for h in (0, 1)}
    return League(
        season=season, pass_coef=np.zeros(pass_design([1], [10], [50], [0], [900], [1]).shape[1]), pass_intercept=0.3,
        fourth_coef=np.array([np.r_[-1.5, np.zeros(k4 - 1)], np.r_[0.0, -4.0, np.zeros(k4 - 2)], np.r_[0.5, np.zeros(k4 - 1)]]),
        fourth_intercept=np.array([-1.0, 1.0, 0.5]), fg_coef=(6.0, -0.11, 0.0), xp_rate=0.94, two_pt_rate=0.08,
        two_pt_success=0.48,
        rates={"sack": 0.065, "scramble": 0.04, "complete": 0.64, "int": 0.022, "untargeted": 0.04, "fumble_lost_run": 0.006,
               "fumble_lost_comp": 0.006, "fumble_lost_sack": 0.05, "pen_off": 0.04, "pen_def": 0.035, "pbu_per_incomplete": 0.3,
               "tackled_comp": 0.9, "tackled_run": 0.95, "assist_per_tackle": 0.4, "ypc": 4.3, "ypcomp": 11.0,
               "ko_returned": 0.4, "punt_returned": 0.5},
        dists={"comp_yards": rng.gamma(2.0, 5.5, 5000).astype(np.float32), "rush_yards": (rng.gamma(1.6, 3.0, 5000) - 0.6).astype(np.float32),
               "scramble_yards": rng.gamma(2.0, 3.5, 2000).astype(np.float32), "sack_yards": -rng.gamma(3, 2.3, 2000).astype(np.float32),
               "ko_start": np.full(500, 70.0, np.float32), "punt_net_by_yl": {b: np.full(100, 41.0, np.float32) for b in range(3, 10)},
               "int_air": np.full(100, 12.0, np.float32), "int_ret": np.full(100, 8.0, np.float32),
               "pen_off_yards": np.full(50, 7.0, np.float32), "pen_def_yards": np.full(50, 8.0, np.float32),
               "ko_return_yards": np.full(50, 24.0, np.float32), "punt_return_yards": np.full(50, 9.0, np.float32)},
        clock=clock, windows=(season - 3, season - 1))


def side(team, tag):
    return Side(team=team, qb=f"{tag}QB",
                receivers=[(f"{tag}WR1", 8, 0.1, 1.1), (f"{tag}WR2", 6, 0.0, 1.0), (f"{tag}TE", 4, 0.1, 0.9), (f"{tag}RB", 3, 0.4, 0.7)],
                rushers=[(f"{tag}RB", 16, 1.0), (f"{tag}RB2", 5, 0.95), (f"{tag}QB", 2, 1.2)],
                defenders=[(f"{tag}D{i}", 5 - 0.3 * i, 0.3, 0.2, 0.05, 0.3, 0.05) for i in range(11)],
                kicker=f"{tag}K", punter=f"{tag}P", kick_returner=f"{tag}KR", punt_returner=f"{tag}PR")


def spec(season=2024, game_type="REG"):
    return GameSpec(season, game_type, side("HOM", "h"), side("AWY", "a"), league(season))


def test_seeded_reproducibility():
    a = Sim(spec(), 400, seed=11).run()
    b = Sim(spec(), 400, seed=11).run()
    c = Sim(spec(), 400, seed=12).run()
    assert np.array_equal(a.score, b.score)
    assert not np.array_equal(a.score, c.score)


def test_games_terminate_and_scores_are_plausible():
    s = Sim(spec(), 2000, seed=3).run()
    assert s.done.all()
    pts = s.ts["points"]
    assert 10 < pts.mean() < 35
    assert pts.min() >= 0
    assert 45 < s.ts["plays"].mean() < 85


def test_accounting_identities_hold_in_every_simulation():
    sp = spec()
    s = Sim(sp, 1500, seed=5).run()
    assert np.array_equal(s.ps["rec_yds"].sum(1), s.ts["pass_yds"].sum(1))
    assert np.array_equal(s.ps["rush_yds"].sum(1), s.ts["rush_yds"].sum(1))
    assert np.array_equal(s.ps["pass_att"].sum(1), s.ts["pass_att"].sum(1))
    assert np.array_equal(s.ps["rec"].sum(1), s.ts["pass_comp"].sum(1))
    assert np.array_equal(s.score.sum(1), s.ts["points"].sum(1))
    assert np.all(s.ps["rec"] <= s.ps["targets"])
    # passing yards credited to QBs equal receiving yards credited to receivers
    assert np.array_equal(s.ps["pass_yds"].sum(1), s.ps["rec_yds"].sum(1))
    summ = summarize(s, sp)
    assert all(v is not False for v in summ["consistency"].values())


def test_td_attribution_matches_team_touchdowns():
    s = Sim(spec(), 1500, seed=6).run()
    offensive_td = s.ps["rec_td"].sum(1) + s.ps["rush_td"].sum(1)
    assert np.array_equal(offensive_td, s.ts["td"].sum(1))
    assert np.array_equal(s.ps["pass_td"].sum(1), s.ps["rec_td"].sum(1))


def test_efficiency_scalar_moves_points_monotonically():
    lo, hi = spec(), spec()
    lo.home.eff, hi.home.eff = 0.8, 1.25
    a = Sim(lo, 1500, seed=8).run().ts["points"][:, 0].mean()
    b = Sim(hi, 1500, seed=8).run().ts["points"][:, 0].mean()
    assert b > a + 3


def test_overtime_rules_by_season():
    assert ot_mode(2010, "REG") == ("sd", 900)
    assert ot_mode(2016, "REG") == ("sd_td", 900)
    assert ot_mode(2018, "REG") == ("sd_td", 600)
    assert ot_mode(2025, "REG") == ("both", 600)
    assert ot_mode(2021, "POST") == ("sd_td", 900)
    assert ot_mode(2023, "POST") == ("both", 900)


def test_regular_season_allows_ties_playoffs_do_not():
    reg = Sim(spec(2024, "REG"), 3000, seed=9).run()
    post = Sim(spec(2024, "POST"), 3000, seed=9).run()
    assert reg.overtime.any()
    assert np.all(post.score[:, 0] != post.score[:, 1])
    # synthetic league: only checks ties stay rare; real-data OT/tie rates are measured in sim/backtest.py
    assert (reg.score[:, 0] == reg.score[:, 1]).mean() < 0.06


def test_removed_receiver_gets_no_targets():
    sp = spec()
    sp.home.receivers = [r for r in sp.home.receivers if r[0] != "hWR1"]
    s = Sim(sp, 500, seed=10).run()
    assert "hWR1" not in s.pidx or s.ps["targets"][:, s.pidx["hWR1"]].sum() == 0


def test_summary_quantiles_ordered():
    sp = spec()
    s = Sim(sp, 1000, seed=12).run()
    out = summarize(s, sp)
    for k in ("home_points", "margin", "total"):
        d = out[k]
        assert d["p10"] <= d["p25"] <= d["median"] <= d["p75"] <= d["p90"]
    assert abs(out["win_prob_home"] + out["win_prob_away"] + out["tie_prob"] - 1) < 1e-9
