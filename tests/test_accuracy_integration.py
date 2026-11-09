"""Synthetic adversarial fixtures, never historical performance evidence."""
import copy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json

import pytest

from football_engine.accuracy import run_auditors
from football_engine.engine import AUDITORS, evaluate_ticket
from football_engine.ledger import Ledger, audit_ledger_evidence
from football_engine.snapshot_evidence import audit_opportunity_evidence
from research.accuracy_backtest import audit_backtest_evidence, _digest
from test_engine import NOW, SOURCE, leg, snapshot


def test_missing_auditor_and_exceptions_block_without_erasing_other_results():
    good=lambda:dict(status='PASS',reasons=[],evidence={'fixture':True})
    checks={n:good for n in AUDITORS if n!='ledger'}
    report=run_auditors(checks,as_of=NOW)
    assert report['status']=='BLOCKED' and len(report['auditors'])==7
    assert report['auditors']['source']['status']=='PASS'
    assert report['auditors']['ledger']['evidence']['check_completed'] is False


@pytest.mark.parametrize('change',[{'line':float('nan')},{'current':float('inf')},{'odds_decimal':True}])
def test_nonfinite_and_boolean_markets_never_approve(change):
    decision=evaluate_ticket(snapshot(),(replace(leg(),**change),))
    assert not decision.approved and decision.joint_probability is None
    assert len(decision.audit)==7 and len(decision.skills)==8


def test_truthy_roster_active_string_cannot_approve():
    game=snapshot();player=replace(game.players['p1'],active='Active')
    assert not evaluate_ticket(replace(game,players={'p1':player}),(leg(),)).approved


def test_missing_or_current_vintage_backtest_evidence_blocks():
    assert audit_backtest_evidence({},NOW)['status']=='BLOCKED'
    assert audit_backtest_evidence({'current_vintage':True},NOW)['status']=='BLOCKED'
    assert audit_opportunity_evidence({},NOW)['status']=='BLOCKED'
    assert audit_ledger_evidence({},NOW)['status']=='BLOCKED'


def test_ledger_will_not_score_broken_hash_chain(tmp_path):
    ledger=Ledger(tmp_path/'ledger.sqlite')
    from test_ledger import pregame_payload
    payload = pregame_payload()
    ledger.append('t','pregame',payload)
    ledger.connection.execute('DROP TRIGGER immutable_events_update')
    ledger.connection.execute("UPDATE events SET payload='{}'");ledger.connection.commit()
    assert ledger.prospective_report()['status']=='INVALID_LEDGER'


def backtest_manifest(tmp_path):
    def archive(name,payload,when='2026-01-02T00:00:00+00:00'):
        path=tmp_path/(name+'.json');path.write_text(json.dumps(payload,sort_keys=True))
        return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),reference='fixture:'+name,
                    archive_reference='fixture-archive:'+name,published_at=when,version_available_at=when,
                    observed_at=when,historical_version_verified=True)
    ticket=dict(ticket_id='t',game_id='g',kickoff='2026-01-04T00:00:00+00:00',frozen_at='2026-01-03T00:00:00+00:00',
                legs=[dict(leg_id='l',player_id='p',team_id='CHI',market='rushing_yards',line=40)],sources=[])
    fold=dict(fold_id='f',train_start='2025-01-01T00:00:00+00:00',train_end='2025-12-31T00:00:00+00:00',
        trained_at='2026-01-02T00:00:00+00:00',test_start='2026-01-03T00:00:00+00:00',
        test_end='2026-01-05T00:00:00+00:00',test_game_ids=['g'],
        train_games=[dict(game_id='past',kickoff='2025-11-01T00:00:00+00:00',labels_available_at='2025-11-02T00:00:00+00:00')])
    model={k:copy.deepcopy(fold[k]) for k in ('fold_id','train_start','train_end','trained_at','train_games','test_game_ids')}
    model['rules_frozen_at']='2026-01-01T00:00:00+00:00'
    fold['model']=archive('model',model)
    ticket['model_sha256']=fold['model']['sha256']
    for purpose in ['forecast','roster','quote','model_inputs']:
        payload=dict(ticket_id='t',game_id='g',leg_ids=['l'],legs=copy.deepcopy(ticket['legs']),
            observed_at='2026-01-02T00:00:00+00:00',model_sha256=ticket['model_sha256'],
            players=[dict(player_id='p',team_id='CHI',active=True)],probabilities={'l':.6},decimal_odds=2)
        ticket['sources'].append(dict(archive(purpose,payload),purpose=purpose,leg_ids=['l']))
    settlement=dict(ticket_id='t',ticket_sha256=_digest(ticket),leg_ids=['l'],outcome='won',final=True,authoritative=True,basis='full_ticket',settled_at='2026-01-05T00:00:00+00:00')
    settlement['source']=archive('settlement',copy.deepcopy(settlement),settlement['settled_at'])
    return dict(kind='prospective_walk_forward',historically_frozen=True,postgame_selected=False,current_vintage=False,
        rules_frozen_at='2026-01-01T00:00:00+00:00',price_available=True,roi_available=False,tickets=[ticket],
        selection_archive=archive('selection',[ticket]),settlements=[settlement],folds=[fold])


