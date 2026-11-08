"""Player intelligence library: universal framework (A-J) + position modules.

Each metric is a (role, population, numerator, denominator) rule evaluated per
player-game from play-by-play, participation and FTN charting, or a box/NGS field.
Rates aggregate as Σnum/Σden over the selected games. Unsupported metrics from the
requested specification are kept in the catalog with ``status='unavailable'`` and
the reason, preserving their schema for later licensed/charted integration.

Role populations
    passer    passer_id on dropbacks (incl. sacks, scrambles)
    rusher    rusher_id on designed runs (scrambles tracked separately for QBs)
    receiver  receiver_id on throws (targets)
    defender  credited defensive events (tackles, sacks, PBU, INT, FF, TFL, QB hits)
    onfield   GSIS IDs in participation offense/defense lists (2016-2025): team
              outcomes while on the field — NOT individual responsibility
    kicker / punter / returner  special-teams credited events
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd

from .. import lake
from ..config import settings
from .plays import derive

UNIVERSAL_GROUPS = {
    "A": "Identity, physical and career", "B": "Usage, snaps and workload", "C": "Production",
    "D": "Position-adjusted efficiency", "E": "Explosive plays", "F": "Negative plays and turnovers",
    "G": "Situational performance", "H": "Competition and matchup difficulty",
    "I": "Consistency and development", "J": "Impact, replacement value and uncertainty",
}
POSITIONS = ["QB", "RB", "WR", "TE", "OL", "EDGE", "DT", "LB", "CB", "S", "K", "P", "LS", "RET"]
POS_MAP = {"QB": "QB", "RB": "RB", "FB": "RB", "HB": "RB", "WR": "WR", "TE": "TE", "T": "OL", "OT": "OL", "G": "OL",
           "OG": "OL", "C": "OL", "OL": "OL", "DE": "EDGE", "OLB": "EDGE", "EDGE": "EDGE", "DT": "DT", "NT": "DT", "DL": "DT",
           "LB": "LB", "ILB": "LB", "MLB": "LB", "CB": "CB", "DB": "CB", "S": "S", "SS": "S", "FS": "S", "SAF": "S",
           "K": "K", "PK": "K", "P": "P", "LS": "LS"}


@dataclass(frozen=True)
class PM:
    key: str
    label: str
    role: str
    pop: str | None         # population indicator column within role rows (None = all role rows)
    value: str              # value column ('one' for counts)
    unit: str
    direction: int
    source: str
    group: str              # universal group A-J
    positions: tuple
    formula: str
    agg: str = "rate"       # rate (Σv/Σpop), sum (Σv), per_game (Σv/games)


def M(key, label, role, pop, value, unit, direction, source, group, positions, formula, agg="rate"):
    return PM(key, label, role, pop, value, unit, direction, source, group, tuple(positions), formula, agg)


QB, RB, WR, TE = ("QB",), ("RB",), ("WR",), ("TE",)
SKILL = ("RB", "WR", "TE")
DEF = ("EDGE", "DT", "LB", "CB", "S")
OFF_ALL = ("QB", "RB", "WR", "TE")

METRICS: list[PM] = [
    # ---------------- QB
    M("qb_dropbacks", "Dropbacks", "passer", None, "one", "count", 0, "pbp", "B", QB, "dropbacks credited to passer", "sum"),
    M("qb_epa_db", "Passing EPA per dropback", "passer", None, "epa", "EPA/db", 1, "pbp", "D", QB, "Σ EPA / dropbacks"),
    M("qb_success", "Passing success rate", "passer", None, "success", "rate", 1, "pbp", "D", QB, "dropbacks EPA>0 / dropbacks"),
    M("qb_cpoe", "CPOE", "passer", "throw", "cpoe", "pct pts", 1, "pbp", "D", QB, "mean(complete − cp)×100"),
    M("qb_comp_pct", "Completion %", "passer", "throw", "complete", "rate", 1, "pbp", "C", QB, "completions / throws"),
    M("qb_adot", "Average depth of throw", "passer", "throw", "air_yards", "yards", 0, "pbp", "B", QB, "Σ air yards / throws"),
    M("qb_air_ypa", "Air yards per attempt", "passer", "throw", "air_yards", "yards", 0, "pbp", "C", QB, "Σ air yards / throws (incl. incompletions)"),
    M("qb_time_to_throw", "Time to throw", "passer", None, "time_to_throw", "s", 0, "participation", "B", QB, "mean NGS time to throw"),
    M("qb_deep_rate", "Deep shot rate", "passer", "throw", "deep", "rate", 0, "pbp", "B", QB, "throws air>=20 / throws"),
    M("qb_behind_los", "Behind-line throw rate", "passer", "throw", "behind_los", "rate", 0, "pbp", "B", QB, "throws air<0 / throws"),
    M("qb_pressure_faced", "Pressure rate faced", "passer", None, "pressure", "rate", -1, "participation", "H", QB, "pressured dropbacks / charted dropbacks"),
    M("qb_p2s", "Pressure-to-sack rate", "passer", "s_pressure", "sack", "rate", -1, "participation", "F", QB, "sacks / pressured dropbacks"),
    M("qb_sack_rate", "Sack rate", "passer", None, "sack", "rate", -1, "pbp", "F", QB, "sacks / dropbacks"),
    M("qb_clean_epa", "Clean-pocket EPA/db", "passer", "s_clean", "epa", "EPA/db", 1, "participation", "G", QB, "EPA on unpressured dropbacks"),
    M("qb_pressure_epa", "EPA/db under pressure", "passer", "s_pressure", "epa", "EPA/db", 1, "participation", "G", QB, "EPA on pressured dropbacks"),
    M("qb_blitz_epa", "EPA/db vs blitz", "passer", "s_blitz", "epa", "EPA/db", 1, "ftn", "G", QB, "EPA when FTN blitzers > 0"),
    M("qb_noblitz_epa", "EPA/db vs no blitz", "passer", "s_no_blitz", "epa", "EPA/db", 1, "ftn", "G", QB, "EPA when no blitz"),
    M("qb_twp_rate", "Turnover-worthy play rate (FTN interception-worthy)", "passer", "throw", "int_worthy", "rate", -1, "ftn", "F", QB, "FTN interception-worthy throws / throws"),
    M("qb_int_rate", "Interception rate", "passer", "throw", "interception", "rate", -1, "pbp", "F", QB, "INT / throws"),
    M("qb_first_read", "First-read throw rate", "passer", "throw", "first_read", "rate", 0, "ftn", "B", QB, "read_thrown=1 / labeled throws"),
    M("qb_checkdown", "Checkdown rate", "passer", "throw", "checkdown", "rate", 0, "ftn", "B", QB, "read_thrown=CHK / labeled throws"),
    M("qb_short_comp", "Short completion % (0-9)", "passer", "s_short", "complete", "rate", 1, "pbp", "D", QB, "completions / throws air 0-9"),
    M("qb_inter_comp", "Intermediate completion % (10-19)", "passer", "s_intermediate", "complete", "rate", 1, "pbp", "D", QB, "completions / throws air 10-19"),
    M("qb_deep_comp", "Deep completion % (20+)", "passer", "s_deep", "complete", "rate", 1, "pbp", "D", QB, "completions / throws air>=20"),
    M("qb_deep_epa", "Deep EPA per throw", "passer", "s_deep", "epa", "EPA", 1, "pbp", "D", QB, "EPA on throws air>=20"),
    M("qb_ypa", "Yards per attempt", "passer", "throw", "pass_yards", "yards", 1, "pbp", "C", QB, "pass yards / throws"),
    M("qb_anya", "Adjusted net yards per attempt", "passer", None, "anya", "yards", 1, "pbp", "D", QB, "(yds + 20·TD − 45·INT − sack yds)/(att + sacks)"),
    M("qb_pa_epa", "Play-action EPA/db", "passer", "s_play_action", "epa", "EPA/db", 1, "ftn", "G", QB, "EPA on FTN play-action dropbacks"),
    M("qb_nopa_epa", "Non-play-action EPA/db", "passer", "s_no_play_action", "epa", "EPA/db", 1, "ftn", "G", QB, "EPA without play action"),
    M("qb_rpo_epa", "RPO EPA/play", "passer", "s_rpo", "epa", "EPA", 1, "ftn", "G", QB, "EPA on FTN RPO dropbacks"),
    M("qb_man_epa", "EPA/db vs man", "passer", "s_man", "epa", "EPA/db", 1, "participation", "H", QB, "EPA vs man coverage"),
    M("qb_zone_epa", "EPA/db vs zone", "passer", "s_zone", "epa", "EPA/db", 1, "participation", "H", QB, "EPA vs zone coverage"),
    M("qb_scramble_rate", "Scramble rate", "passer", None, "scramble", "rate", 0, "pbp", "B", QB, "scrambles / dropbacks"),
    M("qb_rz_epa", "Red-zone EPA/db", "passer", "s_red_zone", "epa", "EPA/db", 1, "pbp", "G", QB, "EPA on red-zone dropbacks"),
    M("qb_third_success", "3rd-down success", "passer", "s_third_down", "first_down", "rate", 1, "pbp", "G", QB, "3rd-down dropbacks gaining 1st / 3rd-down dropbacks"),
    M("qb_explosive", "Explosive pass rate", "passer", None, "explosive_pass", "rate", 1, "pbp", "E", QB, "dropbacks >=20 yds / dropbacks"),
    M("qb_td_rate", "Passing TD rate", "passer", "throw", "pass_td", "rate", 1, "pbp", "C", QB, "pass TD / throws"),
    M("qb_out_of_pocket", "Out-of-pocket rate", "passer", None, "out_of_pocket", "rate", 0, "ftn", "B", QB, "FTN out-of-pocket / dropbacks"),
    M("qb_throwaway", "Throwaway rate", "passer", "throw", "throwaway", "rate", 0, "ftn", "F", QB, "FTN throwaways / throws"),
    # ---------------- rushing (RB + QB designed)
    M("ru_carries", "Designed carries", "rusher", None, "one", "count", 0, "pbp", "B", ("RB", "QB", "WR"), "designed runs", "sum"),
    M("ru_epa", "Rush EPA per carry", "rusher", None, "epa", "EPA/carry", 1, "pbp", "D", ("RB", "QB", "WR"), "Σ EPA / designed carries"),
    M("ru_success", "Rush success rate", "rusher", None, "success", "rate", 1, "pbp", "D", ("RB", "QB", "WR"), "carries EPA>0 / carries"),
    M("ru_ypc", "Yards per carry", "rusher", None, "yards", "yards", 1, "pbp", "C", ("RB", "QB", "WR"), "rush yards / carries"),
    M("ru_explosive", "Explosive run rate (10+)", "rusher", None, "explosive_run", "rate", 1, "pbp", "E", ("RB", "QB", "WR"), "runs >=10 / carries"),
    M("ru_stuff", "Stuff rate", "rusher", None, "stuff", "rate", -1, "pbp", "F", ("RB", "QB"), "runs <=0 yds / carries"),
    M("ru_inside_epa", "Inside-run EPA", "rusher", "s_inside_run", "epa", "EPA", 1, "pbp", "G", RB, "EPA on middle/guard runs (direction, not concept)"),
    M("ru_outside_epa", "Outside-run EPA", "rusher", "s_outside_run", "epa", "EPA", 1, "pbp", "G", RB, "EPA on end-gap runs"),
    M("ru_light_epa", "Light-box EPA", "rusher", "s_light_box", "epa", "EPA", 1, "participation", "H", RB, "EPA vs <=6 in box"),
    M("ru_loaded_epa", "Loaded-box EPA", "rusher", "s_loaded_box", "epa", "EPA", 1, "participation", "H", RB, "EPA vs >=8 in box"),
    M("ru_loaded_rate", "Loaded-box carry rate", "rusher", None, "loaded_box", "rate", 0, "participation", "H", RB, "carries vs >=8 / charted carries"),
    M("ru_gl_carries", "Goal-line carries", "rusher", "s_goal_line", "one", "count", 0, "pbp", "G", ("RB", "QB"), "carries inside the 5", "sum"),
    M("ru_short_yard", "Short-yardage conversion", "rusher", "short_yardage", "first_down", "rate", 1, "pbp", "G", ("RB", "QB"), "3rd/4th & <=2 carries converted"),
    M("ru_td_rate", "Rush TD rate", "rusher", None, "rush_td", "rate", 1, "pbp", "C", ("RB", "QB"), "rush TD / carries"),
    M("ru_fumble_rate", "Fumbles lost per carry", "rusher", None, "fumble_lost", "rate", -1, "pbp", "F", ("RB", "QB"), "fumbles lost / carries"),
    M("ru_left_rate", "Run-left rate", "rusher", None, "run_left", "rate", 0, "pbp", "B", RB, "left runs / runs with direction"),
    M("ru_middle_rate", "Run-middle rate", "rusher", None, "run_middle", "rate", 0, "pbp", "B", RB, "middle runs / runs with direction"),
    M("ru_right_rate", "Run-right rate", "rusher", None, "run_right", "rate", 0, "pbp", "B", RB, "right runs / runs with direction"),
    M("qb_scramble_yards", "Scramble yards", "scrambler", None, "yards", "yards", 1, "pbp", "C", QB, "Σ yards on scrambles", "sum"),
    # ---------------- receiving
    M("re_targets", "Targets", "receiver", None, "one", "count", 0, "pbp", "B", SKILL, "throws to player", "sum"),
    M("re_receptions", "Receptions", "receiver", None, "complete", "count", 0, "pbp", "C", SKILL, "completions to player", "sum"),
    M("re_yards", "Receiving yards", "receiver", None, "pass_yards", "yards", 1, "pbp", "C", SKILL, "receiving yards", "sum"),
    M("re_catch", "Catch rate", "receiver", None, "complete", "rate", 1, "pbp", "D", SKILL, "receptions / targets"),
    M("re_ypt", "Yards per target", "receiver", None, "pass_yards", "yards", 1, "pbp", "D", SKILL, "yards / targets"),
    M("re_epa", "Receiving EPA per target", "receiver", None, "epa", "EPA", 1, "pbp", "D", SKILL, "Σ EPA / targets"),
    M("re_success", "Target success rate", "receiver", None, "success", "rate", 1, "pbp", "D", SKILL, "targets EPA>0 / targets"),
    M("re_adot", "Average depth of target", "receiver", None, "air_yards", "yards", 0, "pbp", "B", SKILL, "Σ air yards / targets"),
    M("re_yac", "YAC per reception", "receiver", None, "yac", "yards", 1, "pbp", "D", SKILL, "Σ YAC / receptions"),
    M("re_yac_oe", "YAC above expected", "receiver", None, "yac_oe", "yards", 1, "pbp", "D", SKILL, "mean(YAC − xYAC) on receptions"),
    M("re_explosive", "Explosive reception rate (20+)", "receiver", None, "explosive_pass", "rate", 1, "pbp", "E", SKILL, "receptions >=20 / targets"),
    M("re_deep_rate", "Deep target rate", "receiver", None, "deep", "rate", 0, "pbp", "B", SKILL, "targets air>=20 / targets"),
    M("re_drop", "Drop rate", "receiver", None, "drop", "rate", -1, "ftn", "F", SKILL, "FTN drops / targets"),
    M("re_contested", "Contested target rate", "receiver", None, "contested", "rate", 0, "ftn", "H", SKILL, "FTN contested / targets"),
    M("re_contested_catch", "Contested catch rate", "receiver", "contested_pop", "complete", "rate", 1, "ftn", "D", SKILL, "receptions on contested targets / contested targets"),
    M("re_catchable", "Catchable target rate", "receiver", None, "catchable", "rate", 0, "ftn", "H", SKILL, "FTN catchable / targets"),
    M("re_created", "Created reception rate", "receiver", None, "created_rec", "rate", 1, "ftn", "D", SKILL, "FTN created receptions / targets"),
    M("re_first_read", "First-read target share", "receiver", None, "first_read", "rate", 0, "ftn", "B", SKILL, "targets as first read / labeled targets"),
    M("re_man_ypt", "Yards per target vs man", "receiver", "s_man", "pass_yards", "yards", 1, "participation", "H", SKILL, "yards / targets vs man"),
    M("re_zone_ypt", "Yards per target vs zone", "receiver", "s_zone", "pass_yards", "yards", 1, "participation", "H", SKILL, "yards / targets vs zone"),
    M("re_man_epa", "EPA per target vs man", "receiver", "s_man", "epa", "EPA", 1, "participation", "H", SKILL, "EPA / targets vs man"),
    M("re_zone_epa", "EPA per target vs zone", "receiver", "s_zone", "epa", "EPA", 1, "participation", "H", SKILL, "EPA / targets vs zone"),
    M("re_rz_targets", "Red-zone targets", "receiver", "s_red_zone", "one", "count", 0, "pbp", "G", SKILL, "targets inside the 20", "sum"),
    M("re_td_rate", "Receiving TD rate", "receiver", None, "pass_td", "rate", 1, "pbp", "C", SKILL, "receiving TD / targets"),
    M("re_third_success", "3rd-down target conversion", "receiver", "s_third_down", "first_down", "rate", 1, "pbp", "G", SKILL, "3rd-down targets gaining 1st / 3rd-down targets"),
    M("re_screen_rate", "Screen target rate", "receiver", None, "screen", "rate", 0, "ftn", "B", SKILL, "FTN screens / targets"),
    # ---------------- on-field offense (route proxy, OL)
    M("of_dropbacks", "Dropbacks on field (route proxy)", "onfield_off", "dropback", "one", "count", 0, "participation", "B", SKILL + ("OL",), "team dropbacks with player on field", "sum"),
    M("of_snaps", "Charted offensive snaps", "onfield_off", None, "one", "count", 0, "participation", "B", OFF_ALL + ("OL",), "plays with player on field", "sum"),
    M("ol_pressure_onfield", "Team pressure rate allowed (on field)", "onfield_off", "dropback", "pressure", "rate", -1, "participation", "D", ("OL", "TE", "RB"), "pressured dropbacks / dropbacks while on field (team outcome)"),
    M("ol_sack_onfield", "Team sack rate allowed (on field)", "onfield_off", "dropback", "sack", "rate", -1, "participation", "D", ("OL", "TE"), "sacks / dropbacks while on field"),
    M("ol_hit_onfield", "Team QB-hit rate allowed (on field)", "onfield_off", "dropback", "qb_hit", "rate", -1, "participation", "D", ("OL", "TE"), "QB hits incl. sacks / dropbacks while on field"),
    M("ol_rush_epa_onfield", "Team rush EPA (on field)", "onfield_off", "designed_run", "epa", "EPA", 1, "participation", "D", ("OL", "TE"), "EPA on designed runs while on field"),
    M("ol_stuff_onfield", "Team stuff rate (on field)", "onfield_off", "designed_run", "stuff", "rate", -1, "participation", "D", ("OL", "TE"), "runs <=0 / runs while on field"),
    M("ol_aly_onfield", "Team adjusted line yards (on field)", "onfield_off", "designed_run", "aly", "yards", 1, "participation", "D", ("OL",), "FO ALY on runs while on field"),
    M("ol_inside_onfield", "Team inside-run EPA (on field)", "onfield_off", "s_inside_run", "epa", "EPA", 1, "participation", "G", ("OL",), "EPA on inside runs while on field"),
    M("ol_outside_onfield", "Team outside-run EPA (on field)", "onfield_off", "s_outside_run", "epa", "EPA", 1, "participation", "G", ("OL",), "EPA on outside runs while on field"),
    M("ol_blitz_pressure", "Team pressure allowed vs blitz (on field)", "onfield_off", "s_blitz", "pressure", "rate", -1, "participation+ftn", "G", ("OL", "RB"), "pressure rate vs blitz while on field"),
    M("pen_off", "Accepted penalties", "penalized", None, "one", "count", -1, "pbp", "F", OFF_ALL + ("OL",) + DEF, "penalties assessed to player", "sum"),
    # ---------------- defenders credited
    M("df_tackles", "Tackles (solo + assisted)", "tackler", None, "one", "count", 0, "pbp", "C", DEF, "tackle credits", "sum"),
    M("df_run_stops", "Run stops", "tackler", "run_stop", "one", "count", 1, "pbp", "D", DEF, "tackle credits on unsuccessful designed runs (EPA<=0)", "sum"),
    M("df_sacks", "Sacks", "sacker", None, "sack_credit", "count", 1, "pbp", "C", DEF, "sacks (half sacks 0.5)", "sum"),
    M("df_qb_hits", "QB hits", "hitter", None, "one", "count", 1, "pbp", "C", DEF, "QB hit credits", "sum"),
    M("df_tfl", "Tackles for loss", "tfl", None, "one", "count", 1, "pbp", "C", DEF, "TFL credits", "sum"),
    M("df_pbu", "Passes defensed", "pbu", None, "one", "count", 1, "pbp", "C", ("LB", "CB", "S", "EDGE", "DT"), "pass-defense credits", "sum"),
    M("df_int", "Interceptions", "intercepter", None, "one", "count", 1, "pbp", "C", ("LB", "CB", "S"), "interceptions", "sum"),
    M("df_ff", "Forced fumbles", "forcer", None, "one", "count", 1, "pbp", "C", DEF, "forced fumbles", "sum"),
    # ---------------- on-field defense
    M("dn_snaps", "Charted defensive snaps", "onfield_def", None, "one", "count", 0, "participation", "B", DEF, "plays with player on field", "sum"),
    M("dn_cov_snaps", "Dropbacks faced on field", "onfield_def", "dropback", "one", "count", 0, "participation", "B", DEF, "opponent dropbacks while on field", "sum"),
    M("dn_pass_epa", "Team EPA/db allowed (on field)", "onfield_def", "dropback", "epa", "EPA/db", -1, "participation", "D", DEF, "EPA allowed on dropbacks while on field"),
    M("dn_pressure", "Team pressure rate (on field)", "onfield_def", "dropback", "pressure", "rate", 1, "participation", "D", ("EDGE", "DT", "LB"), "pressure rate while on field"),
    M("dn_explosive_allowed", "Team explosive pass rate allowed (on field)", "onfield_def", "dropback", "explosive_pass", "rate", -1, "participation", "E", ("CB", "S", "LB"), "explosive passes allowed while on field"),
    M("dn_rush_epa", "Team rush EPA allowed (on field)", "onfield_def", "designed_run", "epa", "EPA", -1, "participation", "D", DEF, "rush EPA allowed while on field"),
    M("dn_stuff", "Team stuff rate (on field)", "onfield_def", "designed_run", "stuff", "rate", 1, "participation", "D", ("EDGE", "DT", "LB", "S"), "stuffs while on field"),
    M("dn_man_epa", "Team EPA allowed in man (on field)", "onfield_def", "s_man", "epa", "EPA", -1, "participation", "H", ("CB", "S", "LB"), "EPA allowed in man while on field"),
    M("dn_zone_epa", "Team EPA allowed in zone (on field)", "onfield_def", "s_zone", "epa", "EPA", -1, "participation", "H", ("CB", "S", "LB"), "EPA allowed in zone while on field"),
    M("dn_two_high_snaps", "Two-high snaps (on field)", "onfield_def", "s_two_high", "one", "count", 0, "participation", "B", ("S", "CB"), "dropbacks vs two-high shells while on field", "sum"),
    M("dn_single_high_snaps", "Single-high snaps (on field)", "onfield_def", "s_single_high", "one", "count", 0, "participation", "B", ("S", "CB"), "dropbacks vs single-high while on field", "sum"),
    M("dn_blitz_pressure", "Team pressure when blitzing (on field)", "onfield_def", "s_blitz", "pressure", "rate", 1, "participation+ftn", "G", ("LB", "S", "CB"), "pressure rate on blitzes while on field"),
    # ---------------- specialists
    M("k_fg_att", "FG attempts", "kicker_fg", None, "one", "count", 0, "pbp", "B", ("K",), "FG attempts", "sum"),
    M("k_fg_pct", "FG %", "kicker_fg", None, "fg_made", "rate", 1, "pbp", "D", ("K",), "made / attempts"),
    M("k_fg_oe", "FG made over expected per attempt", "kicker_fg", None, "fg_oe", "makes", 1, "pbp", "D", ("K",), "mean(made − league distance-logistic p)"),
    M("k_fg_long_pct", "FG % 50+", "kicker_fg", "fg50", "fg_made", "rate", 1, "pbp", "G", ("K",), "made / attempts >= 50 yds"),
    M("k_xp_pct", "XP %", "kicker_xp", None, "xp_good", "rate", 1, "pbp", "D", ("K",), "good XP / XP attempts"),
    M("k_tb_rate", "Kickoff touchback rate", "kicker_ko", None, "touchback", "rate", 0, "pbp", "B", ("K",), "touchbacks / kickoffs"),
    M("p_punts", "Punts", "punter", None, "one", "count", 0, "pbp", "B", ("P",), "punts", "sum"),
    M("p_gross", "Gross punt average", "punter", None, "kick_distance", "yards", 1, "pbp", "C", ("P",), "Σ kick distance / punts"),
    M("p_net", "Net punt average", "punter", None, "punt_net", "yards", 1, "pbp", "D", ("P",), "Σ(distance − return, TB −20) / punts"),
    M("p_in20", "Inside-20 rate", "punter", None, "in20", "rate", 1, "pbp", "D", ("P",), "punts inside 20 / punts"),
    M("p_epa", "EPA per punt (punting team)", "punter", None, "st_epa", "EPA", 1, "pbp", "J", ("P",), "Σ EPA / punts"),
    M("r_kr", "Kick returns", "kick_returner", None, "one", "count", 0, "pbp", "B", ("RET",), "kickoff returns", "sum"),
    M("r_kr_avg", "Kick return average", "kick_returner", None, "return_yards", "yards", 1, "pbp", "C", ("RET",), "Σ return yards / returns"),
    M("r_pr", "Punt returns", "punt_returner", None, "one", "count", 0, "pbp", "B", ("RET",), "punt returns", "sum"),
    M("r_pr_avg", "Punt return average", "punt_returner", None, "return_yards", "yards", 1, "pbp", "C", ("RET",), "Σ return yards / returns"),
    M("r_epa", "Return EPA per return", "kick_returner", None, "ret_epa", "EPA", 1, "pbp", "J", ("RET",), "Σ receiving-team EPA / kick returns"),
]

UNAVAILABLE = [
    ("Big-time throw rate", "QB", "PFF proprietary grade; no public play-level definition/data"),
    ("Expected interception rate", "QB", "no public xINT model; FTN interception-worthy rate is reported separately, not substituted"),
    ("Passer rating by depth", "QB", "computable from public data — scheduled; not yet materialized"),
    ("RYOE per attempt", "RB", "NGS weekly aggregate only (qualified rushers); shown from NGS table when present"),
    ("Yards after contact / missed tackles forced / broken tackle rate", "RB", "requires licensed charting (PFF/SIS)"),
    ("Zone vs gap/power concept efficiency", "RB", "run concept not in public data; run direction is reported instead and labeled as such"),
    ("Pass-blocking ability (RB)", "RB", "individual pass-block assignments not public"),
    ("Man/zone/press win rate", "WR", "route-level win rates need tracking/charting; not public"),
    ("Separation above expected / catch probability above expected", "WR", "NGS weekly avg separation shown where published; expected models not public"),
    ("Slot vs outside alignment", "WR", "alignment not in public participation feed"),
    ("Pass-/run-block win rate, pressures/sacks allowed (individual), assignment errors", "OL", "individual blocking attribution not public; on-field team outcomes are reported instead"),
    ("Speed/power rush defense, stunt/blitz pickup (individual)", "OL", "requires charting of rusher-blocker pairs"),
    ("Pass-rush win rate, time to pressure, 1-on-1 win rate, double/chip rate", "EDGE", "requires tracking/charting; pressure credited to individual not public"),
    ("Gap penetration, double-team resistance", "DT", "requires tracking/charting"),
    ("Coverage EPA / targets / catch rate / separation allowed (individual)", "CB", "defender-in-coverage attribution not public; on-field team outcomes reported instead"),
    ("Press coverage efficiency", "CB", "press alignment not public"),
    ("Gap-fill accuracy, assignment discipline, play-action recognition", "LB", "requires charting"),
    ("Coverage busts", "S", "requires charting"),
    ("Long-snapper snap accuracy", "LS", "no public data; only special-teams snap counts"),
]


def _role_frames(p: pd.DataFrame, d: pd.DataFrame, fg_model) -> dict[str, pd.DataFrame]:
    base = pd.concat([p[["game_id", "season", "week", "posteam", "defteam", "play_id"]], d], axis=1)
    pt = p["play_type"].astype("string")
    roles = {}
    # passer
    db = base[(d["dropback"] == 1)].copy()
    db["player_id"] = p.loc[db.index, "passer_id"]
    db["team"] = db["posteam"]
    py = np.nan_to_num(db["pass_yards"].to_numpy())
    db["anya"] = np.where(db["throw"] == 1, py + 20 * np.nan_to_num(db["pass_td"]) - 45 * np.nan_to_num(db["interception"]),
                          np.where(db["sack"] == 1, -db["sack_yards"], np.nan))
    roles["passer"] = db
    ru = base[d["designed_run"] == 1].copy()
    ru["player_id"] = p.loc[ru.index, "rusher_id"]
    ru["team"] = ru["posteam"]
    ru["rush_td"] = (p.loc[ru.index, "rush_touchdown"] == 1).astype(float)
    ru["short_yardage"] = d.loc[ru.index, "short_yardage"]
    roles["rusher"] = ru
    sc = base[d["scramble"] == 1].copy()
    sc["player_id"] = p.loc[sc.index, "passer_id"]
    sc["team"] = sc["posteam"]
    sc["yards"] = pd.to_numeric(p.loc[sc.index, "yards_gained"], errors="coerce")
    roles["scrambler"] = sc
    re = base[(d["throw"] == 1) & p["receiver_id"].notna()].copy()
    re["player_id"] = p.loc[re.index, "receiver_id"]
    re["team"] = re["posteam"]
    re["explosive_pass"] = ((re["complete"] == 1) & (p.loc[re.index, "yards_gained"] >= 20)).astype(float)
    re["contested_pop"] = re["contested"]
    roles["receiver"] = re
    # credited defenders
    def credit(cols, name, extra=None):
        frames = []
        for c in cols:
            m = p[c].notna()
            f = base[m].copy()
            f["player_id"] = p.loc[m, c]
            f["team"] = f["defteam"]
            if extra is not None:
                for k, v in extra(c, m).items():
                    f[k] = v
            frames.append(f)
        roles[name] = pd.concat(frames, ignore_index=True) if frames else base.iloc[:0]
    run_stop = (d["designed_run"] == 1) & (pd.to_numeric(p["epa"], errors="coerce") <= 0)
    credit(["solo_tackle_1_player_id", "solo_tackle_2_player_id", "assist_tackle_1_player_id", "assist_tackle_2_player_id",
            "tackle_with_assist_1_player_id", "tackle_with_assist_2_player_id"], "tackler",
           lambda c, m: {"run_stop": run_stop[m].astype(float).to_numpy()})
    credit(["sack_player_id", "half_sack_1_player_id", "half_sack_2_player_id"], "sacker",
           lambda c, m: {"sack_credit": np.full(m.sum(), 1.0 if c == "sack_player_id" else 0.5)})
    credit(["qb_hit_1_player_id", "qb_hit_2_player_id"], "hitter")
    credit(["tackle_for_loss_1_player_id", "tackle_for_loss_2_player_id"], "tfl")
    credit(["pass_defense_1_player_id", "pass_defense_2_player_id"], "pbu")
    credit(["interception_player_id"], "intercepter")
    credit(["forced_fumble_player_1_player_id"], "forcer")
    pen = base[(p["penalty"] == 1) & p.get("penalty_player_id", pd.Series(np.nan, index=p.index)).notna()].copy() if "penalty_player_id" in p else base.iloc[:0]
    if len(pen):
        pen["player_id"] = p.loc[pen.index, "penalty_player_id"]
        pen["team"] = p.loc[pen.index, "penalty_team"]
    roles["penalized"] = pen
    # specialists
    fg = base[(pt == "field_goal").fillna(False)].copy()
    fg["player_id"] = p.loc[fg.index, "kicker_player_id"]
    fg["team"] = fg["posteam"]
    dist = pd.to_numeric(p.loc[fg.index, "kick_distance"], errors="coerce").fillna(p.loc[fg.index, "yardline_100"] + 17)
    fg["fg_made"] = (p.loc[fg.index, "field_goal_result"] == "made").astype(float)
    a, b, c = fg_model
    fg["fg_oe"] = fg["fg_made"] - 1 / (1 + np.exp(-(a + b * dist + c * dist ** 2 / 100)))
    fg["fg50"] = (dist >= 50).astype(float)
    roles["kicker_fg"] = fg
    xp = base[(pt == "extra_point").fillna(False)].copy()
    xp["player_id"] = p.loc[xp.index, "kicker_player_id"]
    xp["team"] = xp["posteam"]
    xp["xp_good"] = (p.loc[xp.index, "extra_point_result"] == "good").astype(float)
    roles["kicker_xp"] = xp
    ko = base[(pt == "kickoff").fillna(False)].copy()
    ko["player_id"] = p.loc[ko.index, "kicker_player_id"]
    ko["team"] = ko["defteam"]
    ko["touchback"] = pd.to_numeric(p.loc[ko.index, "touchback"], errors="coerce")
    roles["kicker_ko"] = ko
    pu = base[(pt == "punt").fillna(False)].copy()
    pu["player_id"] = p.loc[pu.index, "punter_player_id"]
    pu["team"] = pu["posteam"]
    kd = pd.to_numeric(p.loc[pu.index, "kick_distance"], errors="coerce")
    rt = pd.to_numeric(p.loc[pu.index, "return_yards"], errors="coerce").fillna(0)
    tb = pd.to_numeric(p.loc[pu.index, "touchback"], errors="coerce").fillna(0)
    pu["kick_distance"] = kd
    pu["punt_net"] = np.where(tb == 1, kd - 20, kd - rt)
    pu["in20"] = pd.to_numeric(p.loc[pu.index, "punt_inside_twenty"], errors="coerce")
    pu["st_epa"] = pd.to_numeric(p.loc[pu.index, "epa"], errors="coerce")
    roles["punter"] = pu
    kr = base[(pt == "kickoff").fillna(False) & p["kickoff_returner_player_id"].notna()].copy()
    kr["player_id"] = p.loc[kr.index, "kickoff_returner_player_id"]
    kr["team"] = kr["posteam"]
    kr["return_yards"] = pd.to_numeric(p.loc[kr.index, "return_yards"], errors="coerce")
    kr["ret_epa"] = pd.to_numeric(p.loc[kr.index, "epa"], errors="coerce")
    roles["kick_returner"] = kr
    pr = base[(pt == "punt").fillna(False) & p["punt_returner_player_id"].notna()].copy()
    pr["player_id"] = p.loc[pr.index, "punt_returner_player_id"]
    pr["team"] = pr["defteam"]
    pr["return_yards"] = pd.to_numeric(p.loc[pr.index, "return_yards"], errors="coerce")
    roles["punt_returner"] = pr
    # on-field (participation)
    for name, col, tcol in (("onfield_off", "offense_players", "posteam"), ("onfield_def", "defense_players", "defteam")):
        has = p[col].notna() & (p[col].astype("string").str.len() > 0).fillna(False)
        if not has.any():
            roles[name] = base.iloc[:0].assign(player_id=pd.Series(dtype=object), team=pd.Series(dtype=object))
            continue
        ids = p.loc[has, col].astype(str).str.split(";")
        lens = ids.str.len().to_numpy()
        idx = np.repeat(ids.index.to_numpy(), lens)
        f = base.loc[idx].copy()
        f["player_id"] = np.concatenate(ids.to_numpy())
        f["team"] = f[tcol]
        roles[name] = f
    return roles


def _agg_metrics(roles: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for m in METRICS:
        f = roles.get(m.role)
        if f is None or f.empty or "player_id" not in f:
            continue
        f = f[f["player_id"].notna()]
        pop = np.ones(len(f), bool) if m.pop is None else (pd.to_numeric(f[m.pop], errors="coerce").to_numpy() == 1) if m.pop in f else np.zeros(len(f), bool)
        val = np.ones(len(f)) if m.value == "one" else pd.to_numeric(f[m.value], errors="coerce").to_numpy(float) if m.value in f else np.full(len(f), np.nan)
        ok = pop & ~np.isnan(val)
        g = pd.DataFrame({"player_id": f["player_id"].to_numpy(), "game_id": f["game_id"].to_numpy(),
                          "team": f["team"].to_numpy(), "n": np.where(ok, val, 0.0), "d": ok.astype(float)})
        g = g.groupby(["player_id", "game_id", "team"], as_index=False)[["n", "d"]].sum()
        g = g[g["d"] > 0]
        g["metric"] = m.key
        rows.append(g)
    return pd.concat(rows, ignore_index=True)


def build(first: int = 2006, last: int | None = None, verbose: bool = True) -> pd.DataFrame:
    from ..sim.league import league_for
    last = last or settings().last_season
    out = []
    for s in range(first, last + 1):
        p = lake.load_plays([s])
        if p.empty:
            continue
        p = p[p["posteam"].notna()].reset_index(drop=True)
        d = derive(p)
        try:
            fg_model = league_for(max(s, 2009)).fg_coef
        except Exception:
            fg_model = (5.5, -0.09, 0.0)
        g = _agg_metrics(_role_frames(p, d, fg_model))
        g["season"] = s
        out.append(g)
        if verbose:
            print("player metrics", s, len(g), flush=True)
    pm = pd.concat(out, ignore_index=True)
    games = lake.load("games")[["game_id", "week", "kickoff", "home_team", "away_team"]]
    pm = pm.merge(games, on="game_id", how="left")
    pm["opponent"] = np.where(pm["team"] == pm["home_team"], pm["away_team"], pm["home_team"])
    pm = pm.drop(columns=["home_team", "away_team"])
    pm.to_parquet(settings().lake_dir / "player_metric_games.parquet", index=False)
    return pm


@lru_cache(maxsize=1)
def load_metric_games() -> pd.DataFrame:
    return pd.read_parquet(settings().lake_dir / "player_metric_games.parquet")


METRIC_INDEX = {m.key: m for m in METRICS}
COVERAGE = {"pbp": "2006-present", "participation": "2016-2025", "ftn": "2022-present", "participation+ftn": "2022-2025"}


def catalog() -> list[dict]:
    cat = [{"id": m.key, "label": m.label, "role": m.role, "unit": m.unit, "direction": m.direction, "source": m.source,
            "coverage": COVERAGE[m.source], "group": m.group, "group_label": UNIVERSAL_GROUPS[m.group],
            "positions": list(m.positions), "formula": m.formula, "aggregation": m.agg, "status": "implemented"} for m in METRICS]
    cat += [{"id": f"unavailable.{i}", "label": l, "positions": [pos], "status": "unavailable", "reason": r,
             "group": None, "source": None} for i, (l, pos, r) in enumerate(UNAVAILABLE)]
    return cat


def catalog_rows():
    return [(f"player.{m.key}", "player", m.label, UNIVERSAL_GROUPS[m.group], m.unit, m.direction, m.formula,
             m.agg, m.source, COVERAGE[m.source], "implemented") for m in METRICS] + \
           [(f"player.unavailable.{i}", "player", l, pos, None, 0, None, None, None, None, "unavailable: " + r)
            for i, (l, pos, r) in enumerate(UNAVAILABLE)]


if __name__ == "__main__":
    build()
