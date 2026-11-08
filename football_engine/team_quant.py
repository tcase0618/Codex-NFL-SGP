"""Team research calculations from one versioned play store; no invented forecasts."""
import hashlib
import json
import math
import random
import sqlite3
from collections import defaultdict
from datetime import datetime,timezone
from functools import lru_cache
from pathlib import Path
from statistics import mean,pstdev,pvariance

from .team_metrics import TEAM_CATALOG,TEAM_VIEWS,GAPS,slug
from .player_profile_store import number,timestamp

CALCULATION_REVISION=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

RULES={}
MATCHUP_METRICS={}
MATCHUP_RULES={}
def register(category,label,side,group,operation,field,scale=1):
    RULES[f'{category}.{slug(label)}']=(side,group,operation,field,scale)


def matchup_metric(key,label,side,group,column,scale=1):
    """Additional comparison definitions; do not relabel the 401 catalog slots."""
    MATCHUP_METRICS[key]={'id':key,'label':label,'category':'matchup','category_label':'Matchup comparisons',
        'unit':'%' if scale==100 else 'EPA/play','direction':'higher' if side=='offense' else 'lower',
        'minimum_plays':100,'minimum_games':3,'definition':f'{column} on {group}; same population on offense and defense'}
    MATCHUP_RULES[key]=(side,group,'mean',column,scale)


for side in ('offense','defense'):
    matchup_metric(f'matchup.{side}_explosive_pass_dropback','Explosive completions per dropback',side,'dropback','explosive_completion',100)
    matchup_metric(f'matchup.{side}_red_epa','Red-zone overall EPA',side,'red','epa')
    matchup_metric(f'matchup.{side}_red_pass_epa','Red-zone passing EPA',side,'red_pass','epa')
    matchup_metric(f'matchup.{side}_red_rush_epa','Red-zone rushing EPA',side,'red_run','epa')
    matchup_metric(f'matchup.{side}_deep_pass_epa','Deep passing EPA',side,'deep_throw','epa')


# mean EPA is on competitive scrimmage plays, including sacks/scrambles;
# NFL passing attempts separately exclude sacks and include recorded spikes.
for category,side in [('offense','offense'),('defense','defense')]:
    names=['EPA / offensive play','Offensive success rate','Positive EPA play %','Negative EPA play %','Early-down EPA','Explosive play rate','Play-to-play EPA volatility','Pass EPA / play','Rush EPA / play','Pass success rate','Rush success rate'] if side=='offense' else ['EPA allowed / play','Defensive success rate','Negative EPA plays forced %','Explosive plays allowed %','Opponent passing EPA','Opponent rushing EPA']
    specs=[('competitive','mean','epa',1),('competitive','mean','success_positive',100),('competitive','mean','success_positive',100),('competitive','mean','negative_epa',100),('early','mean','epa',1),('competitive','mean','explosive',100),('competitive','std','epa',1),('dropback','mean','epa',1),('run','mean','epa',1),('dropback','mean','success_positive',100),('run','mean','success_positive',100)] if side=='offense' else [('competitive','mean','epa',1),('competitive','mean','defensive_success',100),('competitive','mean','negative_epa',100),('competitive','mean','explosive',100),('dropback','mean','epa',1),('run','mean','epa',1)]
    for label,spec in zip(names,specs):register(category,label,side,*spec)
for category,side in [('passing','offense'),('pass_defense','defense')]:
    specs=[('Passing yards / game' if side=='offense' else 'Passing yards allowed / game','throw','per_game','passing_yards',1),
           ('Passing yards / attempt' if side=='offense' else 'Net passing yards / dropback allowed','throw' if side=='offense' else 'dropback','mean','passing_yards' if side=='offense' else 'net_passing_yards',1),
           ('Passing EPA / play' if side=='offense' else 'Passing EPA allowed','dropback','mean','epa',1),
           ('Passing success rate' if side=='offense' else 'Passing success rate allowed','dropback','mean','success_positive',100),
           ('Completion %' if side=='offense' else 'Completion % allowed','throw','mean','complete_pass',100),
           ('Team CPOE' if side=='offense' else 'CPOE allowed','throw','mean','cpoe',1),
           ('Passing TD rate' if side=='offense' else 'Passing TD rate allowed','throw','mean','pass_touchdown',100),
           ('Interception rate' if side=='offense' else 'Interception rate forced','throw','mean','interception',100),
           ('Sack rate' if side=='offense' else 'Sack rate generated','dropback','mean','sack',100),
           ('YAC / completion' if side=='offense' else 'YAC allowed / completion','completion','mean','yards_after_catch',1),
           ('YAC above expected' if side=='offense' else 'YAC above expected allowed','completion','mean','yac_over_expected',1)]
    for label,group,op,field,scale in specs:register(category,label,side,group,op,field,scale)
for label,group,op,field,scale in [('Net yards / dropback','dropback','mean','net_passing_yards',1),('Passing touchdowns','throw','sum','pass_touchdown',1),('Average depth of target','throw','mean','air_yards',1),('Deep shot rate','air_throw','mean','deep',100),('Short pass frequency','air_throw','mean','short',100),('Intermediate pass frequency','air_throw','mean','intermediate',100),('Explosive completion rate','completion','mean','explosive_pass',100),('Air yards / attempt','throw','mean','air_yards',1)]:register('passing',label,'offense',group,op,field,scale)
for category,side in [('rushing','offense'),('run_defense','defense')]:
    labels=['Rushing yards / game','Yards / carry','Rush EPA / attempt','Rush success rate','Rushing first-down rate','Rushing TD rate','Stuffed run rate','Explosive rush rate'] if side=='offense' else ['Rush yards allowed / game','Yards per carry allowed','Rush EPA allowed / play','Rush success rate allowed','Rushing first-down rate allowed','Rushing TD rate allowed','Stuffed run rate generated','Explosive rush rate allowed']
    specs=[('raw_run','per_game','yards_gained',1),('raw_run','mean','yards_gained',1),('run','mean','epa',1),('run','mean','success_positive',100),('run','mean','first_down_rush',100),('raw_run','mean','rush_touchdown',100),('run','mean','stuffed',100),('run','mean','explosive_run',100)]
    for label,spec in zip(labels,specs):register(category,label,side,*spec)
