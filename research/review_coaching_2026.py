"""Reviewed qualitative coaching descriptions for the November 3 collection.

These are source descriptions, not a fitted model or automatic biography parser.
Re-review after staff/caller changes; do not apply them to earlier kickoffs.
"""
import argparse
import json
from pathlib import Path

from .source_based_parlays import DOMAINS
from football_engine.team_profiles import content_hash


# Source indices refer to the retained, ordered coaching-evidence-combined.json.
# Each output fact retains the actual URL, archive hash and observation time.
DESCRIPTIONS = {
 "BUF": (216, "Allen-centered distribution that adapts calls to available receivers, tight ends, backs and blocking personnel; earlier Brady background, not a fixed target-share rule.", 94, "Three-down base with flexible subpackages; outside linebackers can rush or cover. Assess pressure origin and personnel rather than treating every snap as base 3-4."),
 "MIA": (203, "Slowik emphasizes a strong run game and complementary passing within a Shanahan-derived foundation adapted to the players; shared lineage does not mean identical calls or usage.", 203, "Hafley and Duggan bring a shared defensive background; current formation and coverage percentages require game-level charting rather than inheritance from Green Bay."),
 "NE": (226, "Recent film review describes more shotgun in the opening script and increased motion and play-action after early-season struggles. Treat these as dated adjustments.", 92, "Kuhr changes man/zone answers by opponent; the Miami review identifies zone communication and out-breaking routes as review areas, not permanent weaknesses."),
 "NYJ": (4, "Team analysis expects West Coast passing elements paired with a committed ground game under Reich; this is a reported expectation, not a measured formation mix.", 0, "Glenn owns defensive calls while Duker assists; aggressive calls and versatile safety assignments can change the post-snap picture without substituting personnel."),
 "BAL": (255, "Doyle's Johnson/Payton background offers possible formation and run-game variations. These are reported hypotheses; do not transplant Chicago target shares or terminology unchanged.", 249, "Minter calls the defense; Weaver handles supporting game-day responsibilities. Specific current coverage and pressure mixes remain unmeasured in this collection."),
 "CIN": (133, "Offseason self-scouting emphasizes early-down explosives and ball security, with more under-center work discussed as an option rather than a verified season-long shift.", 90, "Golden uses defensive-line rotation to sustain the rush. Evaluate the available rushers and current rotation instead of assigning one blanket pressure rate."),
 "CLE": (11, "Monken and Switzer installed a new, assignment-detailed offense emphasizing fast, accurate alignment and execution. New receivers face a substantial installation burden.", 252, "Rutenberg emphasizes takeaways across all three levels. This states a coaching objective; it does not establish a new interception probability or coverage distribution."),
 "PIT": (146, "McCarthy describes a quarterback-friendly West Coast framework linking the ground game, protection and action passing; Angelichio helps build the offense.", 141, "Graham emphasizes explaining assignments and why calls fit the personnel. Detailed weekly front and coverage frequencies are not established by this report."),
 "HOU": (15, "Caley was selected for adaptability across systems and positions rather than allegiance to one fixed scheme. Current execution and protection must drive player projections.", 22, "Ryans has taken over defensive calling. Reassess matchup assumptions under the actual caller instead of automatically carrying forward coordinator-title assumptions."),
 "IND": (23, "Steichen and the quarterback build checks into protections, routes and runs. Rollout play-action and two-tight-end flexibility can connect the passing and rushing plans.", 14, "The defense's on-field communication has changed to a linebacker green-dot role after tempo issues. An on-field relay assignment is not the coordinator's play-calling job."),
 "JAX": (121, "Coen's offensive continuity and Udinski's detailed opponent preparation support shared terminology and weekly matchup answers; avoid assuming a new system when staff is retained.", 119, "Campanile's offseason work emphasizes situational preparation and roster depth. Detailed front, coverage and blitz frequencies still need direct charting."),
 "TEN": (258, "Saleh's current report credits Daboll with creating open receivers; evaluate Cam Ward's execution and personnel context separately from that coaching assessment.", 18, "Saleh calls a speed-and-physicality defense with Bradley assisting. Coverage and pressure variations are intended to remain simple enough for fast execution."),
 "DEN": (25, "Payton reassumed offensive calls for Week 5 after Webb opened the season in that role; offensive-era comparisons must respect that change.", 31, "Joseph describes opponent-aware offensive and defensive problem-solving in practice. This source does not establish a fixed current coverage or blitz percentage."),
 "KC": (156, "Reid's offensive coaching lineage is background for the current staff; exact present formation, route and red-zone usage must come from current game evidence.", 101, "Spagnuolo's documented background includes four-down structure and flexible pass-rush packages. Historical scheme background is not a certificate of today's subpackage mix."),
 "LV": (30, "Kubiak's practice plan combines wide-zone runs, play-action and quick throws designed around the featured tight end and back; practice observations are not guaranteed regular-season usage.", 32, "Leonard's early plan uses a three-down base, movable linebackers and defensive backs, and a simple player-led approach. One debut game's rush results cannot be a season forecast."),
 "LAC": (34, "McDaniel emphasizes decisive, fast execution and getting the defense reacting; receiving-role gains still depend on routes, personnel and health.", 24, "Early-season reporting finds substantial pressure-system continuity under O'Leary and a changed dime package share influenced by injuries. Separate personnel effects from a wholesale scheme change."),
 "DAL": (45, "Schottenheimer retains offensive calls; evaluate the current passing plan alongside the run-design and blocking expertise Adams brings, without inferring a fixed run rate.", 44, "Parker describes a personnel-first approach and collaborative coaching. Do not infer every coverage or pressure call from his prior Philadelphia employment."),
 "NYG": (116, "Nagy's installation blends concepts and emphasizes positional flexibility; the head coach oversees the operation rather than replacing every position specialist.", 115, "Team preview describes a run-defense wall and common two-high presentations with changing post-snap coverage. Increased creative pressure was a preview expectation, not a measured season rate."),
 "PHI": (36, "Mannion builds on familiar Hurts formations and runs while adding motion, new concepts and role flexibility; retained successful plays coexist with a new caller.", "bio:defensive_coordinator", "Fangio's current biography documents extensive defensive coordination experience and prior defensive results; detailed present split-safety, coverage and pressure rates remain unverified here."),
 "WSH": (39, "Blough plans more huddle and under-center work, opening complementary runs, play-action and keepers; inherited concepts do not imply identical Kingsbury-era usage.", 40, "Jones installs a defense with multiple checks and presentations. Communication and personnel familiarity matter; exact pressure and coverage rates remain separate inputs."),
 "CHI": (48, "Johnson and Taylor organize opponent-specific answers and may adjust blocking to a changed center. Installation and personnel fit should shape weekly opportunity assumptions.", 59, "Allen mixes coverages and coordinates four-man rushes with added pressures, aiming to shrink throwing windows. Rush and coverage effectiveness must be modeled together."),
 "DET": (49, "Petzing's new play-calling role emphasizes offensive physicality; validate current formations, run concepts and tight-end usage rather than reusing the previous coordinator's assumptions.", 58, "Sheppard emphasizes versatile players, anticipation from personnel and down-and-distance, and communicating assignments; versatility must not replace a player's strongest role."),
 "GB": (52, "Recent reporting focuses on red-zone execution and situational fourth-down choices under LaFleur. A single decision does not establish a stable aggression rate or offensive scheme mix.", 99, "Gannon disguises rush and coverage intentions and adapts to the safety group. Pressure can come from different levels rather than a single predictable source."),
 "MIN": (164, "O'Connell's approach emphasizes quarterback communication and player relationships. This supports qualitative leadership context, not an automatic quarterback performance uplift.", 163, "Flores uses an unusually pressure-heavy current approach with disguised coverage. The cited report also flags downfield-explosive vulnerability; retain the report's time scope."),
 "ATL": (69, "Stefanski and Rees combine wide-zone and gap runs with varied presentation. Under-center and play-action elements complement that ground-game structure.", 68, "Ulbrich remains the defensive coordinator under the new head coach. Continuity and current personnel must be distinguished from the offensive coaching overhaul."),
 "CAR": (66, "Canales remains responsible for team-wide execution and offensive improvement during a defensive staff reset. Detailed offensive run/pass concepts are not established by this change report.", 66, "Wash's interim plan is a reset focused on front play, run mechanics and better use of available talent. Do not label it a proven new coverage system before post-change games."),
 "NO": ("bio:head_coach", "Moore and Nussmeier describe fitting the offense to the quarterback; do not assume prior Dallas or Philadelphia usage carries over unchanged to New Orleans.", "bio:defensive_coordinator", "Staley's prior defensive coordination is documented in his biography. A detailed current front and coverage specification remains unavailable in these collected sources."),
 "TB": (64, "Robinson plans to retain gap and duo runs while adding more wide-zone work and connecting run/pass presentations. Two-tight-end and pistol usage are preview clues, not measured Tampa Bay tendencies.", "bio:head_coach", "Bowles combines head-coaching responsibility with documented defensive play-calling experience. No separately titled defensive coordinator appears in the current directory."),
 "ARI": (82, "Mike LaFleur emphasizes timely calls and a clean huddle as the offense is installed. Tactical formation and run-concept frequencies require current charting.", 73, "Rallis retains his calling responsibilities in the October staff review; current coverage and pressure structure must be measured rather than inferred from the retained title."),
 "LAR": ("bio:head_coach", "McVay's offensive leadership and prior quarterback development are documented; exact current run/pass marriage, formations and receiving distribution remain charting requirements.", 193, "Shula draws on several defensive mentors, simplifies teaching and adapts assignments to players. That is a hybrid teaching approach, not one inherited mentor's complete scheme."),
 "SF": (83, "Kubiak and Shanahan describe distributing work through the offense and developing personnel; practice calling must be distinguished from regular-season game calling.", 108, "Morris builds on the existing four-down foundation while allowing four- and five-man fronts. Camp experimentation does not establish a fixed season front percentage."),
 "SEA": (244, "Fleury brings a run-first emphasis, shared execution standards and collaborative weekly adjustments; the new caller seeks continuity rather than a wholesale offensive replacement.", 75, "Macdonald retains the defensive system while Durde has received preseason calling opportunities. Preseason delegation alone does not establish regular-season calling ownership."),
}