def test_backtest_requires_actual_archives_frozen_selection_and_active_roster(tmp_path):
    manifest=backtest_manifest(tmp_path);cutoff='2026-01-06T00:00:00+00:00'
    assert audit_backtest_evidence(manifest,cutoff)['status']=='PASS'
    changed=copy.deepcopy(manifest);changed['tickets'][0]['legs'][0]['player_id']='unrecorded'
    assert audit_backtest_evidence(changed,cutoff)['status']=='BLOCKED'
    manifest['settlements'][0]['outcome']='lost'
    assert audit_backtest_evidence(manifest,cutoff)['status']=='BLOCKED'


def test_official_table_logo_separates_unlinked_opponent_rows():
    from research.source_gap_repair import own_injury_rows
    def table(code,name):
        return f'<div class="nfl-o-injury-report__container"><div class="nfl-o-injury-report__club-logo"><img data-src="https://static.www.nfl.com/clubs/logos/{code}"></div><table><tr><th>Player</th><th>Injury</th></tr><tr><td>{name}</td><td>Knee</td></tr></table></div>'
    rows=own_injury_rows((table('HOU','Texan')+table('NYG','Giant')).encode(),'https://www.houstontexans.com/team/injury-report/')
    assert len(rows)==1 and rows[0]['columns']['Player']=='Texan'


