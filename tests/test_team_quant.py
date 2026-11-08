import json
import sqlite3

import pytest

from football_engine.team_metrics import TEAM_CATALOG,TEAM_VIEWS,LIBRARY,direction
from football_engine.team_quant import TeamQuantStore,RULES,competitive,raw_play,qualifies,field,gap
from football_engine.team_profiles import attach_team_quant,content_hash


def test_all_requested_slots_categories_and_lenses_are_present():
    assert len(TEAM_CATALOG)==401 and len(TEAM_VIEWS)==24 and len(LIBRARY)==18
    assert [len(x[2].split('|')) for x in LIBRARY.values()]==[22,25,24,20,20,24,22,24,22,22,24,30,20,20,20,18,20,24]
    assert set(RULES)<={m['id'] for m in TEAM_CATALOG}


@pytest.mark.parametrize('play_type,flag',[('qb_kneel','qb_kneel'),('qb_spike','qb_spike')])
def test_nfl_raw_production_keeps_kneels_and_spikes_but_epa_excludes_them(play_type,flag):
    play={'play_type':play_type,flag:1,'yards_gained':-1,'two_point_attempt':0}
    assert raw_play(play) and not competitive(play)


def test_known_incompletion_is_zero_yards_not_a_missing_attempt():
    assert field({'incomplete_pass':1},'passing_yards')==0
    assert field({'complete_pass':1},'passing_yards') is None


def test_sacks_are_not_throw_attempts_or_pressure_measurements():
    play={'play_type':'pass','pass_attempt':1,'sack':1,'qb_dropback':1}
    assert not qualifies(play,'throw') and qualifies(play,'dropback')
    assert 'passing.pressure_rate_allowed' not in RULES


def test_team_rushing_uses_full_play_production_including_lateral_yards():
    play={'play_type':'run','rush_attempt':1,'rushing_yards':0,'yards_gained':1}
    rule=RULES['rushing.rushing_yards_game']
    assert qualifies(play,rule[1]) and field(play,rule[3])==1


def test_turnover_and_forced_fumble_credit_uses_reported_teams():
    # After an interception, the defender fumbles back to the original offense.
    play={'posteam':'CHI','defteam':'SEA','interception':1,'fumble_lost':1,'fumbled_1_team':'SEA','fumble_recovery_1_team':'CHI','fumble_forced':1,'forced_fumble_player_1_team':'CHI'}
    assert field(play,'takeaway')==1
    assert field(play,'defense_forced_fumble')==0
    assert field({'interception':0,'fumble_lost':1},'takeaway') is None


def test_net_passing_does_not_credit_scramble_yards():
    assert field({'qb_scramble':1,'yards_gained':15},'net_passing_yards')==0
    assert field({'sack':1,'yards_gained':-8},'net_passing_yards')==-8


def test_long_rest_is_not_silently_classified_as_a_bye():
    assert 'environment.long_rest_epa' in RULES
    assert 'environment.post_bye_epa' not in RULES


def test_actual_observed_calendar_rest_does_not_use_provider_week_one_default(quant):
    result=quant.value('CHI',2026,'environment.days_since_previous_game')
    assert result['value']==1 and result['sample_games']==5


def test_gap_direction_never_manufactures_a_run_concept():
    assert gap({'run_location':'left','run_gap':'tackle'})=='left_tackle'
    assert gap({'run_location':'middle'})=='middle'
    assert gap({'run_location':'right','run_gap':'unknown'}) is None
    assert 'rushing.outside_zone_epa' not in RULES


def test_defensive_direction_is_favorable_when_allowed_epa_is_lower():
    assert direction('defense','EPA allowed / play')=='lower'
    assert direction('environment','Home defensive EPA')=='lower'
    assert direction('situational','Two-minute defense EPA')=='lower'
    assert direction('pass_rush','Sack rate')=='higher'
    assert direction('passing','Sack rate')=='lower'
    assert direction('pass_defense','Explosive pass rate allowed')=='lower'
    assert direction('pass_defense','First-down pass rate allowed')=='lower'
    assert direction('scheme','Play-action frequency')=='context'