# Only explicit reports of calling ownership are marked confirmed.
CALLERS = {
 "BUF": (("Joe Brady",95),("Jim Leonhard",84)), "MIA": ((None,203),(None,203)),
 "NE": (("Josh McDaniels",226),("Zak Kuhr",92)), "NYJ": (("Frank Reich",2),("Aaron Glenn",1)),
 "BAL": (("Declan Doyle",249),("Jesse Minter",249)), "CIN": (("Zac Taylor",133),(None,90)),
 "CLE": ((None,11),("Mike Rutenberg",250)), "PIT": (("Mike McCarthy",143),("Patrick Graham",141)),
 "HOU": ((None,21),("DeMeco Ryans",22)), "IND": ((None,23),(None,14)),
 "JAX": ((None,"bio:head_coach"),(None,119)), "TEN": ((None,258),("Robert Saleh",18)),
 "DEN": (("Sean Payton",25),(None,31)), "KC": ((None,"bio:head_coach"),(None,"bio:defensive_coordinator")),
 "LV": ((None,30),("Rob Leonard",32)), "LAC": (("Mike McDaniel",27),("Chris O'Leary",27)),
 "DAL": (("Brian Schottenheimer",45),("Christian Parker",42)), "NYG": ((None,116),("Dennard Wilson",41)),
 "PHI": (("Sean Mannion",36),(None,"bio:defensive_coordinator")), "WSH": (("David Blough",40),("Daronte Jones",40)),
 "CHI": (("Ben Johnson",53),("Dennis Allen",50)), "DET": (("Drew Petzing",49),(None,58)),
 "GB": ((None,52),("Jonathan Gannon",98)), "MIN": (("Kevin O'Connell",164),("Brian Flores",164)),
 "ATL": (("Tommy Rees",61),(None,68)), "CAR": (("Brad Idzik",262),("Todd Wash",66)),
 "NO": ((None,"bio:head_coach"),(None,"bio:defensive_coordinator")),
 "TB": ((None,64),(None,"bio:head_coach")), "ARI": (("Mike LaFleur",77),("Nick Rallis",73)),
 "LAR": ((None,"bio:head_coach"),(None,193)), "SF": ((None,72),(None,108)),
 "SEA": (("Brian Fleury",81),(None,75)),
}

