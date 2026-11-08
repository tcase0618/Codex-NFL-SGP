"""Position-specific research metric definitions. Missing observations are never zero."""
import re

VIEWS = [
    ("season", "Season"), ("last3", "Last 3 games"), ("last5", "Last 5 games"),
    ("career", "Available career"), ("blitz", "Versus blitz"), ("pressure", "Under pressure"),
    ("clean", "Clean pocket"), ("man", "Man coverage"), ("zone", "Zone coverage"),
    ("home", "Home"), ("away", "Away"), ("first_half", "First half"),
    ("second_half", "Second half"), ("third_down", "Third down"),
    ("opponent_adjusted", "Opponent adjusted"), ("percentile", "League percentile"),
    ("yoy", "Year over year"), ("confidence", "Sample & confidence"),
]

LIBRARY = {
"QB": {
"Passing characteristics": "Average throw depth|Time to throw|Deep shot %|Behind-line throw %|Short throw %|Intermediate throw %|Deep throw %|Air yards / attempt|Throw velocity|Passes beyond first read",
"Accuracy & placement": "Completion %|Adjusted completion %|CPOE|Short accuracy|Intermediate accuracy|Deep accuracy|Tight-window accuracy|On-target throw %|Catchable pass %|Accuracy under pressure",
"Pressure & pocket": "Pressured dropback %|Pressure grade|Pressure-to-sack %|Sack rate|Time to pressure|Clean pocket grade|Blitz grade|Non-blitz grade|Throwaway rate|Pocket escape rate",
"Decisions & risk": "Turnover-worthy play %|Big-time throw %|Interception rate|Expected interception rate|First-read throw %|Checkdown rate|Aggressiveness %|Avoidable sack rate|Late throw %|Decision grade",
"Efficiency & production": "EPA / play|EPA / dropback|Passing success rate|Yards / attempt|Adjusted net yards / attempt|Passing grade|Deep passing grade|Touchdown rate|Explosive pass rate|Third-down EPA",
"Rushing & mobility": "Scramble rate|Designed runs|Rushing grade|Rushing EPA / attempt|Scramble yards / attempt|Missed tackles forced|Rushing success rate|QB rushing TD rate",
"Situation & scheme": "Play-action EPA|Non-play-action EPA|Man coverage EPA|Zone coverage EPA|Red-zone EPA|Two-minute EPA|Fourth-quarter close-game EPA"},
"RB": {
"Production & efficiency": "Carries|Rush yards|Yards / carry|Rushing EPA / play|Rushing success rate|RYOE / attempt|Explosive run %|Stuffed run %|First-down run %|Touchdown rate",
"Vision & contact": "Yards before contact|Yards after contact|Missed tackles forced|Missed tackle rate|Broken tackle %|10+ yard run %|20+ yard run %|Time to line of scrimmage|Rush yards over expected|Top carry speed",
"Style & scheme": "Inside zone efficiency|Outside zone efficiency|Gap scheme efficiency|Power run efficiency|Box defender average|Light-box efficiency|Stacked-box efficiency|Short-yardage conversion %|Goal-line carry share|Run direction splits",
"Receiving & protection": "Routes per dropback|Targets / route|Receptions|Receiving YPRR|YAC / reception|Receiving EPA / target|Drop %|Pass-block snaps|Pressure allowed %|Pass-protection grade"},
"WR": {
"Targets & production": "Targets|Receptions|Catch %|Target share|Targets / route run|Yards / route run|Receiving yards|Yards / target|EPA / target|Receiving success rate",
"Routes & separation": "Average separation|Separation above expected|Man coverage win %|Zone coverage win %|Press coverage win %|Slot route efficiency|Outside route efficiency|Short route separation|Intermediate separation|Deep route separation",
"Catch difficulty": "Contested targets|Contested catch %|Drop %|Catchable target %|Catch % above expected|Tight-window catch %|Deep catch %|Sideline catch %|Red-zone catch %|First-down conversion %",
"Explosiveness & YAC": "YAC / reception|YAC above expected|Missed tackles forced|Explosive reception %|20+ yard reception %|Average depth of target|Air yards share|Deep target share|Average ball-carrier speed|Touchdown / target",
"Deployment & blocking": "Route participation|Slot alignment %|Wide alignment %|Run-block grade|Blocking assignment success %"},
"TE": {
"Receiving": "Targets|Targets / route|Yards / route|Receiving EPA|Catch %|Contested catch %|YAC / reception|YAC over expected|Average separation|Man coverage efficiency|Zone coverage efficiency|Red-zone targets",
"Blocking": "Pass-block grade|Run-block grade|Pass-block win %|Run-block win %|Pressures allowed|Sacks allowed|Gap-block efficiency|Zone-block efficiency|Second-level block success|Double-team efficiency|Missed assignments|Blocking penalties",
"Deployment": "Inline snap %|Slot snap %|Wide snap %|Backfield snap %|Routes / dropback|Blocking snap share|Chip-block rate|Play-action usage|Motion usage|Goal-line snaps|Red-zone route share"},
"EDGE": {
"Pass rush": "Pass-rush snaps|Pass-rush win %|Pressure rate|Sack rate|Sacks|QB hits|QB hurries|Quick pressure rate|Time to pressure|Pass-rush productivity|One-on-one win %|Double-team rate|Chip-block rate|Sack conversion %|Forced fumble rate",
"Run defense": "Run-defense snaps|Run-stop rate|Tackle for loss rate|Run-block win %|Missed tackle %|Edge containment success|Outside run defense|Inside run defense|Read-option discipline|Run EPA allowed / snap",
"Technique & matchups": "Speed-rush win %|Power-rush win %|Inside-counter win %|Bull-rush win %|Left-side efficiency|Right-side efficiency|Wide-alignment efficiency|Stand-up rush efficiency|Elite tackle matchup win %|Pass-rush penalty rate"},
"DI": {
"Pass rush": "Pass-rush win %|Pressure rate|Quick pressure %|Time to pressure|Sacks|Hits|Hurries|Bull-rush win %|One-on-one win %|Double-team rate|Interior disruption EPA|QB disruption rate",
"Run defense": "Run-stop rate|Run defense success|Tackle for loss rate|Stuffed-run rate|Average tackle depth|Missed tackle %|Double-team resistance|Gap control win %|Inside run efficiency|Outside run efficiency|Short-yardage stops|Goal-line stop rate|Run EPA allowed / snap",
"Assignment & technique": "Zero-tech efficiency|One-tech efficiency|Three-tech efficiency|Four-tech efficiency|A-gap penetration|B-gap penetration|Two-gap execution|Single-block efficiency|Double-team win %|Snap alignment versatility"},
"LB": {
"Run defense": "Run-stop rate|Run success allowed|Solo tackles|Assisted tackles|Tackle for loss %|Missed tackle %|Average tackle depth|Gap fill win %|Inside run stop %|Outside run stop %|Pursuit efficiency|Short-yardage stop %|Goal-line stop %|Explosive run prevention",
"Coverage": "Coverage snaps|Targets allowed|Catch % allowed|Yards / coverage snap|Coverage EPA allowed|Passer rating allowed|Man coverage snaps|Zone coverage snaps|TE coverage efficiency|RB coverage efficiency|Slot coverage efficiency|Separation allowed|YAC allowed|Coverage stops|Pass breakups|Coverage assignment errors",
"Pressure & awareness": "Blitz snaps|Blitz pressure %|Blitz sacks|Time to pressure|QB hits|Gap assignment success|Play-action recognition|Screen recognition|Pre-snap alignment errors|Defensive penalties"},
"CB": {
"Overall coverage": "Coverage snaps|Targets allowed|Target rate|Receptions allowed|Catch % allowed|Yards allowed|Yards / coverage snap|Coverage EPA allowed|Coverage success rate|Passer rating allowed|First downs allowed|Touchdowns allowed|Explosive passes allowed|Interceptions|Pass breakups",
"Coverage technique": "Man coverage win %|Zone coverage win %|Press coverage win %|Off-man win %|Average separation allowed|Separation above expected allowed|Deep coverage efficiency|Intermediate coverage efficiency|Short coverage efficiency|Slot coverage efficiency",
"Matchup difficulty": "Average WR quality faced|WR1 matchup snaps|Slot matchup snaps|Speed receiver performance|Physical receiver performance|Route difficulty faced|Coverage help frequency|Targets per man snap|Targets per zone snap|Red-zone coverage efficiency",
"Tackling & discipline": "Solo tackles|Missed tackle %|Run-stop rate|YAC allowed / reception|Pass interference rate|Holding penalty rate|Tackle depth|Screen coverage efficiency|Open-field tackle success|Explosive YAC allowed"},
"S": {
"Coverage": "Coverage snaps|Coverage EPA allowed|Yards allowed|Catch % allowed|Passer rating allowed|Deep completion % allowed|Explosive pass rate allowed|Interceptions|Pass breakups|Man coverage success|Zone coverage success|Separation allowed|Deep closing speed|TE coverage success|Coverage bust rate",
"Run defense": "Run-defense snaps|Run-stop rate|Missed tackle %|Solo tackles|Assisted tackles|Average tackle depth|Short-yardage stops|Run EPA allowed|Gap-fill success|Explosive run prevention",
"Deployment": "Single-high snap %|Two-high snap %|Box snap %|Slot snap %|Deep zone usage|Man coverage usage|Blitz rate|QB pressure rate|Coverage disguise rate|Assignment versatility"},
}
OL = {
"Pass protection": "Pass-block snaps|Pass-block win %|Pressures allowed|Pressure rate allowed|Sacks allowed|QB hits allowed|Hurries allowed|True pass-set win %|One-on-one win %|Double-team efficiency|Time to pressure allowed|Speed-rush win %|Power-rush win %|Stunt pickup success|Pass-block penalties",
"Run blocking": "Run-block snaps|Run-block win %|Gap-block win %|Zone-block win %|Outside zone efficiency|Inside zone efficiency|Power run efficiency|Pull-block win %|Second-level block success|Double-team run success|Stuffed-run contribution|Successful rushing lanes created|Short-yardage blocking success|Red-zone blocking success|Run-block penalties",
}
# The supplied shared OL table contains 30 entries (15 per column).
for role, additions in {
"OT": "Outside-rush win rate|Inside-counter win rate|Bull-rush defense|Edge pressure allowed|Wide-alignment defense|Isolation snap share|Chip-help rate|Speed-rush loss rate|Long-developing pass protection|Elite EDGE matchup performance",
"OG": "Interior pressure allowed|Defensive tackle win rate|Bull-rush defense|A-gap pressure rate|B-gap pressure rate|Combo-block efficiency|Pulling efficiency|Screen-block efficiency|Stunt communication|Short-yardage push",
"C": "Snap accuracy|Snap timing|Nose-tackle win rate|A-gap protection|Blitz pickup|Stunt recognition|Double-team coordination|Protection-call errors|Run-block leverage|Interior short-yardage efficiency",
}.items():
    LIBRARY[role] = {**OL, "Role specialization": additions}

