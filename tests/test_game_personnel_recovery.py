"""Offline acquisition/binding integration fixtures, never real active evidence."""
from copy import deepcopy
import hashlib
import json

import pytest

from research.game_personnel_recovery import build_game_personnel_view
from football_engine.team_profiles import content_hash

AS_OF='2026-11-04T18:00:00+00:00'
KICKOFF='2026-11-06T01:15:00+00:00'


@pytest.fixture
def inputs(tmp_path):
    def source(value, kind, team, reference):
        raw=value.encode() if isinstance(value,str) else json.dumps(value).encode()
        digest=hashlib.sha256(raw).hexdigest(); path=tmp_path/digest;path.write_bytes(raw)
        return dict(kind=kind,team_id=team,reference=reference,resolved_reference=reference,
                    http_status=200,observed_at='2026-11-04T17:00:00+00:00',
                    archive_file=str(path),sha256=digest)
    schedule={'events':[{'id':'401873037','date':KICKOFF,'season':{'year':2026,'type':2},
        'week':{'number':9},'competitions':[{'competitors':[{'team':{'abbreviation':t}} for t in ('BAL','JAX')],
                                           'status':{'type':{'state':'pre'}}}]}]}
    ss=source(schedule,'schedule','BAL','https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=20261105')
    state={'upcoming_games':{t:dict(team_id=t,opponent_id=o,game_id='401873037',kickoff=KICKOFF,
        week=9,season=2026,season_type=2,schedule_source=ss) for t,o in [('BAL','JAX'),('JAX','BAL')]}}
    profiles={t:dict(team_id=t,as_of=AS_OF,version='fixture',personnel={'players':[
        dict(player='Same Name',position='QB',player_reference=f'https://www.{host}.com/team/players-roster/fixture/',
             roster_sections=['Practice Squad'],depth_roles=[],provider_identities={'espn_id':'FAKE'})]})
        for t,host in [('BAL','baltimoreravens'),('JAX','jaguars')]}
    athlete={'id':'123','uid':'s:20~l:28~a:123','displayName':'Same Name','position':{'abbreviation':'QB'},
             'links':[{'href':'https://www.espn.com/nfl/player/_/id/123/fixture'}],'injuries':[],
             'status':{'type':'active'}}
    provider={'team':{'abbreviation':'BAL'},'season':{'year':2026},'athletes':[{'items':[athlete]}]}
    ps=source(provider,'espn_roster','BAL','https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/bal/roster')
    html='<h2>Practice Squad</h2><table><thead><tr><th>Player</th><th>Pos</th></tr></thead><tbody><tr><td><a href="/team/players-roster/fixture/">Same Name</a></td><td>QB</td></tr></tbody></table>'
    cs=source(html,'roster','BAL','https://www.baltimoreravens.com/team/players-roster/')
    for profile in profiles.values():
        profile['version']=content_hash({k:v for k,v in profile.items() if k!='version'})
    return state,profiles,[ps,cs],source


def build(inputs):
    return build_game_personnel_view(*inputs[:3],game_id='401873037',as_of=AS_OF)


def test_real_parser_integration_and_no_name_join(inputs):
    before=deepcopy(inputs[:3]); result=build(inputs)
    team=result['teams']['BAL']; p=team['club_players'][0]
    assert team['game_id']=='401873037' and team['kickoff']==KICKOFF
    assert len(p['roster_evidence'])==1
    assert team['provider_players'][0]['espn_id']=='123'
    assert p['provider_identities']=={}  # identical name is insufficient
    assert p['game_day_eligibility']=='UNKNOWN'
    assert not result['betting_ready'] and not result['game_day_active_proof']
    assert inputs[:3]==before


def test_explicit_crosslink_recovers_id_not_clearance(inputs):
    url=inputs[1]['BAL']['personnel']['players'][0]['player_reference']
    inputs[2].append(inputs[3]('<a href="https://www.espn.com/nfl/player/_/id/123/fixture">profile</a>',
                              'identity_profile','BAL',url))
    team=build(inputs)['teams']['BAL']
    assert team['club_players'][0]['provider_identities']=={'espn_id':'123'}
    assert team['club_players'][0]['game_day_eligibility']=='UNKNOWN'
    assert team['provider_players'][0]['club_player_reference']==url.rstrip('/')
    evidence=team['club_players'][0]['identity_mapping_evidence'][0]
    assert evidence['club_sha256']==inputs[2][-1]['sha256']
    assert evidence['provider_sha256']==inputs[2][0]['sha256']
    assert evidence['anchor_href']=='https://www.espn.com/nfl/player/_/id/123/fixture'