def test_settlement_cannot_rewrite_frozen_forecast_or_settle_before_game(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    from test_ledger import fresh_snapshot
    ledger=Ledger(tmp_path/'ledger.sqlite');game=fresh_snapshot()
    decision=ledger.evaluate_and_record('t',game,(replace(leg(),source=game.source),))
    payload=dict(status='won',source='fixture:final',legs=[dict(status='won',player_id='p1',team_id='CHI',market='rushing_yards',observed=69,threshold=40,odds_decimal=2.5,predicted_probability=decision.joint_probability,source='fixture:final',error_category='none')])
    with pytest.raises(ValueError,match='precede kickoff'):ledger.append('t','settlement',payload)
    monkeypatch.setattr('football_engine.ledger._now',lambda:game.kickoff+timedelta(hours=3))
    payload['legs'][0]['predicted_probability']=.123
    with pytest.raises(ValueError,match='rewrites frozen'):ledger.append('t','settlement',payload)


def test_final_box_game_identity_is_exact_and_nonfinite_values_stay_unknown(tmp_path,monkeypatch):
    from football_engine.settlement import settle_recorded_ticket,_final_totals
    from test_settlement import final_box,proof
    ledger=Ledger(tmp_path/'ledger.sqlite')
    with monkeypatch.context() as clock:
        clock.setattr('football_engine.ledger._now',lambda:NOW)
        ledger.evaluate_and_record('t',replace(snapshot(),game_id='123'),(leg(),))
    box=final_box();source=proof(box);source['reference']+='4'
    with pytest.raises(ValueError,match='match game'):settle_recorded_ticket(ledger,'t',box,source)
    box['boxscore']['players'][0]['statistics'][0]['athletes'][0]['stats'][3]='nan'
    assert 'anytime_td' not in _final_totals(box)['p1']


def test_duplicate_final_player_rows_do_not_double_touchdown_credit():
    from football_engine.settlement import _final_totals
    from test_settlement import final_box
    box=final_box();athletes=box['boxscore']['players'][0]['statistics'][0]['athletes'];athletes.append(copy.deepcopy(athletes[0]))
    with pytest.raises(ValueError,match='duplicate'):_final_totals(box)


def test_unpublished_later_practice_days_stay_blank_not_dropped_or_healthy():
    from research.source_gap_repair import own_injury_rows
    raw=b'<table><tr><th>Player</th><th>Injury</th><th>Wed</th><th>Thu</th></tr><tr><td><a href="/team/players-roster/p/">Player</a></td><td>Knee</td><td>DNP</td><td></td></tr></table>'
    rows=own_injury_rows(raw,'https://club.test/team/injury-report/')
    assert len(rows)==1 and rows[0]['columns']['Wed']=='DNP' and rows[0]['columns']['Thu']==''


@pytest.fixture
def scoped_audit_files(tmp_path):
    """Synthetic schedule/profile archives; no forecast or readiness evidence."""
    from research.automatic_refresh import BASE, upcoming_game_context
    from research.source_based_parlays import DOMAINS
    from football_engine.team_profiles import content_hash
    runtime=tmp_path/'runtime';sources=runtime/'sources';sources.mkdir(parents=True)
    kickoff=(NOW+timedelta(hours=3)).isoformat()
    event=dict(id='fixture-game',date=kickoff,week={'number':1},season={'year':NOW.year,'type':2},
               competitions=[dict(status={'type':{'completed':False}},
                                  competitors=[{'team':{'abbreviation':t}} for t in ('JAX','BAL')])])
    archive=sources/'schedule.json';archive.write_text(json.dumps({'events':[event]}))
    source=dict(reference=BASE+'/scoreboard',archive_file=str(archive),
                sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),observed_at=NOW.isoformat())
    upcoming={r['team_id']:r for r in upcoming_game_context(event,source,NOW)}
    profiles={}
    for team in DOMAINS:
        p=dict(team_id=team,game_id='fixture-game',kickoff=kickoff,as_of=NOW.isoformat())
        profiles[team]={**p,'version':content_hash(p)}
    profile=runtime/'profiles.json';profile.write_text(json.dumps({'profiles':profiles}))
    state=dict(latest_profiles=str(profile),upcoming_games=upcoming,
               context_gaps=[{'team':t,'kind':'fixture-gap'} for t in ('HOU','DAL')])
    state_path=runtime/'state.json';state_path.write_text(json.dumps(state))
    config=tmp_path/'config.json';config.write_text(json.dumps({'runtime':str(runtime)}))
    return dict(config=config,state=state,state_path=state_path,profile=profile,archive=archive,
                tmp_path=tmp_path,runtime=runtime)


def run_scoped_fixture(files, **kwargs):
    from research.accuracy_swarm import run
    return run(files['config'],None,files['tmp_path']/'report',as_of=NOW,**kwargs)


def test_optional_scope_limits_only_sources_and_personnel(scoped_audit_files,monkeypatch):
    files=scoped_audit_files;seen=[]
    def personnel(profile,cutoff):
        seen.append(profile['team_id'])
        return dict(status='PASS',reasons=[],evidence={'synthetic':True})
    monkeypatch.setattr('football_engine.accuracy_personnel.audit_personnel',personnel)
    report=run_scoped_fixture(files,game_id='fixture-game',teams=['JAX','BAL'])
    assert seen==['JAX','BAL']
    assert report['scope']['profiles_selected']==['JAX','BAL']
    assert report['scope']['game_id']=='fixture-game'
    assert report['auditors']['source']['evidence']['profile_count']==2
    assert report['auditors']['source']['status']=='PASS'
    assert report['auditors']['roster']['status']=='PASS'
    assert report['status']=='BLOCKED' and report['betting_ready'] is False
    assert set(report['auditors'])==set(AUDITORS)
    assert all(report['auditors'][n]['status']=='BLOCKED' for n in ('market','remaining','scenario','correlation','ledger'))
    assert str(files['archive']) in {v['path'] for v in report['verified_artifacts']}