LIBRARY.update({
"K": {"Field goals": "Field-goal attempts|Field goals made|Field-goal %|Field-goal probability above expectation|Field goals 0–29 yards|Field goals 30–39 yards|Field goals 40–49 yards|Field goals 50–59 yards|Field goals 60+ yards|Longest field goal",
      "Conditions & execution": "Extra-point attempts|Extra points made|Extra-point %|Blocked kick rate|Missed kick direction|Outdoor field-goal %|Indoor field-goal %|Wind-adjusted accuracy|Kickoff touchback %|Clutch field-goal %"},
"P": {"Punt production": "Punts|Gross punt yards|Gross yards / punt|Net yards / punt|Punts inside 20|Punts inside 10|Touchback %|Hang time|Fair catch %|Return yards allowed / punt",
      "Placement & value": "Punt field-position value|Punt EPA|Directional accuracy|Coffin-corner success|Blocked punt rate|Operation time|Outkicked coverage %|Distance over expected|Pressure punt efficiency|Bad-weather efficiency"},
"LS": {"Snap execution": "Long snaps|Punt snaps|Field-goal snaps|Snap accuracy|Snap timing|Snap velocity|Off-target snap %|Low snap %|High snap %|Snap consistency",
       "Protection & coverage": "Blocking assignment success|Pressures allowed|Blocked-kick contribution|Punt coverage tackles|Missed tackles|Coverage penalties|Holding penalties|False starts|Operation time|Bad-weather snap accuracy"},
"KR": {"Return production": "Kick returns|Kick-return yards|Yards / kick return|Return yards over expected|Return EPA|Return success %|Explosive return %|20+ yard returns|40+ yard returns|Kick-return touchdowns",
       "Ball security & decisions": "Return fumbles|Return fumbles lost|Ball security rate|Fair catches|Touchback decisions|Decision value over expected|Missed tackles forced|Yards after contact|Top return speed|Blocking-lane efficiency"},
"PR": {"Return production": "Punt returns|Punt-return yards|Yards / punt return|Return yards over expected|Return EPA|Return success %|Explosive return %|10+ yard returns|20+ yard returns|Punt-return touchdowns",
       "Ball security & decisions": "Return fumbles|Return fumbles lost|Ball security rate|Fair catches|Muffed punts|Catch decision success|Missed tackles forced|Yards after contact|Top return speed|Coverage pressure performance"},
"FB": {"Blocking & assignment": "Run-block snaps|Run-block grade|Lead-block success %|Gap-block efficiency|Zone-block efficiency|Short-yardage blocking success|Goal-line blocking success|Pass-block snaps|Pass-protection grade|Assignment-adjusted blocking success",
       "Usage & production": "Carries|Rush yards|Yards / carry|Rushing success rate|Goal-line carry share|Targets|Receptions|Receiving yards|Yards / route run|Special-teams snaps"},
})