MANAGEMENT = {
 "BUF": (95, "Brady seeks attacking, versatile defensive leadership while keeping responsibility for the offense."),
 "NYJ": (0, "Glenn takes direct ownership of defensive calls and develops Duker through shared game-planning and mentoring."),
 "NYG": (116, "Harbaugh oversees the full operation, delegates position detail and maintains direct involvement in special teams."),
 "PHI": ("bio:head_coach", "Sirianni describes organizational standards built around toughness, detail and collective execution."),
 "CHI": (51, "Johnson emphasizes demanding practice standards, physical effort and execution away from the ball."),
 "DET": ("bio:head_coach", "Campbell's documented leadership themes are resilience, collaboration and conviction."),
 "MIN": (164, "O'Connell emphasizes relationships and communication with quarterbacks; Flores invites player input on defensive ideas."),
 "CAR": (262, "Canales oversees timeouts and the wider team operation while Idzik calls the offense; this delegation creates more room for head-coaching duties."),
 "ARI": (82, "LaFleur emphasizes fast communication, a clean huddle and consistent operation during installation."),
 "MIA": (203, "Hafley and his coordinators describe adapting shared system foundations to their actual players rather than copying another team's offense unchanged."),
 "NE": ("bio:head_coach", "Vrabel's introduction stresses a focused plan for the roster and returning to championship standards; specific situational tendencies remain unmeasured."),
 "BAL": (249, "Minter keeps defensive calling while Doyle runs offensive calls and Weaver supports sideline operations; responsibilities are deliberately split."),
 "CIN": ("bio:head_coach", "Taylor's biography describes an offense-led head-coaching role and a long-running partnership developing the passing attack."),
 "CLE": (11, "Monken emphasizes accurate assignments, technique and fast execution; receiver evaluation includes work without guaranteed targets."),
 "PIT": (146, "McCarthy links quarterback success to physical rushing, protection and complementary passing rather than treating the passing plan in isolation."),
 "HOU": (15, "Ryans values adaptable assistants who develop players across positions and are not tied to one rigid system."),
 "IND": (23, "Steichen collaborates with the quarterback on routes, protection and run checks, giving the offense situational answers at the line."),
 "JAX": (121, "Coen values staff continuity and detailed preparation that reduces uncertainty for players on game day."),
 "TEN": (18, "Saleh and Bradley prioritize clear assignments and physical speed; added complexity is subordinate to players executing confidently."),
 "DEN": (25, "Payton changes delegation when he believes the team needs it, taking offensive calling back while retaining Webb as coordinator."),
 "KC": ("bio:head_coach", "Reid's long-standing leadership and coaching-development background are documented; current clock and fourth-down policies are not quantified here."),
 "LV": (30, "Kubiak organizes the plan around the strongest offensive players and seeks simple completions and advantageous run looks for them."),
 "LAC": (27, "Harbaugh's staff delegates game-day calls to the offensive and defensive coordinators; both described sideline calling responsibilities."),
 "DAL": ("bio:head_coach", "Schottenheimer's biography describes competition, connection and passion as leadership themes; these are not numerical aggression estimates."),
 "GB": (96, "LaFleur supports defensive problem-solving around the available players rather than selecting a coordinator solely for a fixed playbook."),
 "ATL": (61, "Stefanski delegates offensive calling to Rees within their shared system rather than assuming the head coach always calls the offense."),
 "NO": ("bio:head_coach", "Moore and his staff describe adapting the offensive approach to the quarterback's abilities; development and actual usage remain distinct."),
 "TB": ("bio:head_coach", "Bowles brings a defensive-coordination background to head coaching; specific present delegation and game-management policies need dated confirmation."),
 "LAR": (193, "McVay's staff supports collaborative defensive teaching under Shula, combining ideas from multiple mentors rather than one inflexible template."),
 "SF": (108, "Shanahan's staff preserves established standards while allowing Morris to adapt the defensive front to the players."),
 "SEA": (81, "Macdonald prioritized offensive continuity when choosing Fleury, allowing new ideas within the team's established execution standards."),
}