def test_default_still_audits_all_32_and_unrelated_gaps(scoped_audit_files):
    report=run_scoped_fixture(scoped_audit_files)
    assert len(report['scope']['profiles_selected'])==32
    assert report['auditors']['source']['evidence']['profile_count']==32
    assert report['auditors']['source']['status']=='BLOCKED'
    assert len(report['auditors']['roster']['evidence']['teams'])==32


@pytest.mark.parametrize('game_id,teams',[
    ('fixture-game',['JAX','JAX']),('fixture-game',['JAX','unknown']),
    ('wrong-game',['JAX','BAL']),(None,['JAX','BAL']),('fixture-game',None)])
def test_invalid_game_scope_rejected(scoped_audit_files,game_id,teams):
    with pytest.raises(ValueError):run_scoped_fixture(scoped_audit_files,game_id=game_id,teams=teams)


@pytest.mark.parametrize('damage',[
    'missing_schedule','wrong_opponent','hash','bytes','outside_path','archived_teams',
    'past_game','empty_profile','empty_profiles','profile_path','kickoff','duplicate_event'])
def test_scope_rejects_unverifiable_schedule_or_profiles(scoped_audit_files,damage):
    f=scoped_audit_files;state=f['state'];source=state['upcoming_games']['JAX']['schedule_source']
    if damage=='missing_schedule':state.pop('upcoming_games')
    elif damage=='wrong_opponent':state['upcoming_games']['JAX']['opponent_id']='HOU'
    elif damage=='hash':source['sha256']='0'*64
    elif damage=='bytes':f['archive'].write_text('{}')
    elif damage=='outside_path':
        outside=f['tmp_path']/'outside.json';outside.write_bytes(f['archive'].read_bytes())
        source['archive_file']=str(outside)
    elif damage in ('archived_teams','past_game','duplicate_event'):
        payload=json.loads(f['archive'].read_text());event=payload['events'][0]
        if damage=='archived_teams':event['competitions'][0]['competitors'][0]['team']['abbreviation']='HOU'
        elif damage=='past_game':event['date']=(NOW-timedelta(hours=1)).isoformat()
        else:payload['events'].append(copy.deepcopy(event))
        f['archive'].write_text(json.dumps(payload))
        for row in state['upcoming_games'].values():
            row['schedule_source']['sha256']=hashlib.sha256(f['archive'].read_bytes()).hexdigest()
    elif damage in ('empty_profile','empty_profiles'):
        payload=json.loads(f['profile'].read_text())
        if damage=='empty_profile':payload['profiles']['JAX']={}
        else:payload['profiles']={}
        f['profile'].write_text(json.dumps(payload))
    elif damage=='profile_path':
        outside=f['tmp_path']/'outside-profiles.json';outside.write_bytes(f['profile'].read_bytes())
        state['latest_profiles']=str(outside)
    elif damage=='kickoff':state['upcoming_games']['JAX']['kickoff']=(NOW+timedelta(days=1)).isoformat()
    f['state_path'].write_text(json.dumps(state))
    with pytest.raises(ValueError):run_scoped_fixture(f,game_id='fixture-game',teams=['JAX','BAL'])


def test_scoped_evidence_bindings_cannot_be_rewritten(scoped_audit_files):
    f=scoped_audit_files;evidence=f['tmp_path']/'evidence.json'
    evidence.write_text(json.dumps({'scope':{'game_id':'different-game','model_sha256':'a'*64}}))
    with pytest.raises(ValueError,match='evidence scope'):
        run_scoped_fixture(f,game_id='fixture-game',teams=['JAX','BAL'],evidence_path=evidence)