def slug(label):
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def unit(label):
    if label == "Touchdown / target": return "%"
    if label == "CPOE" or "above expected" in label.lower() and "%" in label: return "pp"
    if "%" in label or any(word in label.lower() for word in ("rate", "share", "accuracy", "probability", "conversion")):
        return "%"
    if "epa" in label.lower(): return "EPA"
    if "time" in label.lower() or "timing" in label.lower(): return "s"
    if "speed" in label.lower() or "velocity" in label.lower(): return "mph"
    if any(word in label.lower() for word in ("yard", "depth", "separation", "yac", "ryoe")): return "yd"
    if "grade" in label.lower(): return "grade"
    return "count / index"


def requirement(label):
    text = label.lower()
    if any(x in text for x in ("expected", "adjusted", "quality faced", "difficulty", "disruption epa", "decision value")):
        return "model_or_provider"
    if any(x in text for x in ("grade", "assignment", "first read", "first-read", "recognition", "win %", "win rate", "communication", "discipline", "gap control", "read-option", "block", "route", "pressure", "blitz", "coverage", "contact", "tackle", "tech", "chip", "alignment", "separation", "velocity", "speed", "throw", "snap", "man ", "zone ", "screen", "pull", "counter", "rush efficiency", "power", "bull", "gap", "pursuit", "late ", "catchable", "contested", "tight-window", "sideline", "on-target", "avoidable", "checkdown", "turnover-worthy", "big-time")):
        return "tracking_or_charting"
    return "public_stats_or_play_by_play"


