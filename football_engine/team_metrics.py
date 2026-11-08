"""The user's team metric slots, with explicit directions and source requirements."""
import re

TEAM_VIEWS=[('season','Full season'),('last3','Last 3 games'),('last5','Last 5 games'),('last8','Last 8 games'),
 ('home','Home'),('away','Away'),('first_half','First half'),('second_half','Second half'),('leading','Leading'),
 ('trailing','Trailing'),('tied','Tied'),('top10','Against top-10 opponents'),('bottom10','Against bottom-10 opponents'),
 ('man','Versus man'),('zone','Versus zone'),('blitz','Versus blitz'),('four_man','Against four-man rush'),
 ('short_rest','Short rest'),('long_rest','Long rest'),('opponent_adjusted','Opponent adjusted'),
 ('percentile','League percentile'),('three_year','3-year trend'),('yoy','Year-over-year change'),('confidence','Confidence interval')]

LIBRARY={
'offense':('Offensive efficiency','Points, EPA & sustainability',
'Points per game|Offensive points / game|Points / drive|Yards / game|Yards / play|First downs / game|Touchdowns / drive|Three-and-out rate|EPA / offensive play|EPA / drive|Offensive success rate|Positive EPA play %|Negative EPA play %|Early-down EPA|Explosive play rate|Play-to-play EPA volatility|Pass EPA / play|Rush EPA / play|Pass success rate|Rush success rate|Points above expected / drive|Opponent-adjusted offensive EPA'),
'passing':('Passing offense','Accuracy, protection & depth',
'Passing yards / game|Passing yards / attempt|Net yards / dropback|Passing touchdowns|Passing TD rate|Completion %|Adjusted completion %|Team CPOE|Passing EPA / play|Passing success rate|Average depth of target|Deep shot rate|Short pass frequency|Intermediate pass frequency|Explosive completion rate|Air yards / attempt|YAC / completion|YAC above expected|Turnover-worthy throw rate|Big-time throw rate|Interception rate|Sack rate|Pressure rate allowed|Pressure-to-sack rate|Average time to throw'),
'rushing':('Rushing offense','Concepts, gaps & creation',
'Rushing yards / game|Yards / carry|Rush EPA / attempt|Rush success rate|Rushing first-down rate|Rushing TD rate|Stuffed run rate|Explosive rush rate|Yards before contact|Yards after contact|RYOE / attempt|Run-block win rate|Missed tackles forced / rush|Average box defenders|Light-box efficiency|Loaded-box efficiency|Inside zone frequency|Inside zone EPA|Outside zone frequency|Outside zone EPA|Gap/power frequency|Gap/power EPA|QB designed-run rate|QB designed-run EPA'),
'line':('Offensive line','Protection & line continuity',
'Pass-block win rate|Pressures allowed / dropback|Sack rate allowed|Time to pressure allowed|Interior pressure allowed|Edge pressure allowed|Blitz pickup success|Stunt pickup success|Unblocked pressure rate|Starting-five continuity|Run-block win rate|Rush success behind line|Yards before contact|Stuffed run rate|Inside run efficiency|Outside run efficiency|Gap-scheme efficiency|Zone-scheme efficiency|Short-yardage conversion|Starting-five run-block continuity'),
'receiving':('Receiving & YAC','Targets, routes & separation',
'Yards per route run|Targets per route|Catch rate|Catch rate over expected|Drop rate|Contested catch rate|YAC per reception|YAC over expected|Explosive reception rate|Receiving EPA/target|Team average separation|Separation over expected|Man coverage efficiency|Zone coverage efficiency|Press coverage efficiency|Slot receiving efficiency|Outside receiving efficiency|Tight-end target share|Running-back target share|Target concentration index'),
'scheme':('Offensive scheme','Personnel, motion & calls',
'11 personnel usage %|12 personnel usage %|21 personnel usage %|13 personnel usage %|Shotgun formation %|Under-center formation %|Empty backfield %|Pistol formation %|Motion at snap %|Pre-snap motion %|Play-action frequency|RPO frequency|No-huddle frequency|Condensed formation usage|Spread formation usage|Formation predictability|11 personnel EPA|12 personnel EPA|21 personnel EPA|Play-action EPA|RPO EPA|Motion-play EPA|Empty formation EPA|Shotgun vs under-center EPA'),
'defense':('Defensive efficiency','EPA allowed, stops & takeaways',
'Points allowed / game|Defensive points allowed / drive|Yards allowed / game|Yards allowed / play|First downs allowed / game|Touchdowns allowed / drive|Three-and-out forced %|Opposing drive scoring rate|EPA allowed / play|EPA allowed / drive|Defensive success rate|Opponent passing EPA|Opponent rushing EPA|Explosive plays allowed %|Negative EPA plays forced %|Opponent-adjusted defensive EPA|Takeaways / game|Forced fumble rate|Interception rate forced|Defensive touchdown rate|Tackles for loss / game|Defensive stop rate'),
'pass_defense':('Pass defense','Coverage & efficiency allowed',
'Passing yards allowed / game|Net passing yards / dropback allowed|Passing EPA allowed|Passing success rate allowed|Completion % allowed|CPOE allowed|Passing TD rate allowed|First-down pass rate allowed|Average separation allowed|Separation vs expected allowed|Catch rate over expected allowed|Deep completion % allowed|Short passing EPA allowed|Intermediate passing EPA allowed|Deep passing EPA allowed|Explosive pass rate allowed|Interception rate forced|Pass breakups / attempt|Sack rate generated|Pressure rate generated|Coverage sack rate|YAC allowed / completion|YAC above expected allowed|Third-down pass EPA allowed'),
'run_defense':('Run defense','Gap integrity & tackling',
'Rush yards allowed / game|Yards per carry allowed|Rush EPA allowed / play|Rush success rate allowed|Rushing TD rate allowed|Explosive rush rate allowed|Rushing first-down rate allowed|Stuffed run rate generated|Yards before contact allowed|Yards after contact allowed|RYOE allowed / attempt|Missed tackle rate|Run-stop win rate|Defensive line penetration rate|Average run tackle depth|Tackles for loss / rush|Inside zone EPA allowed|Outside zone EPA allowed|Gap/power EPA allowed|QB designed-run EPA allowed|Short-yardage stop rate|Goal-line run stop rate'),
'pass_rush':('Pass rush','Pressure, blitzing & disruption',
'Team pass-rush win %|Sacks / game|Sack rate|QB hits / game|QB hurries / game|Pass-rush productivity|Individual isolation win %|Double-team rate faced|Chip-block rate faced|Pressure-to-sack conversion|Forced holding rate|Sack EPA generated|Pressure rate|Quick pressure rate|Time to pressure|Four-man pressure rate|Blitz pressure rate|Unblocked pressure rate|Interior pressure rate|Edge pressure rate|QB scramble forced rate|Opponent throwaway rate'),
'coverage':('Coverage schemes','Man, zone, shells & disguise',
'Man coverage frequency|Zone coverage frequency|Cover 0 usage|Cover 1 usage|Cover 2 usage|Cover 3 usage|Cover 4 usage|Cover 6 usage|Man coverage EPA allowed|Zone coverage EPA allowed|Cover 1 EPA allowed|Cover 2 EPA allowed|Cover 3 EPA allowed|Cover 4 EPA allowed|Single-high EPA allowed|Two-high EPA allowed|Pre-to-post-snap shell change %|Disguised coverage rate|Coverage bust rate|Blitz frequency|Simulated pressure frequency|Nickel personnel usage|Dime personnel usage|Coverage predictability'),
'situational':('Situational football','Downs, red zone & game state',
'1st-down offensive EPA|2nd-down offensive EPA|3rd-down conversion %|4th-down conversion %|3rd-and-short conversion|3rd-and-medium conversion|3rd-and-long conversion|4th-and-short conversion|Early-down pass rate|Early-down success rate|Red-zone TD %|Red-zone points / trip|Red-zone rush EPA|Red-zone pass EPA|Goal-to-go TD %|Goal-line run success|Goal-line pass success|Red-zone defensive EPA|Red-zone stops forced|Goal-to-go defensive stops|First-half EPA|Second-half EPA|First-quarter EPA|Fourth-quarter EPA|Two-minute offense EPA|Two-minute defense EPA|One-score game EPA|Leading-state EPA|Trailing-state EPA|Final-possession EPA'),
'special':('Special teams','Kicking, returns & field position',
'FG success above expected|FG success 40–49 yards|FG success 50+ yards|Extra-point success|Average punt distance|Net punt distance|Punt hang time|Punts inside the 20|Punt value above expected|Kickoff value above expected|Kick return yards above expected|Punt return yards above expected|Kickoff touchback rate|Punt touchback rate|Average return starting field position|Opponent return yards allowed|Return fumble rate|Special teams penalties|Special teams EPA / game|Special teams field-position advantage'),
'coaching':('Coaching & decisions','Pace, tendencies & management',
'Fourth-down go rate|Fourth-down decision EPA|Field-goal decision value|Punt decision value|Timeout efficiency|Two-minute clock efficiency|Challenge success rate|Timeouts remaining at key moments|Win probability added by decisions|Late-game decision consistency|Pass rate over expected|Neutral-situation pace|Early-down pass tendency|Play-action tendency|Motion tendency|Blitz tendency|Play-call predictability|Halftime adjustment effectiveness|Opponent-specific adaptation|Scheme-adjusted offensive EPA'),
'personnel':('Personnel & depth','Availability, roles & continuity',
'Projected starting QB rating|Aggregate OL rating|Aggregate receiving rating|Aggregate rushing rating|Defensive front rating|Linebacker unit rating|Cornerback unit rating|Safety unit rating|Backup quality index|Position-group balance|Projected starters available|Starter snaps missed|OL lineup continuity|Defensive line continuity|Secondary continuity|QB availability|Injury-adjusted roster rating|Replacement-level drop-off|Depth-chart stability|Expected lineup stability'),
'environment':('Environment & rest','Venue, weather & travel',
'Home offensive EPA|Away offensive EPA|Home defensive EPA|Away defensive EPA|Indoor EPA|Outdoor EPA|Wind-adjusted passing EPA|Cold-weather efficiency|Wet-weather rushing efficiency|Short-rest EPA|Long-rest EPA|Post-bye EPA|Travel-distance effect|Time-zone-change effect|Consecutive away-game effect|Opponent rest differential|Days since previous game|Altitude adjustment'),
'trends':('Consistency & trends','Rolling form, variance & regression',
'Last 3 games EPA|Last 5 games EPA|Last 8 games EPA|Season EPA trend slope|Passing EPA trend|Rushing EPA trend|Defensive EPA trend|Success rate trend|Explosive play trend|Pressure rate trend|Game-level EPA variance|Drive-level scoring variance|Turnover differential vs expected|Fumble recovery rate|One-score win rate|Actual minus expected wins|Actual minus projected points|High-leverage play dependence|Roster-adjusted trend|Schedule-adjusted trend'),
'matchup':('Opponent & matchups','Schedule strength & tactical fit',
'Opponent-adjusted offensive EPA|Opponent-adjusted defensive EPA|Past opponent offensive rating|Past opponent defensive rating|Future opponent offensive rating|Future opponent defensive rating|Strength of schedule rating|Strength of victory rating|Offense vs man coverage|Offense vs zone coverage|Offense vs single-high|Offense vs two-high|Offense vs blitz|Offense vs four-man rush|Rush offense vs light box|Rush offense vs stacked box|Defense vs mobile QB|Defense vs play action|Defense vs RPO|Defense vs outside zone|Defense vs gap/power|Defense vs deep passing|Defense vs slot targets|Defense vs receiving TE')}