NEW_HEADS = {"BUF", "MIA", "BAL", "CLE", "PIT", "TEN", "LV", "NYG", "ATL", "ARI"}

TACTICAL_FOUNDATIONS = {
 "BAL": ("defense",0,"Jesse Minter","REPORTED_2026_PLAN", "Concept-based teaching, late coverage rotation and disguise aim to complicate the quarterback's read while keeping defender assignments understandable."),
 "MIA": ("defense",3,"Jeff Hafley","HISTORICAL_BACKGROUND", "Hafley's background includes four-down, single-high structure and later adaptation; front and coverage disguise plus varied pressure are recurring principles, not a fixed current coverage menu."),
 "CLE": ("defense",6,"Mike Rutenberg","REPORTED_2026_PLAN", "An attacking four-down foundation starts with the defensive front, getting downhill and pursuing the ball. The stated priority is fast, physical execution before additional complexity."),
 "PIT": ("defense",9,"Patrick Graham","REPORTED_2026_PLAN", "Roster construction supports three-down and four-down fronts. Graham describes a smaller early-down menu aimed at stopping runs and deep shots, with more options on long-yardage downs."),
 "HOU": ("defense",12,"DeMeco Ryans","HISTORICAL_BACKGROUND", "Ryans and Burke's original shared plan emphasized an aggressive attacking front that gets off the ball and dictates rather than simply reacts. Recheck its current implementation after calling changes."),
 "IND": ("defense",17,"Lou Anarumo","HISTORICAL_BACKGROUND", "Opponent-specific flexibility includes changing coverage numbers and occasionally dropping eight instead of reflexively blitzing. A famous prior matchup is an example, not a default Colts call."),
 "JAX": ("defense",19,"Anthony Campanile","HISTORICAL_BACKGROUND", "Campanile came from linebackers and run-game coordination; this supports reviewing front fits and linebacker responsibilities, but does not establish a current coverage or blitz distribution."),
 "DEN": ("defense",26,"Vance Joseph","CURRENT_SEASON_OBSERVATION", "Joseph connects run stopping and tackling to creating clear passing downs for the rush. Heavy offensive personnel can require both larger run-defense bodies and flexible pass coverage."),
 "KC": ("offense",27,"Andy Reid","HISTORICAL_BACKGROUND", "Reid has described screens as moving a blocking scheme into space, with the back setting up blocks and linemen reaching faster defenders. Current screen frequency remains a separate input."),
 "PHI": ("defense",31,"Vic Fangio","CURRENT_SEASON_OBSERVATION", "Fangio changes secondary assignments with player versatility; reporting describes DeJean moving between nickel and safety, including more safety work in base formations this season."),
 "GB": ("offense",33,"Matt LaFleur","HISTORICAL_BACKGROUND", "The documented outside-zone foundation moves blockers together and asks the back to choose a cut rather than forcing one predetermined hole. It does not establish today's zone-versus-gap percentage."),
 "ATL": ("defense",40,"Jeff Ulbrich","REPORTED_2026_PLAN", "Ulbrich favors a manageable menu with enough multiplicity for modern offenses, prioritizing communication and execution rather than maximum exotic blitz and coverage volume."),
 "CAR": ("offense",42,"Dave Canales","HISTORICAL_BACKGROUND", "Canales' earlier offensive description combines concepts and teaches receivers the whole route tree rather than only slot or outside jobs. Current Idzik usage must be evaluated separately."),
 "NO": ("defense",46,"Brandon Staley","CURRENT_SEASON_OBSERVATION", "Second-year installation is described as improving linebacker recognition and decisive triggering. It supports reviewing role familiarity without inventing a split-safety percentage."),
 "TB": ("defense",48,"Todd Bowles","REPORTED_2026_PLAN", "Bowles describes a hybrid three-down base and 4-2-5 nickel against three-receiver offenses, with coverage-capable edges, versatile roles and disguised coverage in lighter boxes."),
 "LAR": ("offense",54,"Sean McVay","HISTORICAL_BACKGROUND", "The 2025 run-game review describes a shift toward duo while retaining outside-zone answers to favorable fronts. Defensive structure and box reactions determine the choice rather than one rigid concept."),
 "SF": ("offense",57,"Kyle Shanahan","HISTORICAL_BACKGROUND", "Wide-zone runs, condensed formations and versatile personnel create run/pass conflicts through sequencing. The cited background also documents evolution away from assuming constant play-action usage."),
 "SEA": ("defense",61,"Mike Macdonald","REPORTED_2026_PLAN", "The system aims to rush effectively with or without extra rushers and defend the run without always adding larger personnel. Continuity helps players anticipate coverage problems and communicate adjustments."),
}