def contract(label):
    text=label.lower()
    context={"passing_depth":"behind LOS <0; short 0–9; intermediate 10–19; deep 20+ air yards",
             "red_zone":"opponent 20-yard line or closer", "goal_line":"opponent 5-yard line or closer",
             "short_yardage":"yards to gain <=2", "two_minute":"final 120 seconds of either half",
             "close_game":"absolute score margin <=8", "explosive_pass":"20+ gained yards",
             "explosive_run":"10+ gained yards", "quick_pressure":"pressure within 2.5 seconds of snap"}
    if "grade" in text:
        definition="Provider-authored charting grade, retained in its original scale and version; not reconstructed from box-score production."
        denominator="Provider-qualified graded snaps"
    elif any(word in text for word in ("expected", "adjusted", "decision grade", "quality faced", "difficulty")):
        definition="Observed production relative to an explicitly identified, versioned expectation or adjustment model. The model, eligible sample and calibration must accompany any value."
        denominator="Model-qualified opportunities; provider or model contract required"
    elif "%" in text or "rate" in text or "share" in text or "accuracy" in text:
        definition=f"100 × qualifying {label.rstrip('%').strip()} events divided by eligible opportunities. Event attribution and denominator must be provided by the source adapter; different definitions must not be mixed."
        denominator="Eligible attempts/snaps/routes/targets for this event; adapter must specify"
    elif " / " in label:
        numerator,denominator=label.split(" / ",1)
        definition=f"Sum of observed {numerator.lower()} divided by observed {denominator.lower()}, on the same qualified play set."
    elif "average" in text or "time to" in text or "timing" in text or "speed" in text:
        definition=f"Opportunity-weighted mean of observed {label.lower()}, with measurement timestamps, units and eligible-event count retained. Maximums must be separately labeled."
        denominator="Number of qualifying measured events"
    elif any(word in text for word in ("efficiency", "success", "recognition", "discipline", "versatility", "contribution", "value", "productivity", "resistance", "control", "prevention", "leverage", "push", "performance", "defense", "coordination")):
        definition=f"Charted or modeled {label.lower()} over a provider-defined eligible sample. Requires a reproducible attribution rubric; no overall grade or inferred proxy is substituted."
        denominator="Provider-qualified attributed opportunities"
    else:
        definition=f"Total observed {label.lower()} in the selected window, attributed to this stable player ID. Count/fractional-credit rules must match the provider."
        denominator="Not applicable for additive totals"
    return {"definition":definition,"denominator_contract":denominator,"context_contract":context,
            "definition_status":"draft_requires_adapter_validation","opponent_adjustment":"Unavailable without a versioned validated model",
            "confidence_method":"Observed sample counts only; no inferred confidence interval"}