def slug(value):return re.sub('[^a-z0-9]+','_',value.lower()).strip('_')


def direction(category,label):
    text=label.lower()
    if category=='scheme' and text=='shotgun vs under-center epa':return 'context'
    if category=='pass_rush' and text=='pressure-to-sack conversion':return 'higher'
    if text in ('punt touchback rate','time to pressure','time to pressure allowed'):return 'higher' if category=='line' else 'lower'
    if 'epa' in text and ('defensive' in text or 'defense' in text):return 'lower'
    if any(word in text for word in ('allowed','opponent passing epa','opponent rushing epa','opposing drive','negative epa play %','three-and-out rate','interception rate','sack rate','stuffed run rate','drop rate','missed tackle','turnover-worthy','pressure-to-sack','return fumble')):
        if any(word in text for word in ('forced','generated')) or category=='pass_rush' and label=='Sack rate':return 'higher'
        return 'lower'
    if category=='coverage' and 'epa' in text:return 'lower'
    if category in ('pass_defense','run_defense') and ('epa' in text or 'success rate' in text or 'completion' in text):return 'lower'
    if category=='defense' and ('epa' in text or 'points' in text or 'yards' in text or 'touchdowns allowed' in text):return 'lower'
    if 'pressure rate' in text and category in ('passing','line'):return 'lower'
    if any(word in text for word in ('frequency','usage','formation %','tendency','tendencies','continuity','concentration','variance','volatility','predictability','differential','trend','days since','rest differential','balance','designed-run rate','pass rate','go rate')):return 'context'
    return 'higher'