def test_replay_deterministic(inputs):
    assert build(inputs)==build(inputs)


def test_tampered_archive_rejected(inputs):
    inputs[2][0]['sha256']='0'*64
    result=build(inputs)
    assert result['teams']['BAL']['provider_players']==[]
    assert result['source_diagnostics'][0]['issue']=='archive hash mismatch'


def test_schedule_tamper_rejected(inputs):
    inputs[0]['upcoming_games']['BAL']['schedule_source']['sha256']='0'*64
    with pytest.raises(ValueError,match='hash'):build(inputs)


@pytest.mark.parametrize('field,value',[('kickoff','2026-11-07T01:15:00+00:00'),('week',10),('opponent_id','NE')])
def test_wrong_binding_rejected(inputs,field,value):
    inputs[0]['upcoming_games']['BAL'][field]=value
    with pytest.raises(ValueError):build(inputs)


def test_postkickoff_rejected(inputs):
    with pytest.raises(ValueError,match='precede'):build_game_personnel_view(*inputs[:3],game_id='401873037',as_of=KICKOFF)


def test_missing_evidence_is_explicit(inputs):
    inputs[2].clear(); t=build(inputs)['teams']['BAL']
    assert t['missingness']['club_missing_provider_id']==1
    assert 'DEPTH_SOURCE_ROW_UNVERIFIED' in t['club_players'][0]['diagnostics']
    assert not t['market_dependencies']['all_reserves_active_required']


def test_future_observation_rejected(inputs):
    inputs[2][0]['observed_at']='2026-11-04T19:00:00+00:00'
    r=build(inputs)
    assert not r['teams']['BAL']['provider_players']
    assert any('cutoff' in d['issue'] for d in r['source_diagnostics'])


def test_provider_uid_mismatch_rejected(inputs):
    ps=inputs[2][0]; data=json.loads(__import__('pathlib').Path(ps['archive_file']).read_bytes())
    data['athletes'][0]['items'][0]['uid']='s:20~l:28~a:999'
    inputs[2][0]=inputs[3](data,'espn_roster','BAL',ps['reference'])
    r=build(inputs)
    assert not r['teams']['BAL']['provider_players']
    assert any('cross-link' in d['issue'] for d in r['source_diagnostics'])


def test_wrong_team_provider_rejected(inputs):
    inputs[2][0]['team_id']='JAX'
    assert not build(inputs)['teams']['JAX']['provider_players']


def test_profile_different_game_rejected(inputs):
    inputs[1]['BAL']['game_id']='different'
    with pytest.raises(ValueError,match='another game'):build(inputs)


def test_explicit_links_still_reject_provider_collision(inputs):
    player=inputs[1]['BAL']['personnel']['players'][0]
    other=deepcopy(player);other['player_reference']=player['player_reference'].replace('/fixture/','/other/')
    inputs[1]['BAL']['personnel']['players'].append(other)
    profile=inputs[1]['BAL']
    profile['version']=content_hash({k:v for k,v in profile.items() if k!='version'})
    for p in (player,other):
        inputs[2].append(inputs[3]('<a href="https://www.espn.com/nfl/player/_/id/123/fixture">profile</a>',
                                  'identity_profile','BAL',p['player_reference']))
    t=build(inputs)['teams']['BAL']
    assert all(not p['provider_identities'] for p in t['club_players'])
    assert all('CLUB_PROVIDER_ID_COLLISION' in p['diagnostics'] for p in t['club_players'])


@pytest.mark.parametrize('tamper',['content','version','missing_version'])
def test_profile_content_version_tamper_rejected(inputs,tamper):
    profile=inputs[1]['BAL']
    if tamper=='content':
        profile['personnel']['players'][0]['position']='RB'
    elif tamper=='version':
        profile['version']='0'*64
    else:
        del profile['version']
    with pytest.raises(ValueError,match='profile content hash/version mismatch'):
        build(inputs)