def catalog():
    result = {}
    for position, categories in LIBRARY.items():
        metrics = []
        for category, labels in categories.items():
            for label in labels.split("|"):
                metrics.append({**contract(label), "id": f"{position.lower()}.{slug(label)}", "key": slug(label), "label": label,
                                "category": category, "position": position, "unit": unit(label),
                                "requirement": requirement(label), "views": [key for key, _ in VIEWS],
                                "historical_target_start": 2006, "minimum_sample": None,
                                "minimum_sample_status": "not_validated", "coverage": "source_dependent"})
        if len({metric["id"] for metric in metrics}) != len(metrics):
            raise ValueError(f"duplicate metric definitions: {position}")
        result[position] = metrics
    return result


CATALOG = catalog()


def libraries_for_position(position):
    aliases={"DE":["EDGE"],"DT":["DI"],"NT":["DI"],"DL":["EDGE","DI"],"OLB":["EDGE","LB"],
             "ILB":["LB"],"MLB":["LB"],"DB":["CB","S"],"FS":["S"],"SS":["S"],"SAF":["S"],
             "T":["OT"],"LT":["OT"],"RT":["OT"],"G":["OG"],"LG":["OG"],"RG":["OG"],
             "OL":["OT","OG","C"],"HB":["RB"]}
    return [position] if position in CATALOG else aliases.get(position,[])

# Versioned public play-event adapters. Each metric keeps its own measured
# denominator; missing event fields are never counted as failures.
PLAY_METRICS = {}

def _play_spec(roles, labels, event, field, operation='mean', threshold=None, context=None):
    for role in roles.split():
        for label in labels.split('|'):
            mid = role.lower() + '.' + slug(label)
            PLAY_METRICS[mid] = dict(event=event, field=field, operation=operation,
                                    threshold=threshold, context=context,
                                    scale=100 if unit(label)=='%' else 1)