def run(directory: Path, evidence: Path, output: Path, preseason_callers: Path | None = None, tactical_evidence: Path | None = None):
    directories = json.loads(directory.read_text(encoding="utf-8"))["teams"]
    records = json.loads(evidence.read_text(encoding="utf-8"))
    preseason = json.loads(preseason_callers.read_text(encoding="utf-8")) if preseason_callers else None
    tactical = json.loads(tactical_evidence.read_text(encoding="utf-8")) if tactical_evidence else None
    reviewed_staff = json.loads(Path(__file__).with_name("coaching_staff_review_20261103.json").read_text(encoding="utf-8"))
    teams = {}
    for item in directories:
        team = item["team_id"]
        staff = {c["role"]: {"name": c["name"].replace("\ufffd", "'"), "title": c["title"],
                              "status": "INTERIM" if "interim" in c["title"].lower() else "CURRENT_DIRECTORY",
                              "sources": [item["directory_source"]]} for c in item["staff"]}
        if "defensive_coordinator" not in staff:
            staff["defensive_coordinator"] = {"name": None, "title": None, "status": "NOT_SEPARATELY_LISTED",
                                                "sources": [item["directory_source"]]}
        current_roles = {role: {"name": record["name"], "title": record["title"]} for role, record in staff.items()}
        if current_roles != reviewed_staff[team]:
            raise ValueError(f"{team}: staff changed; re-review coaching and schemes before carrying them forward")
        staff["head_coach"]["new_head_coach_2026"] = team in NEW_HEADS
        head_bio = next(c for c in item["staff"] if c["role"] == "head_coach").get("bio_source")
        if head_bio:
            staff["head_coach"]["sources"].append(head_bio)

        def source(key):
            if isinstance(key, int):
                result = records[key]
                if "error" in result:
                    raise ValueError("reviewed source unavailable")
                if not result["reference"].startswith(f"https://www.{DOMAINS[team]}.com/"):
                    raise ValueError("source does not match reviewed team")
                return result
            role = key.split(":",1)[1]
            coach = next(c for c in item["staff"] if c["role"] == role)
            return coach.get("bio_source", item["directory_source"])

        def scheme(key, description):
            historical_keys = {15, 101, 156, 164, 216, 58}
            game_keys = {22, 24, 25, 45, 52, 59, 66, 73, 90, 92, 99, 163, 226, 258}
            scope = "CURRENT_ROLE" if isinstance(key, str) else "HISTORICAL_BACKGROUND" if key in historical_keys else "CURRENT_SEASON_OBSERVATION" if key in game_keys else "REPORTED_2026_PLAN"
            return {"description": description, "scope": scope,
                    "sources": [source(key)], "measured_usage_rates": None}

        oi, od, di, dd = DESCRIPTIONS[team]
        play_callers = {side: {"name": name, "status": "CONFIRMED" if name else "UNCONFIRMED", "sources": [source(key)]}
                       for side, (name, key) in zip(("offense", "defense"), CALLERS[team])}
        if preseason and play_callers["offense"]["status"] == "UNCONFIRMED":
            play_callers["offense"] = {"name": preseason["offensive_callers"][team],
                                       "status": "DATED_PRESEASON_REPORT", "report_date": "2026-09-09",
                                       "sources": [preseason["source"]],
                                       "warning": "Preseason assignment; not independently reconfirmed as current."}
        management_key, management_description = MANAGEMENT.get(team, ("bio:head_coach",
            "Current leadership and career background are documented; specific clock, fourth-down and delegation tendencies need additional current evidence."))
        teams[team] = {"team_id": team, "staff": staff, "play_callers": play_callers,
                       "directory_verified_at": item["directory_source"]["observed_at"],
                       "head_coach_management": {"description": management_description, "sources": [source(management_key)]},
                       "offense": scheme(oi,od), "defense": scheme(di,dd), "changes": [],
                       "era_start_dates": {"offense": None, "defense": None},
                       "quantitative_tendencies": {key: None for key in ("neutral_pass_rate", "tempo", "motion_rate", "play_action_rate", "coverage_mix", "blitz_rate", "fourth_down_decisions")},
                       "reported_rates": [],
                       "warning": "Source descriptions and reported plans are not verified snap frequencies or calibrated probability adjustments."}
        for metric, value, window, key, definition in {
            "NE": [("motion_rate", .753, "2026 Week 4 versus Buffalo", 226, "Article-reported motion frequency for this game"),
                   ("play_action_rate", .357, "2026 Week 4 versus Buffalo", 226, "Article-reported play-action frequency for this game")],
            "LAC": [("blitz_rate", .261, "2026 first six games in bye-week report", 24, "Article-reported blitz frequency; charting definitions require reconciliation")],
            "MIN": [("blitz_rate", .699, "Report published October 29, 2026", 163, "Team article relaying ESPN analysis; charting definitions require reconciliation")],
            "LV": [("four_man_rush_pressure_rate", .433, "2026 regular-season debut only", 32, "Pressures on four-man rushes in the reported debut game")],
        }.get(team, []):
            teams[team]["reported_rates"].append({"metric": metric, "value": value, "window": window,
                "definition": definition, "status": "DATED_REPORTED_RATE_NOT_CALIBRATED_INPUT", "sources": [source(key)]})
        if team == "DEN":
            teams[team]["era_start_dates"]["offense"] = "2026-10-08"
            teams[team]["changes"].append({"date": "2026-10-08", "date_precision": "announcement_date",
                "description": "Payton announced he would replace Webb as offensive caller for Week 5; Webb remains offensive coordinator.", "sources": [source(25), source(33)]})
        if team == "CAR":
            teams[team]["era_start_dates"]["offense"] = "2026-02-24"
            teams[team]["changes"].append({"date": "2026-02-24", "date_precision": "announcement_date",
                "description": "Canales delegated offensive calls to Brad Idzik while retaining head-coach oversight and timeout responsibilities.", "sources": [source(261), source(262)]})
            teams[team]["era_start_dates"]["defense"] = "2026-10-30"
            teams[team]["changes"].append({"date": "2026-10-30", "date_precision": "announcement_date",
                "description": "Todd Wash replaced Ejiro Evero as interim defensive coordinator and caller; post-change scheme evidence remains limited.", "sources": [source(60), source(66)]})
        teams[team]["era_id"] = content_hash({"staff": {role: record["name"] for role, record in staff.items()},
                                               "callers": {side: record["name"] for side, record in play_callers.items()},
                                               "changes": [{k: c[k] for k in ("date", "description")} for c in teams[team]["changes"]]})
        if tactical and team in TACTICAL_FOUNDATIONS:
            side,index,owner,scope,description = TACTICAL_FOUNDATIONS[team]
            fact = tactical[index]
            if fact["team"] != team or "error" in fact:
                raise ValueError("tactical source mismatch")
            teams[team][side]["foundation"] = {"coach": owner, "scope": scope, "description": description,
                                                "sources": [fact], "automatic_probability_multiplier": None}
    artifact = {"kind": "REVIEWED_CURRENT_COACHING_CONTEXT", "as_of_label": "2026-11-03", "teams": teams,
                "historical_pregame_version_verified": False}
    with output.open("x", encoding="utf-8") as handle:
        json.dump(artifact, handle, indent=2)
    print(json.dumps({"teams": len(teams), "confirmed_callers": sum(c["status"] == "CONFIRMED" for t in teams.values() for c in t["play_callers"].values()),
                      "caller_slots": 64, "output": str(output)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for key in ("directory", "evidence", "output"):
        parser.add_argument("--"+key, type=Path, required=True)
    parser.add_argument("--preseason-callers", type=Path)
    parser.add_argument("--tactical-evidence", type=Path)
    args = parser.parse_args()
    run(args.directory,args.evidence,args.output,args.preseason_callers,args.tactical_evidence)
