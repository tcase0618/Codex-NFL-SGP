"""Per-play derived indicators shared by the team and player metric engines.

All indicators are float arrays where NaN means *unknown* (field not charted for
that play/season) and 0 means *observed false*. A metric whose numerator or
denominator depends on a NaN indicator simply excludes that play, so missing
charting can never be counted as zero production.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)

TWO_HIGH = {"COVER_2", "COVER_4", "COVER_6", "2_MAN"}
SINGLE_HIGH = {"COVER_0", "COVER_1", "COVER_3"}


def _f(s) -> np.ndarray:
    return pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)


def derive(p: pd.DataFrame) -> pd.DataFrame:
    """Return a frame of derived indicators aligned with ``p``'s index."""
    d = pd.DataFrame(index=p.index)
    pt = p["play_type"].astype("string")
    kneel = _f(p["qb_kneel"]) == 1
    spike = _f(p["qb_spike"]) == 1
    two_pt = _f(p["two_point_attempt"]) == 1
    is_pass_play = (pt == "pass").fillna(False).to_numpy()
    is_run_play = (pt == "run").fillna(False).to_numpy()
    sack = _f(p["sack"]) == 1
    scramble = _f(p["qb_scramble"]) == 1
    dropback = (_f(p["qb_dropback"]) == 1) & ~spike & ~two_pt
    scrim = (is_pass_play | is_run_play) & ~kneel & ~spike & ~two_pt
    epa = _f(p["epa"])
    yards = _f(p["yards_gained"])
    air = _f(p["air_yards"])
    down = _f(p["down"])
    togo = _f(p["ydstogo"])
    yl = _f(p["yardline_100"])
    qtr = _f(p["qtr"])
    sd = _f(p["score_differential"])
    hsr = _f(p["half_seconds_remaining"])

    d["scrim"] = scrim.astype(float)
    d["dropback"] = dropback.astype(float)
    d["throw"] = (dropback & (_f(p["pass_attempt"]) == 1) & ~sack).astype(float)
    d["designed_run"] = (is_run_play & ~kneel & ~two_pt & ~scramble).astype(float)
    d["scramble"] = scramble.astype(float)
    d["sack"] = sack.astype(float)
    d["epa"] = np.where(scrim & ~np.isnan(epa), epa, np.nan)
    d["success"] = np.where(scrim & ~np.isnan(epa), (epa > 0).astype(float), np.nan)
    d["yards"] = np.where(scrim, yards, np.nan)
    d["first_down"] = np.where(scrim, (_f(p["first_down"]) == 1).astype(float), np.nan)
    thr = d["throw"].to_numpy() == 1
    run = d["designed_run"].to_numpy() == 1
    d["complete"] = np.where(thr, (_f(p["complete_pass"]) == 1).astype(float), np.nan)
    d["interception"] = np.where(thr, (_f(p["interception"]) == 1).astype(float), np.nan)
    d["pass_td"] = np.where(thr, (_f(p["pass_touchdown"]) == 1).astype(float), np.nan)
    d["rush_td"] = np.where(run | scramble, (_f(p["rush_touchdown"]) == 1).astype(float), np.nan)
    # passing yards: incompletions contribute 0, unknown stays NaN
    py = _f(p["passing_yards"])
    d["pass_yards"] = np.where(thr, np.where(d["complete"].to_numpy() == 1, py, 0.0), np.nan)
    d["air_yards"] = np.where(thr, air, np.nan)
    d["yac"] = np.where(thr & (d["complete"].to_numpy() == 1), _f(p["yards_after_catch"]), np.nan)
    d["yac_oe"] = d["yac"].to_numpy() - np.where(thr & (d["complete"].to_numpy() == 1), _f(p["xyac_mean_yardage"]), np.nan)
    d["cpoe"] = np.where(thr, _f(p["cpoe"]), np.nan)
    d["deep"] = np.where(thr & ~np.isnan(air), (air >= 20).astype(float), np.nan)
    d["behind_los"] = np.where(thr & ~np.isnan(air), (air < 0).astype(float), np.nan)
    d["short"] = np.where(thr & ~np.isnan(air), ((air >= 0) & (air < 10)).astype(float), np.nan)
    d["intermediate"] = np.where(thr & ~np.isnan(air), ((air >= 10) & (air < 20)).astype(float), np.nan)
    d["explosive_pass"] = np.where(dropback, ((yards >= 20) & ~sack).astype(float), np.nan)
    d["explosive_run"] = np.where(run, (yards >= 10).astype(float), np.nan)
    d["explosive"] = np.where(scrim, (((yards >= 20) & dropback & ~sack) | ((yards >= 10) & ~dropback)).astype(float), np.nan)
    d["stuff"] = np.where(run, (yards <= 0).astype(float), np.nan)
    d["rush_yards"] = np.where(run | scramble, _f(p["yards_gained"]), np.nan)
    # Adjusted line yards (Football Outsiders public formula): <0: 1.2x, 0-4: 1x, 5-10: 0.5x, >10: 0
    ry = np.where(run, yards, np.nan)
    d["aly"] = np.where(np.isnan(ry), np.nan, np.where(ry < 0, 1.2 * ry, np.where(ry <= 4, ry, np.where(ry <= 10, 4 + 0.5 * (ry - 4), 7.0))))
    d["sack_yards"] = np.where(sack, -yards, 0.0)
    d["qb_hit"] = np.where(dropback, ((_f(p["qb_hit"]) == 1) | sack).astype(float), np.nan)
    fl = (_f(p["fumble_lost"]) == 1)
    d["turnover"] = np.where(scrim, ((_f(p["interception"]) == 1) | fl).astype(float), np.nan)
    d["fumble_lost"] = np.where(scrim, fl.astype(float), np.nan)
    d["penalty_off"] = ((_f(p["penalty"]) == 1) & (p["penalty_team"] == p["posteam"]).to_numpy()).astype(float)
    d["penalty_def"] = ((_f(p["penalty"]) == 1) & (p["penalty_team"] == p["defteam"]).to_numpy()).astype(float)
    d["penalty_yards_off"] = np.where(d["penalty_off"] == 1, _f(p["penalty_yards"]), 0.0)
    d["penalty_yards_def"] = np.where(d["penalty_def"] == 1, _f(p["penalty_yards"]), 0.0)
    d["xpass"] = np.where(scrim, _f(p["xpass"]), np.nan)
    d["pass_oe"] = np.where(scrim, _f(p["pass_oe"]) / 100.0, np.nan)
    d["is_dropback_play"] = np.where(scrim, dropback.astype(float), np.nan)
    d["shotgun"] = np.where(scrim, _f(p["shotgun"]), np.nan)
    d["no_huddle"] = np.where(scrim, _f(p["no_huddle"]), np.nan)
    # Situations
    d["third_down"] = np.where(scrim & (down == 3), 1.0, 0.0)
    d["third_conv"] = np.where(down == 3, _f(p["first_down"]) == 1, np.nan).astype(float)
    d["fourth_down_play"] = np.where(scrim & (down == 4), 1.0, 0.0)
    d["fourth_conv"] = np.where(scrim & (down == 4), (_f(p["first_down"]) == 1).astype(float), np.nan)
    d["short_yardage"] = np.where(scrim & np.isin(down, [3, 4]) & (togo <= 2), 1.0, 0.0)
    # Charting (participation 2016-2025, FTN 2022+)
    ttt = _f(p["time_to_throw"])
    d["time_to_throw"] = np.where(dropback & (ttt > 0), ttt, np.nan)
    d["pressure"] = np.where(dropback, _f(p["was_pressure"]), np.nan)
    d["pressure"] = np.where(dropback & sack & np.isnan(d["pressure"].to_numpy()) & p["has_participation"].to_numpy(), 1.0, d["pressure"])
    d["blitz"] = np.where(dropback, _f(p["blitz"]), np.nan)
    d["pass_rushers"] = np.where(dropback, _f(p["number_of_pass_rushers"]).clip(min=None), np.nan)
    d["pass_rushers"] = np.where(d["pass_rushers"] > 0, d["pass_rushers"], np.nan)
    d["man"] = np.where(dropback, _f(p["man_coverage"]), np.nan)
    cov = p["defense_coverage_type"].astype("string")
    known_cov = cov.notna().to_numpy() & dropback
    d["two_high"] = np.where(known_cov, cov.isin(TWO_HIGH).fillna(False).to_numpy().astype(float), np.nan)
    for c in ("COVER_0", "COVER_1", "COVER_2", "COVER_3", "COVER_4", "COVER_6"):
        d["cov_" + c[-1]] = np.where(known_cov, (cov == c).fillna(False).to_numpy().astype(float), np.nan)
    d["play_action"] = np.where(dropback, _f(p["is_play_action"]), np.nan)
    d["rpo"] = np.where(scrim, _f(p["is_rpo"]), np.nan)
    d["screen"] = np.where(thr, _f(p["is_screen_pass"]), np.nan)
    d["motion"] = np.where(scrim, _f(p["is_motion"]), np.nan)
    d["out_of_pocket"] = np.where(dropback, _f(p["is_qb_out_of_pocket"]), np.nan)
    d["int_worthy"] = np.where(thr, _f(p["is_interception_worthy"]), np.nan)
    d["throwaway"] = np.where(thr, _f(p["is_throw_away"]), np.nan)
    d["catchable"] = np.where(thr, _f(p["is_catchable_ball"]), np.nan)
    d["contested"] = np.where(thr, _f(p["is_contested_ball"]), np.nan)
    d["drop"] = np.where(thr, _f(p["is_drop"]), np.nan)
    d["created_rec"] = np.where(thr, _f(p["is_created_reception"]), np.nan)
    rt = p["read_thrown"].astype("string")
    known_read = thr & rt.isin(["1", "2", "CHK"]).fillna(False).to_numpy()
    d["first_read"] = np.where(known_read, (rt == "1").fillna(False).to_numpy().astype(float), np.nan)
    d["checkdown"] = np.where(known_read, (rt == "CHK").fillna(False).to_numpy().astype(float), np.nan)
    box = _f(p["box_count"])
    d["box"] = np.where(run & (box > 0), box, np.nan)
    d["light_box"] = np.where(run & (box > 0), (box <= 6).astype(float), np.nan)
    d["loaded_box"] = np.where(run & (box > 0), (box >= 8).astype(float), np.nan)
    pers = p["offense_personnel_code"].astype("string")
    known_pers = scrim & pers.notna().to_numpy()
    for code in ("11", "12", "21", "13", "10", "22"):
        d["pers_" + code] = np.where(known_pers, (pers == code).fillna(False).to_numpy().astype(float), np.nan)
    pkg = p["defense_package"].astype("string")
    known_pkg = scrim & pkg.notna().to_numpy()
    for name in ("base", "nickel", "dime"):
        d["pkg_" + name] = np.where(known_pkg, (pkg == name).fillna(False).to_numpy().astype(float), np.nan)
    rl = p["run_location"].astype("string")
    rg = p["run_gap"].astype("string")
    known_dir = run & rl.notna().to_numpy()
    d["run_inside"] = np.where(known_dir, ((rl == "middle") | (rg == "guard")).fillna(False).to_numpy().astype(float), np.nan)
    d["run_outside"] = np.where(known_dir, (rg == "end").fillna(False).to_numpy().astype(float), np.nan)
    d["run_left"] = np.where(known_dir, (rl == "left").fillna(False).to_numpy().astype(float), np.nan)
    d["run_right"] = np.where(known_dir, (rl == "right").fillna(False).to_numpy().astype(float), np.nan)
    d["run_middle"] = np.where(known_dir, (rl == "middle").fillna(False).to_numpy().astype(float), np.nan)
    # Situational split masks (bool)
    d["s_early_down"] = scrim & np.isin(down, [1, 2])
    d["s_third_down"] = scrim & (down == 3)
    d["s_red_zone"] = scrim & (yl <= 20)
    d["s_goal_line"] = scrim & (yl <= 5)
    d["s_first_half"] = scrim & (qtr <= 2)
    d["s_second_half"] = scrim & (qtr >= 3) & (qtr <= 4)
    d["s_neutral"] = scrim & (np.abs(sd) <= 7) & (qtr <= 3)
    d["s_leading"] = scrim & (sd > 0)
    d["s_trailing"] = scrim & (sd < 0)
    d["s_two_minute"] = scrim & (hsr <= 120)
    d["s_q1"] = scrim & (qtr == 1)
    d["s_q4"] = scrim & (qtr == 4)
    d["s_pressure"] = dropback & (d["pressure"].to_numpy() == 1)
    d["s_clean"] = dropback & (d["pressure"].to_numpy() == 0)
    d["s_blitz"] = dropback & (d["blitz"].to_numpy() == 1)
    d["s_no_blitz"] = dropback & (d["blitz"].to_numpy() == 0)
    d["s_man"] = dropback & (d["man"].to_numpy() == 1)
    d["s_zone"] = dropback & (d["man"].to_numpy() == 0)
    d["s_play_action"] = dropback & (d["play_action"].to_numpy() == 1)
    d["s_no_play_action"] = dropback & (d["play_action"].to_numpy() == 0)
    d["s_light_box"] = run & (d["light_box"].to_numpy() == 1)
    d["s_loaded_box"] = run & (d["loaded_box"].to_numpy() == 1)
    d["s_pers_11"] = scrim & (d["pers_11"].to_numpy() == 1)
    d["s_pers_12"] = scrim & (d["pers_12"].to_numpy() == 1)
    d["s_pers_21"] = scrim & (d["pers_21"].to_numpy() == 1)
    d["s_vs_base"] = scrim & (d["pkg_base"].to_numpy() == 1)
    d["s_vs_nickel"] = scrim & (d["pkg_nickel"].to_numpy() == 1)
    d["s_vs_dime"] = scrim & (d["pkg_dime"].to_numpy() == 1)
    d["s_two_high"] = dropback & (d["two_high"].to_numpy() == 1)
    d["s_single_high"] = dropback & (d["two_high"].to_numpy() == 0)
    d["s_deep"] = thr & (d["deep"].to_numpy() == 1)
    d["s_short"] = thr & (d["short"].to_numpy() == 1)
    d["s_intermediate"] = thr & (d["intermediate"].to_numpy() == 1)
    d["s_inside_run"] = run & (d["run_inside"].to_numpy() == 1)
    d["s_outside_run"] = run & (d["run_outside"].to_numpy() == 1)
    d["s_rpo"] = scrim & (d["rpo"].to_numpy() == 1)
    d["s_shotgun"] = scrim & (d["shotgun"].to_numpy() == 1)
    d["s_under_center"] = scrim & (d["shotgun"].to_numpy() == 0)
    d["s_all"] = scrim | dropback
    return d.copy()
