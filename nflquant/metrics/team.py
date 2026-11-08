"""Team intelligence library.

Every field = (side, base metric, situational split). A field is aggregated as
Σnumerator / Σdenominator over the selected team-games, so multi-game windows are
exposure-weighted rather than averages of per-game rates. Sample size (the
denominator) accompanies every value.

Sources:  pbp = nflfastR play-by-play (2006+)
          participation = NGS participation via nflverse (2016-2025)
          ftn = FTN charting via nflverse (2022+)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd

from .. import lake
from ..config import settings
from .plays import derive

COVERAGE = {"pbp": (2006, None), "participation": (2016, 2025), "ftn": (2022, None), "participation+ftn": (2022, 2025)}

CATEGORIES = [
    "Overall offensive efficiency", "Team passing offense", "Team rushing offense", "Offensive line performance",
    "Receiving corps efficiency", "Offensive formations and personnel", "Overall defensive efficiency",
    "Passing defense", "Run defense", "Team pass rush", "Defensive coverage schemes", "Down-and-distance performance",
    "Red-zone and goal-line performance", "Special teams", "Coaching, play calling and decision-making",
    "Roster strength, health and continuity", "Venue, weather, rest and schedule", "Performance trends and regression",
    "Opponent strength and matchups", "Personnel-specific and lineup interaction analysis",
]

SPLITS = {
    "all": ("All plays", None),
    "early_down": ("1st & 2nd down", "Down-and-distance performance"),
    "third_down": ("3rd down", "Down-and-distance performance"),
    "red_zone": ("Red zone (inside 20)", "Red-zone and goal-line performance"),
    "goal_line": ("Goal line (inside 5)", "Red-zone and goal-line performance"),
    "first_half": ("First half", None),
    "second_half": ("Second half (Q3-Q4)", None),
    "q1": ("1st quarter", None),
    "q4": ("4th quarter", None),
    "neutral": ("Neutral script (|score diff| <= 7, Q1-Q3)", "Coaching, play calling and decision-making"),
    "leading": ("Leading", None),
    "trailing": ("Trailing", None),
    "two_minute": ("Final two minutes of half", "Coaching, play calling and decision-making"),
    "pressure": ("Under pressure", "Team pass rush"),
    "clean": ("Clean pocket", "Offensive line performance"),
    "blitz": ("Vs blitz / when blitzing", "Team pass rush"),
    "no_blitz": ("Vs no blitz / no blitz", "Team pass rush"),
    "man": ("Vs man coverage", "Defensive coverage schemes"),
    "zone": ("Vs zone coverage", "Defensive coverage schemes"),
    "two_high": ("Vs two-high shells", "Defensive coverage schemes"),
    "single_high": ("Vs single-high shells", "Defensive coverage schemes"),
    "play_action": ("Play action", "Team passing offense"),
    "no_play_action": ("No play action", "Team passing offense"),
    "light_box": ("Light box (<=6)", "Team rushing offense"),
    "loaded_box": ("Loaded box (>=8)", "Team rushing offense"),
    "inside_run": ("Inside runs (middle/guard)", "Team rushing offense"),
    "outside_run": ("Outside runs (end)", "Team rushing offense"),
    "pers_11": ("11 personnel", "Personnel-specific and lineup interaction analysis"),
    "pers_12": ("12 personnel", "Personnel-specific and lineup interaction analysis"),
    "pers_21": ("21 personnel", "Personnel-specific and lineup interaction analysis"),
    "vs_base": ("Vs base defense", "Personnel-specific and lineup interaction analysis"),
    "vs_nickel": ("Vs nickel", "Personnel-specific and lineup interaction analysis"),
    "vs_dime": ("Vs dime", "Personnel-specific and lineup interaction analysis"),
    "shotgun": ("Shotgun", "Offensive formations and personnel"),
    "under_center": ("Under center", "Offensive formations and personnel"),
    "deep": ("Deep throws (air >= 20)", "Team passing offense"),
    "intermediate": ("Intermediate throws (10-19)", "Team passing offense"),
    "short": ("Short throws (0-9)", "Team passing offense"),
}
SPLIT_SOURCE = {"pressure": "participation", "clean": "participation", "man": "participation", "zone": "participation",
                "two_high": "participation", "single_high": "participation", "light_box": "participation",
                "loaded_box": "participation", "pers_11": "participation", "pers_12": "participation",
                "pers_21": "participation", "vs_base": "participation", "vs_nickel": "participation",
                "vs_dime": "participation", "blitz": "ftn", "no_blitz": "ftn", "play_action": "ftn",
                "no_play_action": "ftn"}

CORE_SPLITS = ["all", "early_down", "third_down", "red_zone", "first_half", "second_half", "neutral", "leading",
               "trailing", "q4", "two_minute"]
PASS_SPLITS = ["all", "early_down", "third_down", "red_zone", "neutral", "pressure", "clean", "blitz", "no_blitz",
               "man", "zone", "two_high", "single_high", "play_action", "no_play_action", "shotgun", "under_center",
               "pers_11", "pers_12", "vs_nickel", "vs_dime", "trailing", "q4", "two_minute"]
RUN_SPLITS = ["all", "early_down", "third_down", "red_zone", "goal_line", "light_box", "loaded_box", "inside_run",
              "outside_run", "pers_11", "pers_12", "pers_21", "vs_base", "vs_nickel", "shotgun", "under_center",
              "leading", "neutral"]
DEPTH_SPLITS = ["all", "deep", "intermediate", "short", "pressure", "clean", "man", "zone", "play_action"]


@dataclass(frozen=True)
class Base:
    key: str
    label: str
    category: str
    def_category: str
    pop: str           # population column (indicator == 1)
    value: str         # value column (mean of value over population, NaN excluded)
    unit: str
    direction: int     # +1 higher is better for the offense
    binary: bool
    source: str = "pbp"
    splits: tuple = ("all",)
    formula: str = ""


def B(key, label, cat, dcat, pop, value, unit, direction, binary, source="pbp", splits=("all",), formula=""):
    return Base(key, label, cat, dcat, pop, value, unit, direction, binary, source, tuple(splits), formula)


O, P, R, OL, RC, F = CATEGORIES[0], CATEGORIES[1], CATEGORIES[2], CATEGORIES[3], CATEGORIES[4], CATEGORIES[5]
DO, DP, DR, PR, CV, DD = CATEGORIES[6], CATEGORIES[7], CATEGORIES[8], CATEGORIES[9], CATEGORIES[10], CATEGORIES[11]
RZ, ST, CO = CATEGORIES[12], CATEGORIES[13], CATEGORIES[14]

BASES = [
    B("epa_play", "EPA per play", O, DO, "scrim", "epa", "EPA/play", 1, False, splits=CORE_SPLITS + ["pers_11", "pers_12", "vs_base", "vs_nickel", "vs_dime", "shotgun", "under_center"], formula="Σ EPA / scrimmage plays (excl. kneels, spikes, 2-pt)"),
    B("success", "Success rate", O, DO, "scrim", "success", "rate", 1, True, splits=CORE_SPLITS + ["pers_11", "pers_12", "vs_nickel"], formula="plays with EPA > 0 / scrimmage plays"),
    B("ypp", "Yards per play", O, DO, "scrim", "yards", "yards/play", 1, False, splits=CORE_SPLITS, formula="Σ yards gained / scrimmage plays"),
    B("explosive", "Explosive play rate", O, DO, "scrim", "explosive", "rate", 1, True, splits=CORE_SPLITS, formula="(dropbacks >= 20 yds + runs >= 10 yds) / scrimmage plays"),
    B("first_down_rate", "First-down rate", O, DO, "scrim", "first_down", "rate", 1, True, splits=CORE_SPLITS, formula="plays gaining a first down / scrimmage plays"),
    B("turnover_rate", "Turnover rate per play", O, DO, "scrim", "turnover", "rate", -1, True, splits=["all", "red_zone", "trailing", "q4"], formula="(INT + fumbles lost) / scrimmage plays"),
    B("pass_epa", "EPA per dropback", P, DP, "dropback", "epa", "EPA/dropback", 1, False, splits=PASS_SPLITS, formula="Σ EPA on dropbacks (incl. sacks, scrambles) / dropbacks"),
    B("pass_success", "Dropback success rate", P, DP, "dropback", "success", "rate", 1, True, splits=PASS_SPLITS, formula="dropbacks with EPA > 0 / dropbacks"),
    B("comp_pct", "Completion %", P, DP, "throw", "complete", "rate", 1, True, splits=DEPTH_SPLITS + ["third_down", "red_zone", "blitz"], formula="completions / throws (sacks excluded)"),
    B("cpoe", "CPOE", P, DP, "throw", "cpoe", "pct pts", 1, False, splits=DEPTH_SPLITS + ["blitz", "third_down"], formula="mean(completion − nflfastR cp model) × 100"),
    B("ypa", "Yards per attempt", P, DP, "throw", "pass_yards", "yards/att", 1, False, splits=DEPTH_SPLITS + ["third_down", "blitz"], formula="Σ passing yards / throws"),
    B("adot", "Average depth of target", P, DP, "throw", "air_yards", "yards", 0, False, splits=["all", "pressure", "clean", "man", "zone", "third_down", "play_action"], formula="Σ air yards / throws with air yards"),
    B("yac_per_comp", "YAC per completion", RC, DP, "throw", "yac", "yards", 1, False, splits=["all", "man", "zone", "short"], formula="Σ yards after catch / completions"),
    B("yac_oe", "YAC over expected", RC, DP, "throw", "yac_oe", "yards", 1, False, splits=["all", "man", "zone"], formula="mean(YAC − nflfastR xYAC mean) on completions"),
    B("deep_rate", "Deep throw rate", P, DP, "throw", "deep", "rate", 0, True, splits=["all", "clean", "pressure", "man", "zone", "play_action"], formula="throws with air yards >= 20 / throws with air yards"),
    B("behind_los_rate", "Behind-LOS throw rate", P, DP, "throw", "behind_los", "rate", 0, True, formula="throws with air yards < 0 / throws"),
    B("int_rate", "Interception rate", P, DP, "throw", "interception", "rate", -1, True, splits=["all", "pressure", "blitz", "man", "zone", "deep", "third_down", "red_zone"], formula="interceptions / throws"),
    B("sack_rate", "Sack rate", OL, PR, "dropback", "sack", "rate", -1, True, splits=["all", "early_down", "third_down", "blitz", "no_blitz", "man", "zone", "trailing", "shotgun"], formula="sacks / dropbacks"),
    B("qb_hit_rate", "QB hit rate (incl. sacks)", OL, PR, "dropback", "qb_hit", "rate", -1, True, splits=["all", "third_down", "blitz"], formula="(QB hits + sacks) / dropbacks"),
    B("pressure_rate", "Pressure rate", OL, PR, "dropback", "pressure", "rate", -1, True, "participation", ["all", "early_down", "third_down", "blitz", "no_blitz", "shotgun", "under_center", "play_action", "trailing"], "NGS was_pressure dropbacks / charted dropbacks"),
    B("time_to_throw", "Time to throw", OL, PR, "dropback", "time_to_throw", "seconds", 0, False, "participation", ["all", "pressure", "clean", "blitz", "third_down"], "mean NGS time to throw (s)"),
    B("explosive_pass", "Explosive pass rate", P, DP, "dropback", "explosive_pass", "rate", 1, True, splits=["all", "early_down", "third_down", "man", "zone", "play_action", "two_high", "single_high"], formula="dropbacks gaining >= 20 yds / dropbacks"),
    B("pass_td_rate", "Passing TD rate", P, DP, "throw", "pass_td", "rate", 1, True, splits=["all", "red_zone"], formula="passing TDs / throws"),
    B("pass_rate", "Dropback rate", CO, CO, "scrim", "is_dropback_play", "rate", 0, True, splits=["all", "early_down", "third_down", "red_zone", "neutral", "leading", "trailing", "first_half", "second_half", "pers_11", "pers_12", "pers_21", "shotgun", "under_center"], formula="dropbacks / scrimmage plays"),
    B("proe", "Pass rate over expected", CO, CO, "scrim", "pass_oe", "rate", 0, False, splits=["all", "early_down", "neutral", "red_zone", "first_half", "second_half", "pers_12"], formula="mean(actual dropback − nflfastR xpass)"),
    B("rush_epa", "EPA per designed run", R, DR, "designed_run", "epa", "EPA/carry", 1, False, splits=RUN_SPLITS, formula="Σ EPA on designed runs / designed runs"),
    B("rush_success", "Rush success rate", R, DR, "designed_run", "success", "rate", 1, True, splits=RUN_SPLITS, formula="designed runs with EPA > 0 / designed runs"),
    B("ypc", "Yards per carry", R, DR, "designed_run", "yards", "yards/carry", 1, False, splits=RUN_SPLITS, formula="Σ rush yards / designed runs"),
    B("explosive_run", "Explosive run rate", R, DR, "designed_run", "explosive_run", "rate", 1, True, splits=["all", "light_box", "loaded_box", "inside_run", "outside_run", "pers_11", "pers_12"], formula="runs >= 10 yds / designed runs"),
    B("stuff_rate", "Run stuff rate", OL, DR, "designed_run", "stuff", "rate", -1, True, splits=["all", "early_down", "short_yardage" if False else "third_down", "red_zone", "loaded_box", "inside_run", "outside_run"], formula="runs gaining <= 0 / designed runs"),
    B("aly", "Adjusted line yards", OL, DR, "designed_run", "aly", "yards/carry", 1, False, splits=["all", "inside_run", "outside_run", "light_box", "loaded_box"], formula="FO ALY weights: <0 ×1.2, 0-4 ×1, 5-10 ×0.5, >10 ×0"),
    B("box_avg", "Defenders in box (runs)", R, DR, "designed_run", "box", "players", 0, False, "participation", ["all", "pers_11", "pers_12", "pers_21", "shotgun", "under_center"], "mean defenders in box on designed runs"),
    B("light_box_rate", "Light-box run rate", R, DR, "designed_run", "light_box", "rate", 0, True, "participation", formula="runs vs <= 6 in box / runs with box count"),
    B("loaded_box_rate", "Loaded-box run rate", R, DR, "designed_run", "loaded_box", "rate", 0, True, "participation", formula="runs vs >= 8 in box / runs with box count"),
    B("inside_run_rate", "Inside run rate", R, DR, "designed_run", "run_inside", "rate", 0, True, formula="runs middle or guard gap / runs with direction"),
    B("outside_run_rate", "Outside run rate", R, DR, "designed_run", "run_outside", "rate", 0, True, formula="runs to end gap / runs with direction"),
    B("left_run_rate", "Run left rate", R, DR, "designed_run", "run_left", "rate", 0, True, formula="runs left / runs with direction"),
    B("right_run_rate", "Run right rate", R, DR, "designed_run", "run_right", "rate", 0, True, formula="runs right / runs with direction"),
    B("scramble_rate", "Scramble rate", P, DP, "dropback", "scramble", "rate", 0, True, splits=["all", "pressure", "man", "third_down"], formula="scrambles / dropbacks"),
    B("blitz_rate", "Blitz rate faced/sent", PR, PR, "dropback", "blitz", "rate", 0, True, "ftn", ["all", "early_down", "third_down", "red_zone", "neutral"], "FTN dropbacks with >= 1 blitzer / charted dropbacks"),
    B("pass_rushers", "Average pass rushers", PR, PR, "dropback", "pass_rushers", "players", 0, False, "participation", ["all", "third_down"], "mean NGS pass rushers on dropbacks"),
    B("man_rate", "Man coverage rate", CV, CV, "dropback", "man", "rate", 0, True, "participation", ["all", "early_down", "third_down", "red_zone", "neutral", "pers_11", "pers_12"], "man-coverage dropbacks / dropbacks with coverage label"),
    B("two_high_rate", "Two-high shell rate", CV, CV, "dropback", "two_high", "rate", 0, True, "participation", ["all", "early_down", "third_down", "neutral", "pers_11"], "Cover 2/4/6/2-man / labeled dropbacks"),
    *[B(f"cover{n}_rate", f"Cover {n} rate", CV, CV, "dropback", f"cov_{n}", "rate", 0, True, "participation", ["all", "third_down"], f"Cover {n} dropbacks / labeled dropbacks") for n in ("0", "1", "2", "3", "4", "6")],
    B("play_action_rate", "Play-action rate", F, F, "dropback", "play_action", "rate", 0, True, "ftn", ["all", "early_down", "pers_12", "under_center"], "FTN play-action dropbacks / charted dropbacks"),
    B("rpo_rate", "RPO rate", F, F, "scrim", "rpo", "rate", 0, True, "ftn", formula="FTN RPO plays / charted plays"),
    B("screen_rate", "Screen rate", F, F, "throw", "screen", "rate", 0, True, "ftn", formula="FTN screen passes / charted throws"),
    B("motion_rate", "Pre-snap motion rate", F, F, "scrim", "motion", "rate", 0, True, "ftn", ["all", "early_down", "pers_11", "pers_12"], "FTN motion plays / charted plays"),
    B("shotgun_rate", "Shotgun rate", F, F, "scrim", "shotgun", "rate", 0, True, splits=["all", "early_down", "third_down", "red_zone"], formula="shotgun snaps / scrimmage plays"),
    B("no_huddle_rate", "No-huddle rate", F, F, "scrim", "no_huddle", "rate", 0, True, splits=["all", "neutral", "trailing"], formula="no-huddle snaps / scrimmage plays"),
    *[B(f"pers_{c}_rate", f"{c} personnel rate", F, F, "scrim", f"pers_{c}", "rate", 0, True, "participation", ["all", "early_down", "third_down", "red_zone"], f"{c} personnel snaps / plays with personnel") for c in ("11", "12", "21", "13", "10", "22")],
    *[B(f"pkg_{n}_rate", f"{n.title()} package rate", F, CV, "scrim", f"pkg_{n}", "rate", 0, True, "participation", ["all", "early_down", "third_down", "pers_11", "pers_12"], f"{n} defensive package snaps / plays with personnel") for n in ("base", "nickel", "dime")],
    B("out_of_pocket", "Out-of-pocket rate", P, DP, "dropback", "out_of_pocket", "rate", 0, True, "ftn", ["all", "pressure"], "FTN QB out of pocket / charted dropbacks"),
    B("int_worthy", "Interception-worthy throw rate", P, DP, "throw", "int_worthy", "rate", -1, True, "ftn", ["all", "pressure", "blitz"], "FTN interception-worthy throws / charted throws"),
    B("throwaway_rate", "Throwaway rate", P, DP, "throw", "throwaway", "rate", 0, True, "ftn", formula="FTN throwaways / charted throws"),
    B("catchable_rate", "Catchable-ball rate", P, DP, "throw", "catchable", "rate", 1, True, "ftn", ["all", "deep", "pressure"], "FTN catchable balls / charted throws"),
    B("drop_rate", "Drop rate", RC, DP, "throw", "drop", "rate", -1, True, "ftn", formula="FTN drops / charted throws"),
    B("contested_rate", "Contested target rate", RC, DP, "throw", "contested", "rate", 0, True, "ftn", formula="FTN contested balls / charted throws"),
    B("created_rec_rate", "Created reception rate", RC, DP, "throw", "created_rec", "rate", 1, True, "ftn", formula="FTN created receptions / charted throws"),
    B("first_read_rate", "First-read throw rate", P, DP, "throw", "first_read", "rate", 0, True, "ftn", formula="FTN read_thrown = 1 / throws with a read label"),
    B("checkdown_rate", "Checkdown rate", P, DP, "throw", "checkdown", "rate", 0, True, "ftn", formula="FTN read_thrown = CHK / throws with a read label"),
    B("third_conv", "3rd-down conversion rate", DD, DD, "third_down", "first_down", "rate", 1, True, formula="3rd-down plays gaining a first down / 3rd-down plays"),
    B("fourth_go_conv", "4th-down conversion rate", DD, DD, "fourth_down_play", "first_down", "rate", 1, True, formula="4th-down scrimmage plays gaining a first down / 4th-down scrimmage plays"),
    B("short_yardage_conv", "Short-yardage conversion", DD, DD, "short_yardage", "first_down", "rate", 1, True, formula="3rd/4th & <= 2 conversions / attempts"),
    B("penalty_rate", "Offensive penalty rate", CO, DO, "scrim", "penalty_off", "rate", -1, True, formula="accepted offensive penalties on scrimmage plays / scrimmage plays"),
]

# Count-per-game fields (numerator summed, denominator = games)
PER_GAME = [
    ("plays_pg", "Scrimmage plays per game", O, DO, "scrim", 0),
    ("dropbacks_pg", "Dropbacks per game", P, DP, "dropback", 0),
    ("runs_pg", "Designed runs per game", R, DR, "designed_run", 0),
    ("sacks_pg", "Sacks per game", OL, PR, "sack", -1),
    ("turnovers_pg", "Turnovers per game", O, DO, "turnover", -1),
    ("explosives_pg", "Explosive plays per game", O, DO, "explosive", 1),
    ("penalties_pg", "Offensive penalties per game", CO, DO, "penalty_off", -1),
    ("penalty_yards_pg", "Offensive penalty yards per game", CO, DO, "penalty_yards_off", -1),
    ("pass_yards_pg", "Net passing yards per game", P, DP, "pass_yards", 1),
    ("rush_yards_pg", "Rushing yards per game", R, DR, "rush_yards", 1),
    ("total_yards_pg", "Total yards per game", O, DO, "yards", 1),
]

DRIVE = [
    ("drives_pg", "Drives per game", O, DO, "count_pg", 0, "drives / games"),
    ("points_per_drive", "Points per drive", O, DO, "points", 1, "Σ offensive points / drives"),
    ("td_drive_rate", "TD drive rate", O, DO, "td", 1, "drives ending in offensive TD / drives"),
    ("score_drive_rate", "Scoring drive rate", O, DO, "score", 1, "drives with a TD or made FG / drives"),
    ("three_out_rate", "Three-and-out rate", O, DO, "three_out", -1, "drives with <= 3 plays, 0 first downs, ending in punt / drives"),
    ("turnover_drive_rate", "Turnover drive rate", O, DO, "giveaway", -1, "drives ending in INT/fumble lost / drives"),
    ("start_yardline", "Average starting field position", O, DO, "start", 1, "mean (100 − yardline_100) at drive start"),
    ("plays_per_drive", "Plays per drive", O, DO, "plays", 0, "Σ scrimmage plays / drives"),
    ("yards_per_drive", "Yards per drive", O, DO, "yards", 1, "Σ scrimmage yards / drives"),
    ("seconds_per_drive", "Seconds per drive", CO, CO, "seconds", 0, "Σ game seconds elapsed / drives"),
    ("rz_trip_rate", "Red-zone trip rate", RZ, RZ, "rz", 1, "drives reaching the opponent 20 / drives"),
    ("rz_td_rate", "Red-zone TD rate (per trip)", RZ, RZ, "rz_td", 1, "red-zone drives ending in TD / red-zone drives"),
    ("gtg_td_rate", "Goal-to-go TD rate", RZ, RZ, "gtg_td", 1, "goal-to-go drives ending in TD / goal-to-go drives"),
    ("rz_trips_pg", "Red-zone trips per game", RZ, RZ, "rz_pg", 1, "red-zone drives / games"),
    ("fg_drive_rate", "Field-goal drive rate", RZ, RZ, "fg", 0, "drives ending in made FG / drives"),
]

SPECIAL = [
    ("fg_pct", "Field-goal %", "kick_fg", 1, "made FG / FG attempts"),
    ("fg_pct_40", "Field-goal % 40+", "kick_fg40", 1, "made FG / attempts of >= 40 yds"),
    ("fg_pct_50", "Field-goal % 50+", "kick_fg50", 1, "made FG / attempts of >= 50 yds"),
    ("xp_pct", "Extra-point %", "kick_xp", 1, "good XP / XP attempts"),
    ("punt_net", "Net punt yards", "punt_net", 1, "mean (kick distance − return yards) on punts (touchback −20)"),
    ("punt_in20_rate", "Punts inside 20", "punt_in20", 1, "punts ending inside opp 20 / punts"),
    ("kick_tb_rate", "Kickoff touchback rate", "ko_tb", 0, "kickoff touchbacks / kickoffs"),
    ("ko_return_avg", "Kickoff return average", "ko_ret", 1, "return yards / kickoffs returned"),
    ("punt_return_avg", "Punt return average", "pr_ret", 1, "return yards / punts returned"),
    ("st_epa", "Special-teams EPA per play", "st_epa", 1, "Σ EPA (kicking/receiving team perspective) / ST plays"),
    ("fg_att_pg", "FG attempts per game", "fg_att_pg", 0, "FG attempts / games"),
    ("punts_pg", "Punts per game", "punts_pg", -1, "punts / games"),
]

GAME = [
    ("points_pg", "Points per game", O, DO, 1, "final team score / games"),
    ("margin_pg", "Point margin per game", O, DO, 1, "(points for − points against) / games"),
    ("win_pct", "Win %", O, DO, 1, "(wins + 0.5 ties) / games"),
]


@dataclass
class Field:
    id: str
    side: str
    base: str
    split: str
    label: str
    category: str
    unit: str
    direction: int
    binary: bool
    source: str
    formula: str
    denominator: str
    kind: str = "play"

    def coverage(self) -> str:
        lo, hi = COVERAGE[self.source]
        return f"{lo}-{hi or 'present'}"


def resolve_source(a: str, b: str) -> str:
    need = {x for x in (a, b) if x != "pbp"}
    return "pbp" if not need else ("participation+ftn" if len(need) == 2 else need.pop())


def fields() -> list[Field]:
    out: list[Field] = []
    for b in BASES:
        for side in ("off", "def"):
            for sp in b.splits:
                src = resolve_source(b.source, SPLIT_SOURCE.get(sp, "pbp"))
                label = b.label + ("" if sp == "all" else f" — {SPLITS[sp][0]}")
                cat = SPLITS[sp][1] or (b.category if side == "off" else b.def_category)
                if side == "def" and cat in (O, P, R, RC):
                    cat = {O: DO, P: DP, R: DR, RC: DP}[cat]
                out.append(Field(f"{side}.{b.key}.{sp}", side, b.key, sp, label, cat, b.unit,
                                 b.direction if side == "off" else -b.direction, b.binary, src,
                                 b.formula + ("" if sp == "all" else f"; restricted to {SPLITS[sp][0].lower()}"),
                                 b.pop))
    for key, label, cat, dcat, pop, direction in PER_GAME:
        for side in ("off", "def"):
            out.append(Field(f"{side}.{key}.all", side, key, "all", label + ("" if side == "off" else " allowed"),
                             cat if side == "off" else dcat, "per game", direction if side == "off" else -direction,
                             False, "pbp", f"Σ {pop} / games", "games", "per_game"))
    for key, label, cat, dcat, num, direction, formula in DRIVE:
        for side in ("off", "def"):
            out.append(Field(f"{side}.{key}.all", side, key, "all", label + ("" if side == "off" else " allowed"),
                             cat if side == "off" else (dcat if dcat != cat else cat), "per drive" if "pg" not in key else "per game",
                             direction if side == "off" else -direction, num in ("td", "score", "three_out", "giveaway", "rz", "rz_td", "gtg_td", "fg"),
                             "pbp", formula, "drives", "drive"))
    for key, label, num, direction, formula in SPECIAL:
        out.append(Field(f"st.{key}.all", "st", key, "all", label, ST, "rate" if "pct" in key or "rate" in key else "value",
                         direction, "pct" in key or "rate" in key, "pbp", formula, num, "special"))
    for key, label, cat, dcat, direction, formula in GAME:
        out.append(Field(f"off.{key}.all", "off", key, "all", label, cat, "value", direction, key == "win_pct", "pbp", formula, "games", "game"))
    out.append(Field("def.points_allowed_pg.all", "def", "points_allowed_pg", "all", "Points allowed per game", DO, "per game", -1, False, "pbp", "opponent final score / games", "games", "game"))
    return out


FIELDS = fields()
FIELD_INDEX = {f.id: f for f in FIELDS}


# ---------------------------------------------------------------- aggregation

def _bincount(idx: np.ndarray, weights: np.ndarray, n: int) -> np.ndarray:
    w = np.nan_to_num(weights, nan=0.0)
    return np.bincount(idx, weights=w, minlength=n)


def team_games_for_season(season: int) -> pd.DataFrame:
    p = lake.load_plays([season])
    if p.empty:
        return pd.DataFrame()
    p = p[p["posteam"].notna() & p["defteam"].notna()].reset_index(drop=True)
    d = derive(p)
    rows = {}
    keys = {}
    for side, team_col, opp_col in (("off", "posteam", "defteam"), ("def", "defteam", "posteam")):
        key = p["game_id"].astype(str) + "|" + p[team_col].astype(str)
        codes, uniq = pd.factorize(key)
        keys[side] = (codes, uniq)
    # unify team-game index across sides
    all_keys = pd.Index(sorted(set(keys["off"][1]) | set(keys["def"][1])))
    n = len(all_keys)
    out = {}
    for side in ("off", "def"):
        codes, uniq = keys[side]
        idx = all_keys.get_indexer(uniq)[codes]
        for b in BASES:
            pop = d[b.pop].to_numpy() == 1
            val = d[b.value].to_numpy(dtype=float)
            for sp in b.splits:
                m = pop & (d["s_" + sp].to_numpy() if sp != "all" else True) & ~np.isnan(val)
                fid = f"{side}.{b.key}.{sp}"
                out[fid + "__n"] = _bincount(idx, np.where(m, val, 0.0), n)
                out[fid + "__d"] = _bincount(idx, m.astype(float), n)
                if not b.binary:
                    out[fid + "__q"] = _bincount(idx, np.where(m, val * val, 0.0), n)
        for key, _l, _c, _dc, pop, _dir in PER_GAME:
            col = d[pop].to_numpy(dtype=float) if pop in d else None
            fid = f"{side}.{key}.all"
            if key == "pass_yards_pg":
                vals = np.where(d["dropback"] == 1, np.nan_to_num(d["pass_yards"].to_numpy()) - d["sack_yards"].to_numpy(), 0.0)
            elif key == "total_yards_pg":
                vals = np.where(d["scrim"] == 1, np.nan_to_num(d["yards"].to_numpy()), 0.0)
            else:
                vals = np.nan_to_num(col)
            out[fid + "__n"] = _bincount(idx, vals, n)
            out[fid + "__d"] = np.ones(n)
    tg = pd.DataFrame(out)
    tg["game_id"] = [k.split("|")[0] for k in all_keys]
    tg["team"] = [k.split("|")[1] for k in all_keys]
    tg = tg.merge(_drive_metrics(p, d), on=["game_id", "team"], how="left")
    tg = tg.merge(_special_metrics(p), on=["game_id", "team"], how="left")
    return tg


def _drive_metrics(p: pd.DataFrame, d: pd.DataFrame) -> pd.DataFrame:
    q = pd.DataFrame({
        "game_id": p["game_id"], "team": p["posteam"], "opp": p["defteam"], "drive": p["fixed_drive"],
        "scrim": d["scrim"], "yards": np.where(d["scrim"] == 1, np.nan_to_num(d["yards"]), 0.0),
        "first_down": np.nan_to_num(d["first_down"]), "yl": p["yardline_100"], "gsr": p["game_seconds_remaining"],
        "result": p["fixed_drive_result"].astype("string"), "gtg": p["goal_to_go"],
        "pts": 6.0 * ((p["touchdown"] == 1) & (p["td_team"] == p["posteam"])) + 1.0 * (p["extra_point_result"] == "good")
               + 2.0 * (p["two_point_conv_result"] == "success") + 3.0 * (p["field_goal_result"] == "made"),
    })
    q = q[q["drive"].notna() & (p["play_type"].astype("string") != "kickoff").fillna(True).to_numpy()]
    g = q.groupby(["game_id", "team", "drive"], sort=False)
    dr = g.agg(plays=("scrim", "sum"), yards=("yards", "sum"), fd=("first_down", "sum"), start=("yl", "first"),
               minyl=("yl", "min"), t0=("gsr", "max"), t1=("gsr", "min"), result=("result", "first"),
               pts=("pts", "sum"), gtg=("gtg", "max"), opp=("opp", "first")).reset_index()
    dr = dr[dr["plays"] > 0]
    dr["td"] = (dr["result"] == "Touchdown").astype(float)
    dr["fg"] = (dr["result"] == "Field goal").astype(float)
    dr["score"] = dr["td"] + dr["fg"]
    dr["giveaway"] = (dr["result"] == "Turnover").astype(float)
    dr["three_out"] = ((dr["result"] == "Punt") & (dr["fd"] == 0) & (dr["plays"] <= 3)).astype(float)
    dr["rz"] = (dr["minyl"] <= 20).astype(float)
    dr["rz_td"] = dr["rz"] * dr["td"]
    dr["gtg_d"] = (dr["gtg"] == 1).astype(float)
    dr["gtg_td"] = dr["gtg_d"] * dr["td"]
    dr["start"] = 100 - dr["start"]
    dr["seconds"] = (dr["t0"] - dr["t1"]).clip(lower=0)
    dr["points"] = dr["pts"].clip(upper=8)
    out = []
    for side, tcol in (("off", "team"), ("def", "opp")):
        agg = dr.groupby(["game_id", tcol]).agg(
            drives=("plays", "size"), points=("points", "sum"), td=("td", "sum"), score=("score", "sum"),
            three_out=("three_out", "sum"), giveaway=("giveaway", "sum"), start=("start", "sum"), plays=("plays", "sum"),
            yards=("yards", "sum"), seconds=("seconds", "sum"), rz=("rz", "sum"), rz_td=("rz_td", "sum"),
            gtg=("gtg_d", "sum"), gtg_td=("gtg_td", "sum"), fg=("fg", "sum")).reset_index().rename(columns={tcol: "team"})
        f = pd.DataFrame({"game_id": agg["game_id"], "team": agg["team"]})
        for key, _l, _c, _dc, num, _dir, _f in DRIVE:
            fid = f"{side}.{key}.all"
            if key == "drives_pg":
                f[fid + "__n"], f[fid + "__d"] = agg["drives"], 1.0
            elif key == "rz_trips_pg":
                f[fid + "__n"], f[fid + "__d"] = agg["rz"], 1.0
            elif key == "rz_td_rate":
                f[fid + "__n"], f[fid + "__d"] = agg["rz_td"], agg["rz"]
            elif key == "gtg_td_rate":
                f[fid + "__n"], f[fid + "__d"] = agg["gtg_td"], agg["gtg"]
            else:
                f[fid + "__n"], f[fid + "__d"] = agg[num], agg["drives"]
        out.append(f)
    return out[0].merge(out[1], on=["game_id", "team"], how="outer")


def _special_metrics(p: pd.DataFrame) -> pd.DataFrame:
    pt = p["play_type"].astype("string")
    fg = (pt == "field_goal").fillna(False)
    xp = (pt == "extra_point").fillna(False)
    punt = (pt == "punt").fillna(False)
    ko = (pt == "kickoff").fillna(False)
    made = (p["field_goal_result"] == "made").astype(float)
    dist = pd.to_numeric(p["kick_distance"], errors="coerce")
    tb = pd.to_numeric(p["touchback"], errors="coerce").fillna(0)
    ret = pd.to_numeric(p["return_yards"], errors="coerce").fillna(0)
    epa = pd.to_numeric(p["epa"], errors="coerce").fillna(0)
    yl_end = pd.to_numeric(p["yardline_100"], errors="coerce") - dist.fillna(0) + ret
    rows = []
    def add(team, mask, key, num, den):
        rows.append(pd.DataFrame({"game_id": p.loc[mask, "game_id"], "team": team[mask], "k": key,
                                  "n": np.asarray(num)[mask.to_numpy()], "d": np.asarray(den)[mask.to_numpy()]}))
    kicking_ko = p["defteam"]  # nflfastR: posteam on kickoffs is the receiving team
    add(p["posteam"], fg, "fg_pct", made, 1.0 + 0 * made)
    add(p["posteam"], fg & (dist >= 40), "fg_pct_40", made, 1.0 + 0 * made)
    add(p["posteam"], fg & (dist >= 50), "fg_pct_50", made, 1.0 + 0 * made)
    add(p["posteam"], xp, "xp_pct", (p["extra_point_result"] == "good").astype(float), 1.0 + 0 * made)
    net = np.where(tb == 1, dist.fillna(0) - 20, dist.fillna(0) - ret)
    add(p["posteam"], punt & dist.notna(), "punt_net", net, 1.0 + 0 * made)
    in20 = pd.to_numeric(p["punt_inside_twenty"], errors="coerce").fillna(0).astype(float)
    add(p["posteam"], punt & dist.notna(), "punt_in20_rate", in20, 1.0 + 0 * made)
    add(kicking_ko, ko, "kick_tb_rate", tb.astype(float), 1.0 + 0 * made)
    returned_ko = ko & (tb == 0) & (ret != 0)
    add(p["posteam"], returned_ko, "ko_return_avg", ret, 1.0 + 0 * made)
    returned_punt = punt & (tb == 0) & (ret != 0)
    add(p["defteam"], returned_punt, "punt_return_avg", ret, 1.0 + 0 * made)
    st_mask = fg | xp | punt | ko
    st_team = np.where(ko, p["defteam"], p["posteam"])
    add(pd.Series(st_team, index=p.index), st_mask & p["epa"].notna(), "st_epa", epa, 1.0 + 0 * made)
    add(p["posteam"], fg, "fg_att_pg", 1.0 + 0 * made, 0.0 * made)
    add(p["posteam"], punt, "punts_pg", 1.0 + 0 * made, 0.0 * made)
    r = pd.concat(rows, ignore_index=True)
    agg = r.groupby(["game_id", "team", "k"])[["n", "d"]].sum().unstack("k")
    f = pd.DataFrame(index=agg.index)
    for key, *_ in SPECIAL:
        n = agg["n"][key] if key in agg["n"] else 0.0
        dd = agg["d"][key] if key in agg["d"] else 0.0
        if key in ("fg_att_pg", "punts_pg"):
            dd = 1.0
        f[f"st.{key}.all__n"] = n
        f[f"st.{key}.all__d"] = dd
    return f.fillna(0.0).reset_index()


def build(first: int | None = None, last: int | None = None, verbose: bool = True) -> pd.DataFrame:
    """Compute the team-game field matrix for all seasons and write ``lake/team_games.parquet``."""
    cfg = settings()
    first = first or cfg.first_season
    last = last or cfg.last_season
    games = lake.load("games")
    frames = []
    for s in range(first, last + 1):
        tg = team_games_for_season(s)
        if tg.empty:
            continue
        frames.append(tg)
        if verbose:
            print("team_games", s, tg.shape, flush=True)
    tg = pd.concat(frames, ignore_index=True)
    meta = games[["game_id", "season", "week", "game_type", "home_team", "away_team", "home_score", "away_score",
                  "kickoff", "roof", "temp", "wind", "div_game", "home_rest", "away_rest", "away_travel_miles"]]
    tg = tg.merge(meta, on="game_id", how="left")
    home = tg["team"] == tg["home_team"]
    tg["is_home"] = home.astype(float)
    tg["opponent"] = np.where(home, tg["away_team"], tg["home_team"])
    tg["points_for"] = np.where(home, tg["home_score"], tg["away_score"])
    tg["points_against"] = np.where(home, tg["away_score"], tg["home_score"])
    done = tg["points_for"].notna()
    tg["off.points_pg.all__n"], tg["off.points_pg.all__d"] = tg["points_for"].where(done, 0), done.astype(float)
    tg["def.points_allowed_pg.all__n"], tg["def.points_allowed_pg.all__d"] = tg["points_against"].where(done, 0), done.astype(float)
    tg["off.margin_pg.all__n"] = (tg["points_for"] - tg["points_against"]).where(done, 0)
    tg["off.margin_pg.all__d"] = done.astype(float)
    tg["off.win_pct.all__n"] = np.where(done, (tg["points_for"] > tg["points_against"]) + 0.5 * (tg["points_for"] == tg["points_against"]), 0)
    tg["off.win_pct.all__d"] = done.astype(float)
    tg = tg.fillna({c: 0.0 for c in tg.columns if c.endswith(("__n", "__d", "__q"))})
    tg = tg.copy()
    tg.to_parquet(cfg.lake_dir / "team_games.parquet", index=False)
    return tg


@lru_cache(maxsize=1)
def load_team_games() -> pd.DataFrame:
    return pd.read_parquet(settings().lake_dir / "team_games.parquet")


if __name__ == "__main__":
    build()