for label,group,op,field,scale in [('Catch rate','target','mean','complete_pass',100),('YAC per reception','completion','mean','yards_after_catch',1),('YAC over expected','completion','mean','yac_over_expected',1),('Explosive reception rate','completion','mean','explosive_pass',100),('Receiving EPA/target','target','mean','epa',1),('Target concentration index','target','hhi','receiver_player_id',1)]:register('receiving',label,'offense',group,op,field,scale)
for label,group,field in [('Short passing EPA allowed','short_throw','epa'),('Intermediate passing EPA allowed','intermediate_throw','epa'),('Deep passing EPA allowed','deep_throw','epa'),('Third-down pass EPA allowed','third_pass','epa')]:register('pass_defense',label,'defense',group,'mean',field)
register('pass_defense','Deep completion % allowed','defense','deep_throw','mean','complete_pass',100)
register('pass_defense','First-down pass rate allowed','defense','throw','mean','first_down_pass',100)
register('pass_defense','Explosive pass rate allowed','defense','dropback','mean','explosive_pass',100)
for label,group,field in [('Shotgun formation %','competitive','shotgun'),('Under-center formation %','competitive','under_center'),('No-huddle frequency','competitive','no_huddle')]:register('scheme',label,'offense',group,'mean',field,100)
register('scheme','Shotgun vs under-center EPA','offense','competitive','contrast','shotgun')
for label,group,op,field,scale in [('Sacks / game','dropback','per_game','sack',1),('Sack rate','dropback','mean','sack',100),('QB hits / game','dropback','per_game','qb_hit',1),('Sack EPA generated','sack','sum','negative_epa_value',1)]:register('pass_rush',label,'defense',group,op,field,scale)
register('line','Sack rate allowed','offense','dropback','mean','sack',100)
register('line','Stuffed run rate','offense','run','mean','stuffed',100)
register('line','Short-yardage conversion','offense','short_run','mean','first_down_or_td',100)
for label,group,field,scale in [('1st-down offensive EPA','first_down','epa',1),('2nd-down offensive EPA','second_down','epa',1),('3rd-down conversion %','third','third_conversion',100),('4th-down conversion %','fourth','fourth_conversion',100),('3rd-and-short conversion','third_short','third_conversion',100),('3rd-and-medium conversion','third_medium','third_conversion',100),('3rd-and-long conversion','third_long','third_conversion',100),('4th-and-short conversion','fourth_short','fourth_conversion',100),('Early-down pass rate','early','qb_dropback',100),('Early-down success rate','early','success_positive',100),('Red-zone rush EPA','red_run','epa',1),('Red-zone pass EPA','red_pass','epa',1),('Goal-line run success','goal_run','first_down_or_td',100),('Goal-line pass success','goal_pass','first_down_or_td',100),('First-half EPA','first_half','epa',1),('Second-half EPA','second_half','epa',1),('First-quarter EPA','q1','epa',1),('Fourth-quarter EPA','q4','epa',1),('Two-minute offense EPA','two_minute','epa',1),('One-score game EPA','one_score','epa',1),('Leading-state EPA','leading','epa',1),('Trailing-state EPA','trailing','epa',1)]:register('situational',label,'offense',group,'mean',field,scale)
register('situational','Two-minute defense EPA','defense','two_minute','mean','epa')
register('situational','Red-zone defensive EPA','defense','red','mean','epa')
register('run_defense','Tackles for loss / rush','defense','run','mean','tackled_for_loss',100)
register('run_defense','Short-yardage stop rate','defense','short_run','mean','not_first_down_or_td',100)
register('run_defense','Goal-line run stop rate','defense','goal_run','mean','not_first_down_or_td',100)
for label,group,field in [('FG success 40–49 yards','fg_40','fg_made'),('FG success 50+ yards','fg_50','fg_made'),('Extra-point success','pat','pat_made'),('Kickoff touchback rate','kickoff','touchback'),('Punt touchback rate','punt','touchback')]:register('special',label,'offense',group,'mean',field,100)
register('special','Average punt distance','offense','punt','mean','kick_distance')
register('special','Punts inside the 20','offense','punt','sum','punt_inside_twenty')
register('special','Special teams EPA / game','offense','special','per_game','epa')
register('coaching','Early-down pass tendency','offense','early','mean','qb_dropback',100)
register('coaching','Pass rate over expected','offense','competitive','mean','pass_oe')
register('coaching','Fourth-down go rate','offense','fourth_decision','mean','go_for_it',100)
for label,group in [('Home offensive EPA','home'),('Away offensive EPA','away'),('Indoor EPA','indoor'),('Outdoor EPA','outdoor'),('Short-rest EPA','short_rest'),('Long-rest EPA','long_rest')]:register('environment',label,'offense',group,'mean','epa')
for label,group in [('Home defensive EPA','home'),('Away defensive EPA','away')]:register('environment',label,'defense',group,'mean','epa')
for category,side,labels in [('offense','offense',['Points per game','Yards / game','Yards / play','First downs / game']),('defense','defense',['Points allowed / game','Yards allowed / game','Yards allowed / play','First downs allowed / game'])]:
    register(category,labels[0],side,'games','mean','team_points')
    register(category,labels[1],side,'raw','per_game','yards_gained')
    register(category,labels[2],side,'raw','mean','yards_gained')
    register(category,labels[3],side,'all','per_game','first_down')
for category,side in [('offense','offense'),('defense','defense')]:
    labels=['Points / drive','Touchdowns / drive','Three-and-out rate','EPA / drive'] if side=='offense' else ['Defensive points allowed / drive','Touchdowns allowed / drive','Three-and-out forced %','EPA allowed / drive']
    for label,field,scale in zip(labels,['points','td','three_out','epa'],[1,1,100,1]):register(category,label,side,'drives','mean',field,scale)
register('offense','Offensive points / game','offense','drives','per_game','points')
register('defense','Opposing drive scoring rate','defense','drives','mean','scored',100)
register('defense','Defensive stop rate','defense','drives','mean','stop',100)
register('defense','Takeaways / game','defense','competitive','per_game','takeaway')
register('defense','Forced fumble rate','defense','competitive','mean','defense_forced_fumble',100)
register('defense','Defensive touchdown rate','defense','competitive','mean','defensive_touchdown',100)
register('defense','Interception rate forced','defense','throw','mean','interception',100)
register('defense','Tackles for loss / game','defense','competitive','per_game','tackled_for_loss')
register('situational','Red-zone TD %','offense','red_drives','mean','td',100)
register('situational','Red-zone points / trip','offense','red_drives','mean','points')
register('situational','Goal-to-go TD %','offense','goal_drives','mean','td',100)
register('situational','Red-zone stops forced','defense','red_drives','sum','stop')
register('situational','Goal-to-go defensive stops','defense','goal_drives','sum','stop')
register('environment','Days since previous game','offense','games','mean','observed_calendar_rest')
register('environment','Opponent rest differential','offense','games','mean','rest_difference')
register('trends','Drive-level scoring variance','offense','drives','variance','points')
register('trends','One-score win rate','offense','games','mean','one_score_win',100)

# Field semantics changed alongside these adapters; cached definitions must be rebuilt.
DEFINITION_REVISION=hashlib.sha256(json.dumps({'catalog':TEAM_CATALOG,'rules':RULES,'semantics_version':3},sort_keys=True).encode()).hexdigest()