def test_scoped_sources_reject_paths_outside_source_roots(scoped_audit_files):
    from research.accuracy_swarm import source_audit
    from football_engine.team_profiles import content_hash
    f=scoped_audit_files;outside=f['tmp_path']/'foreign.json';outside.write_text('{}')
    p=dict(as_of=NOW.isoformat(),source=dict(reference='fixture:source',archive_file=str(outside),
           sha256=hashlib.sha256(outside.read_bytes()).hexdigest()))
    p['version']=content_hash(p)
    report=source_audit({'JAX':p},[f['runtime']/'sources'],NOW,[],restrict_paths=True)
    assert report['status']=='BLOCKED' and 'source archive outside source roots' in report['reasons']


def test_legacy_seed_archive_is_verified_inside_approved_root(scoped_audit_files):
    from football_engine.team_profiles import content_hash
    f=scoped_audit_files
    root=f['tmp_path']/'bears-seahawks-refresh-sources'
    root.mkdir()
    archive=root/'source.json'
    archive.write_text('{"fixture":"seed"}')
    payload=json.loads(f['profile'].read_text())
    p=payload['profiles']['JAX']
    p.pop('version')
    p['source']=dict(reference='fixture:seed',archive_file=str(archive),
                     sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    p['version']=content_hash(p)
    f['profile'].write_text(json.dumps(payload))
    report=run_scoped_fixture(f,game_id='fixture-game',teams=['JAX','BAL'])
    result=report['auditors']['source']
    assert result['status']=='PASS' and result['evidence']['source_hashes_verified']==1


def test_real_layout_output_snapshot_and_repaired_source_are_accepted_read_only(scoped_audit_files):
    from football_engine.team_profiles import content_hash
    f=scoped_audit_files;output=f['tmp_path']/'outputs'
    repaired=output/'gap-repair-current-injuries'/'sources';repaired.mkdir(parents=True)
    archive=repaired/'injury.json';archive.write_text('{"fixture":"injury"}')
    folder=output/'gap-repair-integration-20261104';folder.mkdir()
    payload=json.loads(f['profile'].read_text())
    p=payload['profiles']['BAL'];p.pop('version')
    p['source']={'archive_file':str(archive),'reference':'fixture:injury',
                 'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'observed_at':NOW.isoformat()}
    crosswalk=f['runtime']/'reviewed-crosswalk.json';crosswalk.write_text('{"fixture":"crosswalk"}')
    p['crosswalk']={'archive_file':str(crosswalk),'reference':'fixture:crosswalk',
                    'sha256':hashlib.sha256(crosswalk.read_bytes()).hexdigest()}
    p['version']=content_hash(p)
    profile=folder/'profiles.json';profile.write_text(json.dumps(payload))
    f['state']['latest_profiles']=str(profile);f['state_path'].write_text(json.dumps(f['state']))
    f['config'].write_text(json.dumps({'runtime':str(f['runtime']),'output':str(output)}))
    inputs=[f['state_path'],f['config'],profile,archive,f['archive'],crosswalk]
    before={path:path.read_bytes() for path in inputs}
    report=run_scoped_fixture(f,game_id='fixture-game',teams=['BAL','JAX'])
    assert report['auditors']['source']['status']=='PASS'
    assert report['auditors']['source']['evidence']['source_hashes_verified']==2
    assert report['scope']['game_id']=='fixture-game' and report['scope']['profiles_selected']==['BAL','JAX']
    assert all(path.read_bytes()==data for path,data in before.items())
    # Approved output location does not override the content hash requirement.
    archive.write_text('{"fixture":"tampered"}')
    from research.accuracy_swarm import source_audit
    audit=source_audit({'BAL':p},[output],NOW,[],restrict_paths=True)
    assert audit['status']=='BLOCKED' and 'source archive hash mismatch' in audit['reasons']