def unit(label):
    text=label.lower()
    if text in ('positive epa play %','negative epa play %','negative epa plays forced %'):return '%'
    if text=='pass rate over expected':return 'pp'
    if text in ('passing touchdowns','punts inside the 20','red-zone stops forced','goal-to-go defensive stops'):return 'count'
    if 'epa' in text:return 'EPA'
    if 'cpoe' in text or 'rate over expected' in text:return 'pp'
    if any(word in text for word in ('rate','frequency','usage','success','conversion','share','%')):return '%'
    if 'yard' in text or 'depth' in text or 'separation' in text:return 'yd'
    if 'time to' in text or 'pace' in text:return 's'
    return 'provider-defined'


def catalog():
    result=[]
    for order,(category,(title,subtitle,labels)) in enumerate(LIBRARY.items(),1):
        for label in labels.split('|'):
            text=label.lower()
            source='public_play_by_play'
            if any(x in text for x in ('adjust','rating','expected','effect','quality','value above','projected','drop-off','depth-chart','stability','decision','predictability','clutch','strength of','dependence')):source='validated_model_or_provider'
            elif any(x in text for x in ('pressure','coverage','zone','motion','personnel','rpo','route','block','contact','separation','hurries','shell','disguise','assignment','chip','lineup','starting-five','continuity','starter snaps','available','availability','big-time','turnover-worthy','time to throw','four-man','slot','receiving te','win %')):source='tracking_or_charting'
            result.append({'id':f'{category}.{slug(label)}','label':label,'category':category,'category_label':title,'category_subtitle':subtitle,'order':order,
                           'direction':direction(category,label),'unit':unit(label),'requirement':source,
                           'definition':f'{label}; a validated source-field calculation or an explicit provider/model definition is required. No inferred charting substitute.',
                           'definition_status':'requires_source_adapter','minimum_plays':100,'minimum_games':3,'sample_policy':'research screening policy, not empirically calibrated',
                           'views':[key for key,_ in TEAM_VIEWS]})
    if len({m['id'] for m in result})!=len(result):raise ValueError('duplicate team metric IDs')
    return result


TEAM_CATALOG=catalog()
GAPS=['left_end','left_tackle','left_guard','middle','right_guard','right_tackle','right_end']