@pytest.fixture
def quant(tmp_path):
    path=tmp_path/'teams.sqlite';db=sqlite3.connect(path)
    db.executescript('''CREATE TABLE sources(id INTEGER,reference TEXT,sha256 TEXT,observed_at TEXT,path TEXT,kind TEXT);
    CREATE TABLE games(id TEXT,season INTEGER,week INTEGER,game_date TEXT,home TEXT,away TEXT,home_score REAL,away_score REAL,data TEXT,source_id INTEGER);
    CREATE TABLE plays(game_id TEXT,play_id TEXT,season INTEGER,posteam TEXT,defteam TEXT,source_id INTEGER,data TEXT);
    CREATE TABLE team_context(team TEXT,data TEXT,source_id INTEGER);CREATE TABLE metadata(key TEXT,value TEXT);''')
    db.execute('INSERT INTO sources VALUES(1,?,?,?,?,?)',('fixture:plays','a'*64,'2026-11-03T12:00:00Z','fixture','pbp'))
    teams=['CHI','SEA','BUF','BAL','NE','MIA','KC','DAL']
    db.execute('INSERT INTO metadata VALUES(?,?)',('summary',json.dumps({'teams':8,'plays':2400})))
    for team in teams:db.execute('INSERT INTO team_context VALUES(?,?,1)',(team,json.dumps({'team_id':team,'season_year':2026,'as_of':'2026-11-03T12:00:00Z'})))
    for week in range(1,7):
        for index in range(0,len(teams),2):
            home,away=teams[index:index+2];gid=f'g{week}-{index}';date=f'2026-09-{week+1:02d}'
            db.execute('INSERT INTO games VALUES(?,?,?,?,?,?,?,?,?,1)',(gid,2026,week,date,home,away,24,14,json.dumps({'home_rest':'7','away_rest':'7','roof':'outdoors'})))
            for offense,defense in [(home,away),(away,home)]:
                for i in range(50):
                    throw=i%2==0;epa=.02*teams.index(offense);p={'play_type':'pass' if throw else 'run','two_point_attempt':0,'qb_kneel':0,'qb_spike':0,'qb_dropback':int(throw),'pass_attempt':int(throw),'rush_attempt':int(not throw),'qb_scramble':0,'sack':0,'complete_pass':int(throw),'passing_yards':5 if throw else 0,'rushing_yards':0 if throw else 5,'yards_gained':5,'epa':epa,'down':1,'qtr':1 if i<25 else 4,'score_differential':7 if i<25 else -7,'air_yards':5 if throw else None,'first_down':0,'touchdown':0,'receiver_player_id':f'player{i%4}' if throw else None,'fixed_drive':str(i//5),'fixed_drive_result':'Punt','posteam_score':0,'posteam_score_post':0,'yardline_100':50,'source_id':1}
                    db.execute('INSERT INTO plays VALUES(?,?,?,?,?,?,?)',(gid,offense+str(i),2026,offense,defense,1,json.dumps(p)))
    db.commit();db.close();return TeamQuantStore(path)


def test_weighted_epa_and_as_of_boundaries(quant):
    value=quant.value('SEA',2026,'offense.epa_offensive_play')
    assert value['value']==.02 and value['sample_plays']==300 and value['sample_games']==6
    assert quant.value('SEA',2026,'offense.epa_offensive_play',as_of='2026-11-02T12:00:00Z')['value'] is None


def test_defensive_leading_view_reverses_possession_score(quant):
    records,_=quant.select('CHI',2026,'defense','competitive','leading',None)
    assert records and all(p['score_differential']<0 for p in records)


def test_period_splits_do_not_relabel_full_game_or_drive_totals(quant):
    assert quant.value('CHI',2026,'offense.points_per_game','first_half')['value'] is None
    assert quant.value('CHI',2026,'offense.points_drive','leading')['value'] is None
    assert quant.value('SEA',2026,'offense.epa_offensive_play','first_half')['sample_plays']==150


def test_directional_percentile_and_percentile_view_match(quant):
    rank=quant.percentile('CHI',2026,'defense.epa_allowed_play')
    assert rank['value']>50 and rank['meaning']=='Higher is better'
    view=quant.value('CHI',2026,'defense.epa_allowed_play','percentile')
    assert view['value']==rank['value'] and view['unit']=='percentile'
    assert quant.percentile('CHI',2026,'defense.epa_allowed_play','percentile')==rank


def test_unsupported_charting_and_gap_lenses_do_not_fall_back(quant):
    assert quant.value('CHI',2026,'coverage.cover_0_usage')['value'] is None
    assert quant.value('CHI',2026,'offense.epa_offensive_play','man')['value'] is None
    assert quant.gap_matrix('CHI',2026,view='man')['rows']==[]


def test_game_cluster_interval_is_descriptive_and_reproducible(quant):
    ci=quant.confidence('SEA',2026,'offense.epa_offensive_play')
    assert ci['lower']==ci['upper']==.02 and ci['game_clusters']==6
    assert quant.confidence('SEA',2026,'offense.epa_offensive_play')==ci
    assert quant.value('SEA',2026,'offense.epa_offensive_play','confidence')['confidence_interval']==ci


def test_season_confidence_is_not_substituted_for_changes_or_ranks(quant):
    for view in ('percentile','yoy','three_year'):
        assert quant.confidence('SEA',2026,'offense.epa_offensive_play',view)['status']=='UNAVAILABLE'


def test_kick_only_scoring_drive_is_not_discarded(quant):
    play={'play_type':'field_goal','field_goal_attempt':1,'fixed_drive':'FGONLY','fixed_drive_result':'Field goal','posteam_score':0,'posteam_score_post':3,'epa':.4}
    with sqlite3.connect(quant.path) as db:db.execute('INSERT INTO plays VALUES(?,?,?,?,?,?,?)',('g1-0','FG',2026,'CHI','SEA',1,json.dumps(play)))
    drive=next(d for d in quant.drives('CHI',2026) if d['drive']=='FGONLY')
    assert drive['points']==3 and drive['epa']==.4 and drive['td']==0


def test_shared_player_events_respect_source_knowledge_time(quant):
    with sqlite3.connect(quant.path) as db:
        db.execute('CREATE TABLE player_events(player_id TEXT,game_id TEXT,play_id TEXT,role TEXT,name_as_reported TEXT,source_id INTEGER)')
        db.execute('INSERT INTO player_events VALUES(?,?,?,?,?,?)',('p','g1-0','CHI0','passer','Fixture',1))
    assert quant.player_summary('p',2026)['metrics']['passing_yards']['value']==5
    assert quant.player_summary('p',2026,as_of='2026-11-02T12:00:00Z')['status']=='UNAVAILABLE'


def test_binding_quant_requires_matching_identity_season_and_available_sources():
    profile={'team_id':'CHI','season_year':2026,'as_of':'2026-11-03T13:00:00Z','game_observations':[{'game_id':'1','season':2026}]}
    profile['version']=content_hash(profile)
    data={'team':'CHI','season':2026,'view':'season','metrics':[],'coverage':{},'sources':[{'observed_at':'2026-11-03T12:00:00Z'}],
          'games':[{'data':{'espn':'1'}}],'gap_offense':{},'gap_defense':{},'model_status':'NOT_CALIBRATED','historically_pregame_certified':False}
    bound=attach_team_quant(profile,data)
    assert bound['version']!=profile['version'] and not bound['team_quant']['missing_current_season_game_ids']
    assert bound['team_quant']['forecast_available'] is False
    with pytest.raises(ValueError):attach_team_quant(profile,{**data,'team':'SEA'})
    with pytest.raises(ValueError):attach_team_quant(profile,{**data,'sources':[{'observed_at':'2026-11-04T12:00:00Z'}]})


def test_matchup_does_not_invent_edges_win_probabilities_or_scores(quant):
    result=quant.matchup('CHI','SEA',2026)
    assert result['win_probability'] is None and result['score_distribution'] is None
    assert all(factor['predicted_edge'] is None for item in result['comparisons'] for factor in item['factors'])
    with pytest.raises(ValueError):quant.matchup('CHI','CHI',2026)


def test_historical_profile_does_not_inherit_current_staff(quant):
    old=quant.profile('CHI',2024)
    assert old['context']['coaching'] is None and old['context']['personnel'] is None


@pytest.mark.parametrize('category,label,expected',[
    ('pass_rush','Pressure-to-sack conversion','higher'),
    ('pass_rush','Time to pressure','lower'),
    ('line','Time to pressure allowed','higher'),
    ('special','Punt touchback rate','lower'),
])
def test_pressure_and_punt_directions(category,label,expected):
    assert direction(category,label)==expected


def test_catalog_distinguishes_counts_percentages_and_percentage_points():
    metrics={m['id']:m for m in TEAM_CATALOG}
    assert metrics['offense.positive_epa_play']['unit']=='%'
    assert metrics['situational.red_zone_stops_forced']['unit']=='count'
    assert metrics['passing.passing_touchdowns']['unit']=='count'
    assert metrics['coaching.pass_rate_over_expected']['unit']=='pp'


@pytest.mark.parametrize('air,expected',[(-5,'short'),(0,'short'),(9,'short'),(10,'intermediate'),(19,'intermediate'),(20,'deep')])
def test_pass_depth_buckets_partition_all_known_air_yards(air,expected):
    assert {name for name in ('short','intermediate','deep') if field({'air_yards':air},name)}=={expected}


def test_net_passing_rules_use_matching_dropback_definitions():
    for metric in ('passing.net_yards_dropback','pass_defense.net_passing_yards_dropback_allowed'):
        assert RULES[metric][1:4]==('dropback','mean','net_passing_yards')
    assert field({'qb_scramble':1,'yards_gained':25},'explosive_pass')==0
    assert field({'incomplete_pass':1,'passing_yards':None},'passing_yards')==0


def test_turnovers_use_authoritative_store_teams_even_when_json_omits_them(quant):
    play={'play_type':'run','interception':0,'fumble_lost':1,'fumbled_1_team':'CHI','fumble_recovery_1_team':'SEA'}
    with sqlite3.connect(quant.path) as db:
        db.execute('INSERT INTO plays VALUES(?,?,?,?,?,?,?)',('g1-0','turnover',2026,'CHI','SEA',1,json.dumps(play)))
    stored=next(p for p in quant.plays('SEA',2026,'defense') if p['play_id']=='turnover')
    assert field(stored,'takeaway')==1
    assert field(play,'takeaway') is None
    assert field({'interception':None,'fumble_lost':0},'takeaway') is None


def test_success_is_percentage_of_known_epa_and_missing_fields_do_not_qualify(quant):
    with sqlite3.connect(quant.path) as db:
        row=db.execute("SELECT data FROM plays WHERE game_id='g1-0' AND play_id='CHI0'").fetchone()
        play=json.loads(row[0]);play.pop('epa')
        db.execute("UPDATE plays SET data=? WHERE game_id='g1-0' AND play_id='CHI0'",(json.dumps(play),))
    result=quant.value('CHI',2026,'offense.offensive_success_rate')
    assert result['value']==0 and result['unit']=='%'
    assert result['sample_plays']==299 and result['eligible_observations']==300
    assert not result['qualified'] and not result['complete_fields']
    assert quant.percentile('CHI',2026,'offense.offensive_success_rate') is None
    assert field({'epa':0},'defensive_success')==1
    assert field({'epa':0},'success_positive')==0


def test_contrast_sample_gate_counts_only_plays_with_epa(quant):
    with sqlite3.connect(quant.path) as db:
        for row in db.execute("SELECT game_id,play_id,data FROM plays WHERE posteam='CHI'").fetchall():
            play=json.loads(row[2]);play['shotgun']=int(play['pass_attempt']==1)
            if row[0]!='g1-0':play.pop('epa')
            db.execute('UPDATE plays SET data=? WHERE game_id=? AND play_id=?',(json.dumps(play),row[0],row[1]))
    result=quant.value('CHI',2026,'scheme.shotgun_vs_under_center_epa')
    assert result['value']==0 and result['sample_plays']==50 and result['sample_games']==1
    assert not result['qualified']


def test_per_game_interval_does_not_omit_zero_event_denominator_games(quant):
    with sqlite3.connect(quant.path) as db:
        db.execute("DELETE FROM plays WHERE game_id='g1-0' AND posteam='CHI' AND json_extract(data,'$.pass_attempt')=1")
    result=quant.value('CHI',2026,'passing.passing_yards_game')
    assert result['value']==round(625/6,5) and result['denominator_games']==6
    assert result['sample_games']==5 and result['qualified']
    interval=quant.confidence('CHI',2026,'passing.passing_yards_game')
    assert interval['status']=='UNAVAILABLE' and 'denominator games' in interval['reason']


def test_unavailable_rank_does_not_inherit_qualified_calculated_status(quant,monkeypatch):
    monkeypatch.setattr(quant,'overview',lambda:{'teams':['CHI','SEA']})
    result=quant.value('CHI',2026,'offense.epa_offensive_play','percentile')
    assert result['value'] is None and result['status']=='UNAVAILABLE' and not result['qualified']


def test_gap_rates_exclude_missing_yards_and_gate_known_samples(quant,monkeypatch):
    plays=[{'game_id':'g1','run_location':'middle','yards_gained':10,'epa':1},
           {'game_id':'g2','run_location':'middle','yards_gained':None,'epa':None}]
    monkeypatch.setattr(quant,'select',lambda *args:(plays,2))
    row=next(r for r in quant.gap_matrix('CHI',2026)['rows'] if r['gap']=='middle')
    assert row['explosive_rate']==100 and row['success_rate']==100
    assert row['plays']==2 and row['yards_sample']==row['epa_sample']==1 and row['small_sample']


@pytest.fixture
def quant_history(quant):
    with sqlite3.connect(quant.path) as db:
        games=db.execute("SELECT * FROM games WHERE home='CHI'").fetchall()
        plays=db.execute("SELECT * FROM plays WHERE posteam='CHI'").fetchall()
        weeks={g[0]:g[2] for g in games}
        for year,slope in ((2024,.2),(2025,.1)):
            for game in games:
                row=list(game);row[0]=f'{year}-{game[0]}';row[1]=year;row[3]=game[3].replace('2026',str(year))
                db.execute('INSERT INTO games VALUES(?,?,?,?,?,?,?,?,?,?)',row)
            for play in plays:
                row=list(play);row[0]=f'{year}-{play[0]}';row[2]=year
                data=json.loads(row[6]);data['epa']=slope*weeks[play[0]];row[6]=json.dumps(data)
                db.execute('INSERT INTO plays VALUES(?,?,?,?,?,?,?)',row)
    return quant


@pytest.mark.parametrize('view,expected',[('yoy',-.1),('three_year',-.2)])
def test_cross_season_trend_change_compares_slopes_in_each_season(quant_history,view,expected):
    result=quant_history.value('CHI',2026,'trends.season_epa_trend_slope',view)
    assert result['value']==pytest.approx(expected) and result['status']=='DESCRIPTIVE_CHANGE'
    assert result['qualified']


@pytest.mark.parametrize('view',['yoy','three_year'])
def test_cross_season_change_requires_all_season_sample_gates(quant_history,view):
    with sqlite3.connect(quant_history.path) as db:
        db.execute("DELETE FROM plays WHERE season=2025 AND game_id NOT IN ('2025-g1-0','2025-g2-0')")
    result=quant_history.value('CHI',2026,'offense.epa_offensive_play',view)
    assert result['value'] is None and not result['qualified'] and result['status']=='UNAVAILABLE'
    assert 'Every compared season' in result['reason']


@pytest.mark.parametrize('sparse_arm',[False,True])
def test_contrast_requires_each_arm_to_pass_plays_and_games_gates(quant,sparse_arm):
    with sqlite3.connect(quant.path) as db:
        for game_id,play_id,raw in db.execute("SELECT game_id,play_id,data FROM plays WHERE posteam='CHI'").fetchall():
            play=json.loads(raw)
            play['shotgun']=int(play_id!='CHI0') if sparse_arm else play['pass_attempt']
            db.execute('UPDATE plays SET data=? WHERE game_id=? AND play_id=?',(json.dumps(play),game_id,play_id))
    result=quant.value('CHI',2026,'scheme.shotgun_vs_under_center_epa')
    assert result['value']==0 and result['sample_plays']==300
    assert result['qualified'] is (not sparse_arm)
    assert result['contrast_samples']['0']['plays']==(6 if sparse_arm else 150)
    assert result['direction']=='context'


def test_one_score_win_cohort_excludes_other_margins_without_marking_missing(quant):
    with sqlite3.connect(quant.path) as db:
        db.execute("UPDATE games SET away_score=21 WHERE home='CHI' AND week<=4")
    result=quant.value('CHI',2026,'trends.one_score_win_rate')
    assert result['value']==100 and result['sample_games']==4
    assert result['qualified'] and result['complete_fields'] and result['eligible_observations']==4
