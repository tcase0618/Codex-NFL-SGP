"""Shared game/play source of truth for team research and player event traces."""
import argparse
import csv
import gzip
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

from football_engine.team_metrics import TEAM_CATALOG,TEAM_VIEWS
from football_engine.player_profile_store import number

ALIASES={'LA':'LAR','STL':'LAR','SD':'LAC','OAK':'LV','WAS':'WSH','JAC':'JAX'}
NUMERIC=('week down qtr yardline_100 ydstogo game_seconds_remaining half_seconds_remaining quarter_seconds_remaining '
'yards_gained epa success cpoe cp air_yards yards_after_catch passing_yards rushing_yards receiving_yards '
'qb_dropback qb_kneel qb_spike qb_scramble pass_attempt rush_attempt sack complete_pass incomplete_pass interception '
'first_down first_down_pass first_down_rush first_down_penalty third_down_converted third_down_failed fourth_down_converted fourth_down_failed '
'touchdown pass_touchdown rush_touchdown return_touchdown fumble fumble_lost fumble_forced tackled_for_loss qb_hit '
'shotgun no_huddle goal_to_go two_point_attempt penalty penalty_yards special_teams_play field_goal_attempt kick_distance '
'punt_attempt kickoff_attempt extra_point_attempt touchback punt_inside_twenty return_yards score_differential '
'posteam_score posteam_score_post defteam_score defteam_score_post drive_play_count drive_first_downs drive_inside20 '
'drive_ended_with_score xyac_mean_yardage xpass pass_oe').split()
TEXT=('posteam defteam posteam_type play_type run_location run_gap pass_location pass_length field_goal_result extra_point_result '
'penalty_team penalty_type fixed_drive fixed_drive_result drive_start_yard_line passer_player_id passer_player_name '
'receiver_player_id receiver_player_name rusher_player_id rusher_player_name punt_returner_player_id kickoff_returner_player_id '
'return_team fumble_recovery_1_team fumble_recovery_2_team fumbled_1_team fumbled_2_team forced_fumble_player_1_team forced_fumble_player_2_team td_team').split()