def competitive(p):return p.get('play_type') in ('pass','run') and p.get('two_point_attempt')!=1 and p.get('qb_kneel')!=1 and p.get('qb_spike')!=1
def raw_play(p):return p.get('play_type') in ('pass','run','qb_kneel','qb_spike') and p.get('two_point_attempt')!=1
def gap(p):
    location=p.get('run_location');which=p.get('run_gap')
    if location=='middle':return 'middle'
    if location in ('left','right') and which in ('end','tackle','guard'):return location+'_'+which
    return None


def field(p,key):
    epa=p.get('epa');yards=p.get('yards_gained');air=p.get('air_yards')
    if key=='explosive_completion':
        if p.get('complete_pass') not in (0,1):return None
        if p['complete_pass']==0:return 0
        return int(yards>=20) if yards is not None else None
    if key=='passing_yards' and p.get(key) is None and (p.get('incomplete_pass')==1 or p.get('interception')==1):return 0
    if key=='success_positive':return int(epa>0) if epa is not None else None
    if key=='defensive_success':return int(epa<=0) if epa is not None else None
    if key=='negative_epa':return int(epa<0) if epa is not None else None
    if key=='negative_epa_value':return -epa if epa is not None else None
    if key=='explosive':return int(yards>=(20 if p.get('qb_dropback')==1 else 10)) if yards is not None else None
    if key=='explosive_pass' and p.get('qb_scramble')==1:return 0
    if key in ('explosive_pass','explosive_run','stuffed'):
        if yards is None:return None
        return int(yards>=20) if key=='explosive_pass' else int(yards>=10) if key=='explosive_run' else int(yards<=0)
    if key in ('short','intermediate','deep'):return int(air<10) if key=='short' and air is not None else int(10<=air<20) if key=='intermediate' and air is not None else int(air>=20) if key=='deep' and air is not None else None
    if key=='under_center':return 1-p['shotgun'] if 'shotgun' in p else None
    if key in ('first_down_or_td','not_first_down_or_td'):
        if 'first_down' not in p or 'touchdown' not in p:return None
        value=int(p['first_down']==1 or p['touchdown']==1);return 1-value if key.startswith('not_') else value
    if key=='takeaway':
        if p.get('interception') not in (0,1) or p.get('fumble_lost') not in (0,1):return None
        if p['fumble_lost']==0:return p['interception']
        attributed=[(p.get('fumbled_'+str(i)+'_team'),p.get('fumble_recovery_'+str(i)+'_team')) for i in (1,2)]
        attributed=[(loser,winner) for loser,winner in attributed if loser and winner]
        if not attributed or not p.get('posteam') or not p.get('defteam'):return None
        return p['interception']+sum(loser==p.get('posteam') and winner==p.get('defteam') for loser,winner in attributed)
    if key=='defense_forced_fumble':
        if 'fumble_forced' not in p:return None
        if p['fumble_forced']==0:return 0
        teams=[p.get('forced_fumble_player_'+str(i)+'_team') for i in (1,2)];teams=[team for team in teams if team]
        return int(p.get('defteam') in teams) if teams else None
    if key=='defensive_touchdown':
        if 'touchdown' not in p:return None
        if p['touchdown']==0:return 0
        return int(p['td_team']==p.get('defteam')) if p.get('td_team') else None
    if key=='net_passing_yards':
        if p.get('qb_scramble')==1:return 0
        if p.get('sack')==1:return p.get('yards_gained')
        return field(p,'passing_yards')
    if key in ('third_conversion','fourth_conversion'):
        prefix='third_down' if key.startswith('third') else 'fourth_down'
        return p.get(prefix+'_converted') if p.get(prefix+'_converted',0)+p.get(prefix+'_failed',0)>0 else None
    if key=='yac_over_expected':return p['yards_after_catch']-p['xyac_mean_yardage'] if 'yards_after_catch' in p and 'xyac_mean_yardage' in p else None
    if key=='fg_made':return int(p['field_goal_result']=='made') if 'field_goal_result' in p else None
    if key=='pat_made':return int(p['extra_point_result']=='good') if 'extra_point_result' in p else None
    if key=='go_for_it':return int(raw_play(p))
    return p.get(key)


def qualifies(p,group):
    if group=='all':return True
    if group=='raw':return raw_play(p)
    if group=='raw_run':return raw_play(p) and p.get('rush_attempt')==1
    if group=='throw':return raw_play(p) and p.get('pass_attempt')==1 and p.get('sack')!=1 and p.get('qb_scramble')!=1
    if group=='completion':return qualifies(p,'throw') and p.get('complete_pass')==1
    if group=='target':return qualifies(p,'throw') and bool(p.get('receiver_player_id'))
    if group=='air_throw':return qualifies(p,'throw') and p.get('air_yards') is not None
    if group.endswith('_throw') and group.split('_')[0] in ('short','intermediate','deep'):return qualifies(p,'air_throw') and field(p,group.split('_')[0])==1
    if group=='punt':return p.get('punt_attempt')==1
    if group=='kickoff':return p.get('kickoff_attempt')==1
    if group=='pat':return p.get('extra_point_attempt')==1
    if group in ('fg_40','fg_50'):return p.get('field_goal_attempt')==1 and p.get('kick_distance') is not None and (40<=p['kick_distance']<50 if group=='fg_40' else p['kick_distance']>=50)
    if group=='special':return p.get('special_teams_play')==1
    if group=='fourth_decision':return p.get('down')==4 and (raw_play(p) or p.get('punt_attempt')==1 or p.get('field_goal_attempt')==1)
    if not competitive(p):return False
    if group=='competitive':return True
    if group=='dropback':return p.get('qb_dropback')==1
    if group=='run':return p.get('rush_attempt')==1 and p.get('qb_scramble')!=1
    if group=='sack':return p.get('sack')==1
    if group=='early':return p.get('down') in (1,2)
    if group=='first_down':return p.get('down')==1
    if group=='second_down':return p.get('down')==2
    if group in ('third','fourth'):return p.get('down')==(3 if group=='third' else 4)
    if group in ('third_short','third_medium','third_long','fourth_short'):
        down=4 if group=='fourth_short' else 3;distance=p.get('ydstogo')
        if distance is None:return False
        return p.get('down')==down and (distance<=2 if group.endswith('short') else 3<=distance<=6 if group.endswith('medium') else distance>=7)
    if group=='third_pass':return p.get('down')==3 and p.get('qb_dropback')==1
    if group in ('red','red_run','red_pass','goal_run','goal_pass'):
        distance=p.get('yardline_100');inside=distance is not None and distance<=(5 if group.startswith('goal') else 20)
        if not inside:return False
        if group.endswith('run'):return qualifies(p,'run')
        if group.endswith('pass'):return qualifies(p,'dropback')
        return True
    if group=='short_run':return qualifies(p,'run') and p.get('ydstogo') is not None and p['ydstogo']<=2
    if group in ('first_half','second_half'):return p.get('qtr') in ((1,2) if group=='first_half' else (3,4))
    if group in ('q1','q4'):return p.get('qtr')==(1 if group=='q1' else 4)
    if group=='two_minute':return p.get('qtr') in (2,4) and p.get('half_seconds_remaining') is not None and p['half_seconds_remaining']<=120
    if group in ('leading','trailing','tied','one_score'):
        score=p.get('score_differential')
        if score is None:return False
        return score>0 if group=='leading' else score<0 if group=='trailing' else score==0 if group=='tied' else abs(score)<=8
    return False