_play_spec('QB', 'EPA / play', 'offense', 'epa')
_play_spec('QB', 'EPA / dropback', 'dropback', 'epa')
_play_spec('QB', 'Passing success rate', 'pass', 'success')
_play_spec('QB', 'Explosive pass rate', 'pass', 'yards_gained', 'ge', 20)
_play_spec('QB', 'Third-down EPA', 'dropback', 'epa', context='third_down')
_play_spec('QB', 'Red-zone EPA', 'dropback', 'epa', context='red_zone')
_play_spec('QB', 'Two-minute EPA', 'dropback', 'epa', context='two_minute')
_play_spec('QB', 'Fourth-quarter close-game EPA', 'dropback', 'epa', context='fourth_close')
_play_spec('QB', 'Designed runs', 'designed_run', None, 'count')
_play_spec('QB', 'Scramble rate', 'dropback', 'qb_scramble')
_play_spec('QB', 'Scramble yards / attempt', 'scramble', 'rushing_yards')
_play_spec('QB RB FB', 'Rushing success rate', 'rush', 'success')
_play_spec('QB', 'Rushing EPA / attempt', 'rush', 'epa')
_play_spec('RB', 'Rushing EPA / play', 'rush', 'epa')
_play_spec('RB', 'Explosive run %|10+ yard run %', 'rush', 'rushing_yards', 'ge', 10)
_play_spec('RB', '20+ yard run %', 'rush', 'rushing_yards', 'ge', 20)
_play_spec('RB', 'Stuffed run %', 'rush', 'rushing_yards', 'le', 0)
_play_spec('RB', 'Short-yardage conversion %', 'rush', 'first_down_rush', context='short_yardage')
_play_spec('RB', 'First-down run %', 'rush', 'first_down_rush')
_play_spec('WR', 'EPA / target', 'target', 'epa')
_play_spec('RB', 'Receiving EPA / target', 'target', 'epa')
_play_spec('TE', 'Receiving EPA', 'target', 'epa', 'sum')
_play_spec('WR', 'Receiving success rate', 'target', 'success')
_play_spec('WR', 'Explosive reception %|20+ yard reception %', 'catch', 'receiving_yards', 'ge', 20)
_play_spec('WR', 'Deep target share', 'target', 'air_yards', 'ge', 20)
_play_spec('WR', 'Deep catch %', 'deep_target', 'complete_pass')
_play_spec('WR', 'Red-zone catch %', 'target', 'complete_pass', context='red_zone')
_play_spec('TE', 'Red-zone targets', 'target', None, 'count', context='red_zone')
_play_spec('WR', 'Touchdown / target', 'target', 'pass_touchdown')
_play_spec('QB', 'Touchdown rate', 'pass', 'pass_touchdown')
_play_spec('RB', 'Touchdown rate', 'rush', 'rush_touchdown')
_play_spec('QB', 'QB rushing TD rate', 'rush', 'rush_touchdown')
_play_spec('WR', 'First-down conversion %', 'catch', 'first_down_pass')

PLAY_CONTEXTS = {
    'third_down': 'down = 3', 'red_zone': 'yardline_100 <= 20',
    'two_minute': '0 <= half_seconds_remaining <= 120',
    'fourth_close': 'qtr = 4 and abs(score_differential) <= 8',
    'short_yardage': '0 <= ydstogo <= 2',
}
PLAY_EVENTS = {
    'offense': 'union of explicitly GSIS-attributed passer dropbacks and rusher attempts, one player/play',
    'dropback': 'qb_dropback = 1, passer GSIS ID; scramble rusher GSIS ID when qb_scramble = 1',
    'pass': 'pass_attempt = 1 and sack = 0, attributed passer GSIS ID',
    'rush': 'rush_attempt = 1, attributed rusher GSIS ID',
    'designed_run': 'rush_attempt = 1 and qb_scramble = 0, attributed rusher GSIS ID',
    'scramble': 'qb_scramble = 1 and rush_attempt = 1, attributed rusher GSIS ID',
    'target': 'pass_attempt = 1 and sack = 0, attributed receiver GSIS ID',
    'catch': 'target with complete_pass = 1',
    'deep_target': 'target with air_yards >= 20',
}

def play_definition(metric_id):
    spec = PLAY_METRICS[metric_id]
    op, field = spec['operation'], spec['field']
    expression = ('1' if op == 'count' else
                  f"{field} {'>=' if op == 'ge' else '<='} {spec['threshold']}" if op in ('ge', 'le') else field)
    return {
        'revision': 'GSIS_PLAYER_EVENTS_V1', 'metric_id': metric_id,
        'event': PLAY_EVENTS[spec['event']],
        'context': PLAY_CONTEXTS.get(spec['context'], 'selected view'),
        'formula': f"sum({expression})" + ('' if op in ('sum', 'count') else f" / measured eligible events * {spec['scale']}"),
        'exclusions': 'no_play, special teams, two-point attempts, kneels and spikes; finite required measurements only',
        'attribution': 'Stable GSIS IDs only; target EPA is full team play EPA on targeted plays, not isolated receiver contribution.',
        'missingness': 'No eligible measured events -> null. Measured zero remains zero. No full-participation or availability claim.',
        'source_definition': 'https://nflfastr.com/articles/field_descriptions.html',
    }