def build(root,archive,output,player_database=None):
    output.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((archive/'manifest.json').read_text());db=sqlite3.connect(output/'teams.sqlite')
    db.executescript('''PRAGMA journal_mode=DELETE;
      CREATE TABLE sources(id INTEGER PRIMARY KEY,reference TEXT,sha256 TEXT,observed_at TEXT,path TEXT,kind TEXT);
      CREATE TABLE games(id TEXT PRIMARY KEY,season INTEGER,week INTEGER,game_date TEXT,home TEXT,away TEXT,home_score REAL,away_score REAL,data TEXT,source_id INTEGER);
      CREATE TABLE plays(game_id TEXT,play_id TEXT,season INTEGER,posteam TEXT,defteam TEXT,source_id INTEGER,data TEXT,PRIMARY KEY(game_id,play_id));
      CREATE INDEX plays_offense ON plays(posteam,season,game_id);CREATE INDEX plays_defense ON plays(defteam,season,game_id);
      CREATE VIEW drives AS SELECT game_id,posteam,source_id,json_extract(data,'$.fixed_drive') AS drive_id,COUNT(*) AS source_rows
        FROM plays WHERE posteam IS NOT NULL AND json_extract(data,'$.fixed_drive') IS NOT NULL GROUP BY game_id,posteam,source_id,drive_id;
      CREATE TABLE player_participation(game_id TEXT,play_id TEXT,player_id TEXT,source_id INTEGER,role TEXT);
      CREATE TABLE lineups(game_id TEXT,play_id TEXT,team TEXT,player_ids TEXT,source_id INTEGER);
      CREATE TABLE formations(game_id TEXT,play_id TEXT,team TEXT,data TEXT,source_id INTEGER);
      CREATE TABLE play_charting(game_id TEXT,play_id TEXT,data TEXT,source_id INTEGER);
      CREATE TABLE player_events(player_id TEXT,game_id TEXT,play_id TEXT,role TEXT,name_as_reported TEXT,source_id INTEGER);
      CREATE INDEX player_event_identity ON player_events(player_id,game_id);
      CREATE TABLE player_metrics(player_id TEXT,metric_id TEXT,window TEXT,value REAL,source_id INTEGER);
      CREATE TABLE team_metrics(team TEXT,metric_id TEXT,window TEXT,value REAL,source_id INTEGER);
      CREATE TABLE injuries(team TEXT,player_id TEXT,data TEXT,source_id INTEGER);
      CREATE TABLE metric_definitions(id TEXT PRIMARY KEY,data TEXT);
      CREATE TABLE model_predictions(id TEXT PRIMARY KEY,as_of TEXT,trained_through TEXT,data TEXT,source_id INTEGER);
      CREATE TABLE team_context(team TEXT PRIMARY KEY,data TEXT,source_id INTEGER);
      CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
    ''')
    sources={}
    for source in manifest['sources']:
        if source['status']!='ARCHIVED':continue
        path=Path(source['path']);assert hashlib.sha256(path.read_bytes()).hexdigest()==source['sha256']
        sid=db.execute('INSERT INTO sources VALUES(NULL,?,?,?,?,?)',(source['reference'],source['sha256'],source['observed_at'],str(path),source['kind'])).lastrowid
        sources[source['name']]=sid
    now=datetime.now(timezone.utc)
    finals={};excluded=[];mismatches=[]
    # Schedules supply explicit reported final scores, not a guess from the
    # presence of play rows. Current-season ESPN identities/scores are checked
    # when a matching independently archived summary is available.
    with (archive/'games.csv').open(encoding='utf-8-sig',newline='') as handle:
        for row in csv.DictReader(handle):
            year=int(row['season']);home_score=number(row['home_score']);away_score=number(row['away_score'])
            if year<2006 or row['game_type']!='REG' or home_score is None or away_score is None or row['gameday']>now.date().isoformat():continue
            home=ALIASES.get(row['home_team'],row['home_team']);away=ALIASES.get(row['away_team'],row['away_team'])
            if year==2026 and row.get('espn'):
                try:event=str(int(float(row['espn'])))
                except ValueError:event=row['espn']
                url=f'https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event}'
                cache=root/'outputs/reconstructed-replay-20261103-v4/final-boxes';path=cache/(hashlib.sha256(url.encode()).hexdigest()+'.json')
                if path.exists():
                    raw=json.loads(path.read_bytes());comp=raw['header']['competitions'][0]
                    scores={c['team']['abbreviation']:float(c['score']) for c in comp['competitors']}
                    if scores.get(home)!=home_score or scores.get(away)!=away_score or comp['status']['type']['name']!='STATUS_FINAL':
                        mismatches.append({'game':row['game_id'],'espn':event,'schedule_scores':{home:home_score,away:away_score},'espn_scores':scores});continue
            finals[row['game_id']]=row
            data={k:row.get(k) for k in ('gameday','gametime','roof','surface','temp','wind','home_rest','away_rest','home_qb_id','away_qb_id','home_coach','away_coach','espn','location')}
            db.execute('INSERT INTO games VALUES(?,?,?,?,?,?,?,?,?,?)',(row['game_id'],year,int(row['week']),row['gameday'],home,away,home_score,away_score,json.dumps(data,separators=(',',':')),sources['games.csv']))
    counts=Counter();duplicate_plays=0
    for source in sorted(manifest['sources'],key=lambda x:x.get('season',0)):
        if source['status']!='ARCHIVED' or source['kind']!='pbp':continue
        sid=sources[source['name']];batch=[];events=[];forms=[]
        with gzip.open(source['path'],'rt',encoding='utf-8-sig',newline='') as handle:
            for raw in csv.DictReader(handle):
                gid=raw.get('game_id');pid=raw.get('play_id')
                if gid not in finals or not pid or number(raw.get('play_deleted'))==1:continue
                row={key:value for key in TEXT if (value:=raw.get(key)) not in (None,'','NA')}
                row.update({key:value for key in NUMERIC if (value:=number(raw.get(key))) is not None})
                for key in ('posteam','defteam','penalty_team','return_team','fumble_recovery_1_team','fumble_recovery_2_team','fumbled_1_team','fumbled_2_team','forced_fumble_player_1_team','forced_fumble_player_2_team','td_team'):
                    if key in row:
                        row['source_'+key]=row[key];row[key]=ALIASES.get(row[key],row[key])
                batch.append((gid,pid,source['season'],row.get('posteam'),row.get('defteam'),sid,json.dumps(row,separators=(',',':'))))
                for role in ('passer','receiver','rusher'):
                    if raw.get(role+'_player_id'):
                        events.append((raw[role+'_player_id'],gid,pid,role,raw.get(role+'_player_name'),sid))
                if 'shotgun' in row or 'no_huddle' in row:
                    forms.append((gid,pid,row.get('posteam'),json.dumps({key:row[key] for key in ('shotgun','no_huddle') if key in row}),sid))
                counts[source['season']]+=1
                if len(batch)>=2000:
                    db.executemany('INSERT INTO plays VALUES(?,?,?,?,?,?,?)',batch);db.executemany('INSERT INTO player_events VALUES(?,?,?,?,?,?)',events);db.executemany('INSERT INTO formations VALUES(?,?,?,?,?)',forms);batch=[];events=[];forms=[]
        db.executemany('INSERT INTO plays VALUES(?,?,?,?,?,?,?)',batch);db.executemany('INSERT INTO player_events VALUES(?,?,?,?,?,?)',events);db.executemany('INSERT INTO formations VALUES(?,?,?,?,?)',forms);db.commit()
        print(json.dumps({'season':source['season'],'plays':counts[source['season']]}),flush=True)
    # Drop no observations. A scheduled final without parsed plays remains a
    # coverage gap, and never supplies a zero-efficiency game.
    missing=[row[0] for row in db.execute('SELECT id FROM games WHERE NOT EXISTS(SELECT 1 FROM plays WHERE plays.game_id=games.id)')]
    status=json.loads((root/'outputs/football-refresh-status.json').read_text());context_path=Path(status['latest_profiles']);context=json.loads(context_path.read_text())
    sid=db.execute('INSERT INTO sources VALUES(NULL,?,?,?,?,?)',(str(context_path),hashlib.sha256(context_path.read_bytes()).hexdigest(),context['as_of'],str(context_path),'reviewed_team_context')).lastrowid
    for team,data in context['profiles'].items():db.execute('INSERT INTO team_context VALUES(?,?,?)',(team,json.dumps(data,separators=(',',':')),sid))
    for metric in TEAM_CATALOG:db.execute('INSERT INTO metric_definitions VALUES(?,?)',(metric['id'],json.dumps(metric)))
    for table in ('sources','games','plays','player_events','lineups','player_participation','formations','play_charting','injuries','metric_definitions','model_predictions'):
        for action in ('UPDATE','DELETE'):
            db.execute(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable research observation'); END")
    report={'created_at':now.isoformat(),'teams':32,'metric_slots':len(TEAM_CATALOG),'categories':18,'views':len(TEAM_VIEWS),'play_counts':dict(counts),'plays':sum(counts.values()),
            'final_schedule_games':len(finals),'games_without_plays':missing,'score_conflicts_quarantined':mismatches,'source_vintage':'CURRENT_COLLECTION_NOT_PREGAME_CERTIFIED',
            'player_database':str(player_database) if player_database else None,'models_calibrated':False,'lineups_available':False,
            'missing_pbp_seasons':manifest['missing_pbp_seasons'],'coverage_status':'PARTIAL_ADVANCED_SOURCE_COVERAGE'}
    db.execute("INSERT INTO metadata VALUES('summary',?)",(json.dumps(report),));db.commit();db.close()
    (output/'coverage.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (output/'catalog.json').write_text(json.dumps({'metrics':TEAM_CATALOG,'views':TEAM_VIEWS},indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--archive',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--player-database',type=Path)
    args=parser.parse_args();print(json.dumps(build(args.root,args.archive,args.output,args.player_database),indent=2))