def aggregate_values(values,operation,scale=1):
    if not values:return None
    if operation in ('mean','per_game'):return mean(values)*scale
    if operation=='sum':return sum(values)*scale
    if operation=='std':return pstdev(values)*scale
    if operation=='variance':return pvariance(values)*scale
    return None


class TeamQuantStore:
    def __init__(self,path,*,context_path=None,charting_path=None):
        self.path=str(path)
        self.charting_path=str(charting_path) if charting_path else None
        self.context_metrics=None
        self.charting_metrics=None
        if charting_path:
            from .charting_metrics import ChartingMetrics
            self.charting_metrics=ChartingMetrics(charting_path)
            self.charting_metrics.verify_base(path)
        if context_path:
            from .team_context_metrics import TeamContextMetrics
            self.context_metrics=TeamContextMetrics(context_path)
            self.context_metrics.verify_base(path)

    @lru_cache(maxsize=8192)
    def charted_value(self,team,season,metric_id,view,as_of):
        return self.charting_metrics.value(team,season,metric_id,view,as_of) if self.charting_metrics else None
    def connect(self):
        db=sqlite3.connect(f'file:{self.path}?mode=ro',uri=True);db.row_factory=sqlite3.Row;return db

    @lru_cache(maxsize=1)
    def overview(self):
        with self.connect() as db:
            return {'summary':json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0]),
                    'teams':[r[0] for r in db.execute('SELECT team FROM team_context ORDER BY team')],
                    'seasons':[r[0] for r in db.execute('SELECT DISTINCT season FROM plays ORDER BY season DESC')],
                    'metrics':TEAM_CATALOG,'views':TEAM_VIEWS}

    @lru_cache(maxsize=128)
    def games(self,team,season,as_of=None):
        with self.connect() as db:
            raw=db.execute('SELECT g.*,s.observed_at FROM games g JOIN sources s ON s.id=g.source_id WHERE g.season=? AND (g.home=? OR g.away=?) AND EXISTS(SELECT 1 FROM plays p WHERE p.game_id=g.id) ORDER BY g.game_date,g.week',(season,team,team)).fetchall()
        if as_of:raw=[r for r in raw if timestamp(r['observed_at'])<=timestamp(as_of)]
        return [{**dict(r),'data':json.loads(r['data'])} for r in raw]

    @lru_cache(maxsize=96)
    def plays(self,team,season,side='offense',as_of=None):
        column='posteam' if side=='offense' else 'defteam'
        with self.connect() as db:raw=db.execute(f'SELECT p.game_id,p.play_id,p.posteam,p.defteam,p.data,p.source_id,s.observed_at FROM plays p JOIN sources s ON p.source_id=s.id WHERE p.{column}=? AND p.season=?',(team,season)).fetchall()
        if as_of:raw=[r for r in raw if timestamp(r['observed_at'])<=timestamp(as_of)]
        return [{**json.loads(r['data']),'posteam':r['posteam'],'defteam':r['defteam'],'game_id':r['game_id'],'play_id':r['play_id'],'source_id':r['source_id']} for r in raw]

    @lru_cache(maxsize=96)
    def drives(self,team,season,side='offense',as_of=None):
        groups=defaultdict(list)
        for play in self.plays(team,season,side,as_of):
            if play.get('fixed_drive') is not None:groups[(play['game_id'],play['fixed_drive'])].append(play)
        drives=[]
        for (gid,drive),plays in groups.items():
            raw=[p for p in plays if raw_play(p)]
            if not raw and not any(p.get('field_goal_attempt')==1 or p.get('punt_attempt')==1 for p in plays):continue
            outcomes={p.get('fixed_drive_result') for p in plays};starts=[p['posteam_score'] for p in plays if 'posteam_score' in p];ends=[p['posteam_score_post'] for p in plays if 'posteam_score_post' in p]
            points=max(ends)-min(starts) if starts and ends else None
            epa=[p['epa'] for p in plays if (competitive(p) or p.get('field_goal_attempt')==1 or p.get('punt_attempt')==1) and 'epa' in p]
            td=int('Touchdown' in outcomes)
            drives.append({'game_id':gid,'drive':drive,'points':points,'td':td,'three_out':int(len(raw)==3 and 'Punt' in outcomes and not any(p.get('first_down')==1 for p in raw)),
                           'epa':sum(epa) if epa else None,'scored':int(points>0) if points is not None else None,'stop':int(points==0) if points is not None and any(competitive(p) for p in raw) else None,
                           'red':any(p.get('yardline_100') is not None and p['yardline_100']<=20 for p in raw),
                           'goal':any(p.get('goal_to_go')==1 for p in raw),'source_id':plays[0]['source_id']})
        return drives

    def select(self,team,season,side,group,view,as_of):
        games=self.games(team,season,as_of);chosen=games
        if view in ('last3','last5','last8'):chosen=games[-int(view[-1]):]
        if view in ('home','away'):chosen=[g for g in chosen if (g['home']==team)==(view=='home')]
        if view in ('short_rest','long_rest'):chosen=[g for g in chosen if number(g['data'].get('home_rest' if g['home']==team else 'away_rest')) is not None and (number(g['data'].get('home_rest' if g['home']==team else 'away_rest'))<7 if view=='short_rest' else number(g['data'].get('home_rest' if g['home']==team else 'away_rest'))>7)]
        ids={g['id'] for g in chosen}
        if (group=='games' or group.endswith('drives')) and view in ('first_half','second_half','leading','trailing','tied'):
            return [],len(chosen)
        if group=='games':
            records=[]
            calendar_rest={games[i]['id']:(datetime.fromisoformat(games[i]['game_date'])-datetime.fromisoformat(games[i-1]['game_date'])).days for i in range(1,len(games))}
            for game in chosen:
                home=game['home']==team;own='home' if home else 'away';other='away' if home else 'home'
                own_rest=number(game['data'].get(own+'_rest'));other_rest=number(game['data'].get(other+'_rest'))
                margin=game[own+'_score']-game[other+'_score']
                records.append({'game_id':game['id'],'team_points':game[own+'_score'] if side=='offense' else game[other+'_score'],'own_rest':own_rest,'observed_calendar_rest':calendar_rest.get(game['id']),'rest_difference':own_rest-other_rest if own_rest is not None and other_rest is not None else None,'source_id':game['source_id'],
                                'one_score_win':(1 if margin>0 else .5 if margin==0 else 0) if abs(margin)<=8 else None})
            return records,len(chosen)
        if group.endswith('drives'):
            records=[p for p in self.drives(team,season,side,as_of) if p['game_id'] in ids]
            if group=='red_drives':records=[p for p in records if p['red']]
            if group=='goal_drives':records=[p for p in records if p['goal']]
            return records,len(chosen)
        records=[p for p in self.plays(team,season,side,as_of) if p['game_id'] in ids]
        if group in ('home','away','short_rest','long_rest','post_bye','indoor','outdoor'):
            selected=set()
            for game in chosen:
                home=game['home']==team;rest=number(game['data'].get('home_rest' if home else 'away_rest'));roof=game['data'].get('roof')
                valid=(home if group=='home' else not home if group=='away' else rest is not None and rest<7 if group=='short_rest' else rest is not None and rest>7 if group=='long_rest' else rest is not None and rest>=12 if group=='post_bye' else roof in ('dome','closed') if group=='indoor' else roof in ('outdoors','open'))
                if valid:selected.add(game['id'])
            records=[p for p in records if p['game_id'] in selected and competitive(p)]
        else:records=[p for p in records if qualifies(p,group)]
        if view in ('first_half','second_half','leading','trailing','tied'):
            # Score differential is from the possessing offense's perspective.
            # Defense team leading/trailing must reverse that sign.
            def matches(p):
                if view=='first_half':return p.get('qtr') in (1,2)
                if view=='second_half':return p.get('qtr') in (3,4)
                score=p.get('score_differential')
                if score is None:return False
                if side=='defense':score=-score
                return score>0 if view=='leading' else score<0 if view=='trailing' else score==0
            records=[p for p in records if matches(p)]
        return records,len(chosen)

    @lru_cache(maxsize=20000)
    def value(self,team,season,metric_id,view='season',as_of=None):
        metric=MATCHUP_METRICS[metric_id] if metric_id in MATCHUP_METRICS else next(m for m in TEAM_CATALOG if m['id']==metric_id)
        result={**metric,'value':None,'status':'UNAVAILABLE','sample_plays':0,'sample_games':0,'source_ids':[],
                'percentile':None,'qualified':False,'confidence_interval':None,'formula':None,
                'reason':'Required charting or validated model is not available; no box-score proxy is substituted.'}
        if self.context_metrics is not None and view not in ('percentile','confidence','yoy','three_year'):
            context_value=self.context_metrics.value(team,season,metric_id,view,as_of)
            if context_value is not None:
                weeks={g['id']:g['week'] for g in self.games(team,season,as_of)}
                return {**result,**context_value,'_game_values':{},'sample_unit':'plays',
                        'effective_minimum_sample':metric['minimum_plays'],
                        'observed_weeks':sorted({weeks[gid] for gid in context_value.get('game_ids',[]) if gid in weeks}),
                        'feature_signature':f'context|{metric_id}|{view}'}
        if self.charting_metrics is not None and view not in ('percentile','confidence','yoy','three_year'):
            charted=self.charted_value(team,season,metric_id,view,as_of)
            if charted is not None:
                return {**result,**charted,'source_ids':list(charted.get('source_ids',[])),
                        'sample_unit':'charted plays','effective_minimum_sample':metric['minimum_plays']}
        if view in ('man','zone','blitz','four_man','top10','bottom10','opponent_adjusted'):
            result['reason']='This view requires versioned charting or opponent ratings available at the relevant cutoff.';return result
        if view=='percentile':
            base=self.value(team,season,metric_id,'season',as_of);rank=self.percentile(team,season,metric_id,'season',as_of)
            if rank is None:return {**base,'value':None,'status':'UNAVAILABLE','qualified':False,'reason':'Insufficient qualified peers for a percentile.','percentile':None,'_game_values':{}}
            return {**base,'raw_value':base['value'],'value':rank['value'],'unit':'percentile','percentile':rank,'status':'DESCRIPTIVE_PERCENTILE','feature_signature':base.get('feature_signature','')+'|directional_percentile','_game_values':{}}
        if view=='confidence':
            base=self.value(team,season,metric_id,'season',as_of)
            return {**base,'confidence_interval':self.confidence(team,season,metric_id,'season',as_of)}
        rolling={'trends.last_3_games_epa':'last3','trends.last_5_games_epa':'last5','trends.last_8_games_epa':'last8'}
        if metric_id in rolling:
            if view!='season':result['reason']='This metric already defines its rolling game window; nested windows are withheld.';return result
            base=self.value(team,season,'offense.epa_offensive_play',rolling[metric_id],as_of)
            return {**base,**metric,'value':base['value'],'status':base['status'],'formula':f"{rolling[metric_id]}: "+str(base['formula'])}
        trend_bases={'trends.season_epa_trend_slope':'offense.epa_offensive_play','trends.passing_epa_trend':'offense.pass_epa_play',
                     'trends.rushing_epa_trend':'offense.rush_epa_play','trends.defensive_epa_trend':'defense.epa_allowed_play',
                     'trends.success_rate_trend':'offense.offensive_success_rate','trends.explosive_play_trend':'offense.explosive_play_rate',
                     'trends.game_level_epa_variance':'offense.epa_offensive_play'}
        if metric_id in trend_bases and view not in ('yoy','three_year'):
            base=self.value(team,season,trend_bases[metric_id],view,as_of);groups=base.get('_game_values',{})
            ordered=[g['id'] for g in self.games(team,season,as_of) if g['id'] in groups]
            ys=[mean(groups[gid]) for gid in ordered]
            if len(ys)<3:result['reason']='Needs at least three observed games for a descriptive trend.';return result
            xs=list(range(len(ys)));mx=mean(xs);my=mean(ys)
            value=pvariance(ys) if metric_id.endswith('variance') else sum((x-mx)*(y-my) for x,y in zip(xs,ys))/sum((x-mx)**2 for x in xs)
            if metric_id in ('trends.success_rate_trend','trends.explosive_play_trend'):value*=100
            return {**base,**metric,'value':round(value,6),'unit':'variance' if metric_id.endswith('variance') else 'change/game',
                    'status':'DESCRIPTIVE_TREND','confidence_interval':None,'percentile':None,'formula':'Population variance of game-mean EPA' if metric_id.endswith('variance') else 'OLS slope of observed game means versus chronological game index; not causal or schedule adjusted',
                    'feature_signature':base.get('feature_signature','')+('|variance' if metric_id.endswith('variance') else '|OLS_slope'),'_game_values':{}}
        if view in ('yoy','three_year'):
            years=range(season-2,season+1) if view=='three_year' else (season-1,season)
            samples=[self.value(team,y,metric_id,'season',as_of) for y in years]
            if any(s['value'] is None for s in samples):result['reason']='Comparable observations are missing in one or more required seasons.';return result
            if any(not s['qualified'] for s in samples):
                result['reason']='Every compared season must pass its metric sample and completeness gates.';return result
            if view=='yoy':
                if samples[0]['observed_weeks']!=samples[1]['observed_weeks']:result['reason']='Year-over-year windows have different observed weeks; unmatched comparisons are withheld.';return result
                return {**samples[-1],'value':samples[-1]['value']-samples[0]['value'],'status':'DESCRIPTIVE_CHANGE','percentile':None,'confidence_interval':None,'reason':None,
                        'feature_signature':samples[-1].get('feature_signature','')+'|yoy','source_ids':sorted({sid for sample in samples for sid in sample['source_ids']})}
            if RULES.get(metric_id,(None,None,None))[2]=='sum' and len({tuple(s['observed_weeks']) for s in samples})!=1:
                result['reason']='Unequal season exposure makes a trend in totals incomparable.';return result
            return {**samples[-1],'value':samples[-1]['value']-samples[0]['value'],'trend_points':[{'season':y,'value':s['value']} for y,s in zip(years,samples)],'status':'DESCRIPTIVE_CHANGE','percentile':None,'reason':None,'confidence_interval':None,
                    'feature_signature':samples[-1].get('feature_signature','')+'|three_year_delta','source_ids':sorted({sid for sample in samples for sid in sample['source_ids']})}
        rule=RULES.get(metric_id) or MATCHUP_RULES.get(metric_id)
        if not rule:return result
        side,group,operation,column,scale=rule
        rows,total_games=self.select(team,season,side,group,view,as_of)
        # Other margins are outside the one-score cohort, not missing outcomes.
        if column=='one_score_win':rows=[p for p in rows if p.get('one_score_win') is not None]
        values=[(p,field(p,column)) for p in rows];values=[(p,v) for p,v in values if v is not None]
        if operation=='contrast':values=[(p,v) for p,v in values if v in (0,1) and number(p.get('epa')) is not None]
        if not values:result['reason']='No usable observations for this source, window and metric.';return result
        grouped=defaultdict(list)
        for p,v in values:grouped[p['game_id']].append(v)
        numeric=[v for _,v in values]
        if operation=='per_game':value=sum(numeric)/total_games if total_games else None
        elif operation=='hhi':
            counts=defaultdict(int)
            for value in numeric:counts[value]+=1
            value=sum((count/len(numeric))**2 for count in counts.values())
        elif operation=='contrast':
            shotgun=[p['epa'] for p,_ in values if p.get('shotgun')==1 and p.get('epa') is not None];under=[p['epa'] for p,_ in values if p.get('shotgun')==0 and p.get('epa') is not None]
            value=mean(shotgun)-mean(under) if shotgun and under else None
        else:value=aggregate_values(numeric,operation,scale)
        if value is None or not math.isfinite(value):return result
        samples=len(values);games=len(grouped)
        complete_fields=len(values)==len(rows)
        minimum=3 if group=='games' else 20 if group.endswith('drives') else metric['minimum_plays']
        qualified=games>=metric['minimum_games'] and samples>=minimum and complete_fields
        contrast_samples={}
        if operation=='contrast':
            for arm in (0,1):
                arm_rows=[p for p,v in values if v==arm]
                contrast_samples[str(arm)]={'plays':len(arm_rows),'games':len({p['game_id'] for p in arm_rows})}
            qualified=qualified and all(s['plays']>=minimum and s['games']>=metric['minimum_games'] for s in contrast_samples.values())
        formula=f'{operation}({column}) on {side}/{group}; scale {scale}'
        unit=metric['unit']
        if scale==100:unit='%'
        if column=='pass_oe':unit='pp'
        if column in ('points','team_points'):unit='points'
        if column=='td' and scale==1:unit='TD/drive'
        if column=='receiver_player_id':unit='HHI'
        if column in ('own_rest','observed_calendar_rest','rest_difference'):unit='days'
        result.update(value=round(value,5),status='CALCULATED' if qualified else 'INCOMPLETE_FIELDS' if not complete_fields else 'SMALL_SAMPLE',sample_plays=samples,sample_games=games,
                      source_ids=sorted({p['source_id'] for p,_ in values}),qualified=qualified,formula=formula,unit=unit,
                      reason=None if complete_fields else 'Eligible observations have missing metric fields; sample gate is withheld.',eligible_observations=len(rows),complete_fields=complete_fields,denominator_games=total_games,observed_weeks=sorted({g['week'] for g in self.games(team,season,as_of) if g['id'] in grouped}),
                      policy='Success means offensive EPA > 0 (defensive success EPA <= 0), not yardage conversion. Dropbacks include sacks and scrambles; net passing credits zero scramble yards. Rush EPA excludes scrambles, while raw rushing production includes them. Short throws include negative air yards. Scrimmage EPA excludes kneels, spikes and two-point tries. Raw production includes recorded kneels/spikes. Drive EPA additionally includes terminal punt/FG EPA; drive points use observed score changes, not assumed seven-point TDs. Rates pool eligible observations; no means of game percentages.',
                      _game_values={key:vs for key,vs in grouped.items()} if operation in ('mean','sum','per_game') else {})
        result['feature_signature']='|'.join(map(str,(side,group,operation,column,scale,view)))
        result['sample_unit']='games' if group=='games' else 'drives' if group.endswith('drives') else 'plays'
        result['effective_minimum_sample']=minimum
        if contrast_samples:result['contrast_samples']=contrast_samples
        result['provider_model_dependency']='nflverse EPA/CPOE/expected-completion model; training vintage not independently verified' if column in ('epa','cpoe','yac_over_expected','pass_oe','success_positive','defensive_success','negative_epa','negative_epa_value') else None
        return result

    def confidence(self,team,season,metric_id,view='season',as_of=None):
        if view in ('yoy','three_year','percentile'):
            return {'status':'UNAVAILABLE','reason':'Changes, trends and directional percentile ranks require a separate uncertainty estimator; a season-level interval is not substituted.'}
        value=self.value(team,season,metric_id,view,as_of);groups=value.get('_game_values',{})
        if not value['qualified'] or len(groups)<5 or not groups:return {'status':'UNAVAILABLE','reason':'Needs a supported additive/rate estimator, at least five game clusters and the metric sample gate.'}
        if metric_id not in RULES:return {'status':'UNAVAILABLE','reason':'A separate confidence estimator for this rolling/trend metric is not validated.'}
        rule=RULES[metric_id];operation,scale=rule[2],rule[4]
        if operation=='per_game' and len(groups)!=value['denominator_games']:
            return {'status':'UNAVAILABLE','reason':'Per-game bootstrap requires all denominator games; games without eligible events are not silently omitted.'}
        clusters=list(groups.values());seed=int(hashlib.sha256(f"{team}|{season}|{value.get('feature_signature',metric_id)}|{view}".encode()).hexdigest()[:12],16);rng=random.Random(seed);draws=[]
        for _ in range(400):
            sampled=[rng.choice(clusters) for _ in clusters];total=sum(sum(v) for v in sampled)
            draws.append(total/len(sampled) if operation=='per_game' else total*scale if operation=='sum' else total/sum(map(len,sampled))*scale)
        draws.sort();return {'status':'DESCRIPTIVE_GAME_CLUSTER_BOOTSTRAP','level':.95,'lower':round(draws[9],5),'upper':round(draws[389],5),'resamples':400,'game_clusters':len(clusters),'method':'Percentile bootstrap resampling whole observed games; not a predictive interval or calibrated betting probability.'}

    def percentile(self,team,season,metric_id,view='season',as_of=None):
        if view in ('percentile','confidence'):view='season'
        value=self.value(team,season,metric_id,view,as_of)
        if not value['qualified']:return None
        peers=[self.value(t,season,metric_id,view,as_of) for t in self.overview()['teams']]
        numbers=[p['value'] for p in peers if p['qualified'] and p['value'] is not None]
        if len(numbers)<8:return None
        rank=100*(sum(v<value['value'] for v in numbers)+.5*sum(v==value['value'] for v in numbers))/len(numbers)
        if value['direction']=='lower':rank=100-rank
        return {'value':round(rank,1),'peers':len(numbers),'direction':value['direction'],'meaning':'Higher is better' if value['direction']!='context' else 'Raw-value rank; no preferred direction'}

    def gap_matrix(self,team,season,side='offense',view='season',as_of=None):
        if view not in ('season','last3','last5','last8','home','away','first_half','second_half','leading','trailing','tied','short_rest','long_rest'):
            return {'rows':[],'mapped_plays':0,'eligible_runs':0,'unmapped_plays':0,'concept_classification':'This view is not supported for the gap matrix; no season average was substituted.'}
        rows,_=self.select(team,season,side,'run',view,as_of);mapped=[p for p in rows if gap(p)]
        result=[]
        for name in GAPS:
            plays=[p for p in mapped if gap(p)==name];epas=[p['epa'] for p in plays if p.get('epa') is not None]
            yards=[p['yards_gained'] for p in plays if p.get('yards_gained') is not None]
            games={p['game_id'] for p in plays if p.get('epa') is not None}
            result.append({'gap':name,'plays':len(plays),'frequency':100*len(plays)/len(mapped) if mapped else None,
                           'epa':mean(epas) if epas else None,'success_rate':100*sum(e>0 for e in epas)/len(epas) if epas else None,
                           'explosive_rate':100*sum(y>=10 for y in yards)/len(yards) if yards else None,
                           'epa_sample':len(epas),'yards_sample':len(yards),
                           'small_sample':len(epas)<100 or len(yards)<100 or len(games)<3})
        return {'rows':result,'mapped_plays':len(mapped),'eligible_runs':len(rows),'unmapped_plays':len(rows)-len(mapped),'concept_classification':'UNAVAILABLE: direction/gap is not inside-zone, outside-zone or gap/power scheme.'}

    def profile(self,team,season=2026,view='season',as_of=None):
        if team not in self.overview()['teams'] or view not in dict(TEAM_VIEWS):raise ValueError('unknown team or analytical view')
        if as_of:timestamp(as_of)
        values=[]
        for metric in TEAM_CATALOG:
            value=dict(self.value(team,season,metric['id'],view,as_of));value.pop('_game_values',None)
            if value['value'] is not None:value['percentile']=self.percentile(team,season,metric['id'],'season' if view in ('percentile','confidence') else view,as_of) if view not in ('three_year','yoy') else None
            values.append(value)
        with self.connect() as db:
            context=json.loads(db.execute('SELECT data FROM team_context WHERE team=?',(team,)).fetchone()[0])
            sources=[dict(r) for r in db.execute('SELECT * FROM sources')]
        if self.context_metrics is not None:sources+=self.context_metrics.sources
        if self.charting_metrics is not None:sources+=self.charting_metrics.sources()
        if season!=context.get('season_year') or as_of and timestamp(context['as_of'])>timestamp(as_of):
            context={'team_id':team,'status':'CURRENT_COACHING_NOT_ATTRIBUTED_TO_HISTORICAL_SEASON','as_of':context.get('as_of'),'coaching':None,'personnel':None}
        games=self.games(team,season,as_of)
        offense_groups=self.value(team,season,'offense.epa_offensive_play','season',as_of).get('_game_values',{})
        defense_groups=self.value(team,season,'defense.epa_allowed_play','season',as_of).get('_game_values',{})
        trends=[{'game_id':g['id'],'week':g['week'],'date':g['game_date'],'opponent':g['away'] if g['home']==team else g['home'],
                 'points_for':g['home_score'] if g['home']==team else g['away_score'],'points_against':g['away_score'] if g['home']==team else g['home_score'],
                 'offensive_epa':mean(offense_groups[g['id']]) if offense_groups.get(g['id']) else None,
                 'defensive_epa':mean(defense_groups[g['id']]) if defense_groups.get(g['id']) else None} for g in games]
        personnel_context=self.context_metrics.personnel(team,season,as_of) if self.context_metrics is not None else None
        return {'team':team,'season':season,'view':view,'metrics':values,'games':games,'context':context,'sources':sources,
                'personnel_context':personnel_context,
                'calculation_revision':CALCULATION_REVISION,'definition_revision':DEFINITION_REVISION,'knowledge_cutoff':as_of,
                'game_trends':trends,
                'coverage':{'defined':401,'populated':sum(v['value'] is not None for v in values),'qualified':sum(v['qualified'] for v in values),'games':len(games)},
                'gap_offense':self.gap_matrix(team,season,'offense',view,as_of),'gap_defense':self.gap_matrix(team,season,'defense',view,as_of),
                'historically_pregame_certified':False,'model_status':'DESCRIPTIVE_NOT_CALIBRATED','forecast':None}

    def evidence(self,team,season,metric_id,view='season',as_of=None):
        """Evidence follows the adapter that actually supplies the displayed value."""
        value=self.value(team,season,metric_id,view,as_of)
        context=self.context_metrics.value(team,season,metric_id,view,as_of) if self.context_metrics else None
        charted=self.charted_value(team,season,metric_id,view,as_of) if context is None else None
        if context is not None or charted is not None:
            sources=self.context_metrics.sources if context is not None else self.charting_metrics.sources()
            result={'rows':[],'total_eligible_rows':None,'limit':80,'evidence_kind':'SOURCED_AGGREGATE',
                    'reason':'This adapter exposes aggregate evidence; no sampled play rows are asserted.',
                    'aggregate':{k:v for k,v in value.items() if not k.startswith('_')},
                    'sources':[s for s in sources if s['id'] in value['source_ids']],
                    'no_historical_available_at_certification':True}
            if context is None and hasattr(self.charting_metrics,'evidence'):
                result.update(self.charting_metrics.evidence(team,season,metric_id,view,as_of,limit=80))
            return result
        rule=RULES.get(metric_id) or MATCHUP_RULES.get(metric_id)
        if not rule:return {'rows':[],'reason':value.get('reason') or 'No approved observation adapter for this selection.','aggregate':value}
        rows,_=self.select(team,season,rule[0],rule[1],view,as_of)
        return {'rows':[{'game_id':p['game_id'],'play_id':p.get('play_id'),'source_id':p.get('source_id'),
                         'observed_value':field(p,rule[3]),'data':p} for p in rows[:80]],
                'total_eligible_rows':len(rows),'limit':80,'evidence_kind':'PLAY_OBSERVATIONS',
                'no_historical_available_at_certification':True}

    def player_trace(self,player_id,season,as_of=None):
        with self.connect() as db:
            rows=db.execute('SELECT e.role,p.game_id,p.play_id,p.data,p.source_id,s.observed_at FROM player_events e JOIN plays p ON p.game_id=e.game_id AND p.play_id=e.play_id JOIN sources s ON s.id=p.source_id WHERE e.player_id=? AND p.season=? ORDER BY p.game_id,CAST(p.play_id AS REAL)',(player_id,season)).fetchall()
        if as_of:rows=[row for row in rows if timestamp(row['observed_at'])<=timestamp(as_of)]
        return {'player_id':player_id,'season':season,'participation_scope':'Explicit passer/receiver/rusher events only; not all 11-player snap participation',
                'events':[{'game_id':r['game_id'],'play_id':r['play_id'],'role':r['role'],'source_id':r['source_id'],'data':json.loads(r['data'])} for r in rows]}

    def player_summary(self,player_id,season,as_of=None):
        trace=self.player_trace(player_id,season,as_of);rows=trace['events'];passes=[r['data'] for r in rows if r['role']=='passer' and qualifies(r['data'],'throw')]
        rushes=[r['data'] for r in rows if r['role']=='rusher' and qualifies(r['data'],'raw_run')]
        targets=[r['data'] for r in rows if r['role']=='receiver' and qualifies(r['data'],'target')]
        metrics={}
        def total(key,records,column):
            values=[field(p,column) for p in records];known=[v for v in values if v is not None]
            metrics[key]={'value':sum(known) if known else None,'known_events':len(known),'eligible_events':len(records),'complete_fields':bool(records) and len(known)==len(records)}
        total('passing_yards',passes,'passing_yards');total('rushing_yards',rushes,'rushing_yards');total('receiving_yards',[p for p in targets if p.get('complete_pass')==1],'receiving_yards')
        metrics['passing_attempts']={'value':len(passes) if rows else None,'scope':'Recorded passer-role NFL throw attempts, excluding sacks'}
        metrics['rushing_attempts']={'value':len(rushes) if rows else None,'scope':'Recorded primary-rusher attempts, including kneels'}
        metrics['targets']={'value':len(targets) if rows else None,'scope':'Recorded intended-receiver targets on throws'}
        metrics['receptions']={'value':sum(p.get('complete_pass',0) for p in targets) if targets else None,'scope':'Completions on recorded receiver events'}
        metrics['completion_pct']={'value':100*sum(p.get('complete_pass',0) for p in passes)/len(passes) if passes else None,'scope':'Recorded passer throws'}
        for label,plays in [('target_play_epa',targets),('rushing_play_epa',rushes)]:
            values=[p['epa'] for p in plays if competitive(p) and 'epa' in p]
            metrics[label]={'value':mean(values) if values else None,'scope':'Offensive play EPA on these recorded events; not isolated causal player credit'}
        ids=sorted({r['source_id'] for r in rows})
        with self.connect() as db:sources=[dict(r) for r in db.execute('SELECT * FROM sources') if r['id'] in ids]
        return {'player_id':player_id,'season':season,'status':'OBSERVED_SHARED_PLAY_CONTEXT' if rows else 'UNAVAILABLE','metrics':metrics,
                'unique_plays':len({(r['game_id'],r['play_id']) for r in rows}),'source_namespace':'team_quant_play_store','sources':sources,
                'scope':trace['participation_scope'],'causal_player_grade':None,'historically_pregame_certified':False}

    def matchup(self,first,second,season,view='season',as_of=None):
        if first==second:raise ValueError('select two different teams')
        pairs=[('Passing efficiency','passing.passing_epa_play','pass_defense.passing_epa_allowed'),
               ('Rushing success','rushing.rush_success_rate','run_defense.rush_success_rate_allowed'),
               ('Explosive passing per dropback','matchup.offense_explosive_pass_dropback','matchup.defense_explosive_pass_dropback'),
               ('Explosive rushing rate','rushing.explosive_rush_rate','run_defense.explosive_rush_rate_allowed'),
               ('Completion rate','passing.completion','pass_defense.completion_allowed'),
               ('Deep passing efficiency','matchup.offense_deep_pass_epa','matchup.defense_deep_pass_epa'),
               ('Sack exposure','passing.sack_rate','pass_rush.sack_rate'),
               ('Red-zone overall efficiency','matchup.offense_red_epa','matchup.defense_red_epa'),
               ('Red-zone passing efficiency','matchup.offense_red_pass_epa','matchup.defense_red_pass_epa'),
               ('Red-zone rushing efficiency','matchup.offense_red_rush_epa','matchup.defense_red_rush_epa'),
               ('Yards after catch per completion','passing.yac_completion','pass_defense.yac_allowed_completion')]
        rules={**RULES,**MATCHUP_RULES}
        comparisons=[]
        for offense,defense in ((first,second),(second,first)):
            factors=[]
            for label,a,b in pairs:
                av=self.value(offense,season,a,view,as_of);bv=self.value(defense,season,b,view,as_of)
                factors.append({'factor':label,'offense':{k:v for k,v in av.items() if not k.startswith('_')},'defense':{k:v for k,v in bv.items() if not k.startswith('_')},
                                'offense_percentile':self.percentile(offense,season,a,view,as_of),'defense_percentile':self.percentile(defense,season,b,view,as_of),
                                'comparable':rules[a][1:]==rules[b][1:],
                                'comparison_basis':{'population':rules[a][1],'field':rules[a][3],'scale':rules[a][4]},'predicted_edge':None})
            comparisons.append({'offense':offense,'defense':defense,'factors':factors})
        return {'teams':[first,second],'season':season,'comparisons':comparisons,'status':'DESCRIPTIVE_MATCHUP_CONTEXT_ONLY','score_distribution':None,'win_probability':None,
                'missing':['Confirmed game-day personnel and stable lineup assignments','Coverage/concept charting where unavailable','Validated interaction model and walk-forward calibration'],
                'note':'Percentiles compare each metric to its own qualified league cohort. Different denominators are not subtracted into a forecast or edge.'}
