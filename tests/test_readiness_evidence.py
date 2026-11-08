"""Synthetic offline evidence only; never measurements of model performance."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import sqlite3

import pytest

from football_engine.engine import TicketQuote,evaluate_ticket,leg_key
from football_engine.ledger import _json_ready
from research.calibration import _content_hash
from research.readiness_evidence import (audit_bindings,audit_capabilities,run,verify_pack)
from test_engine import snapshot,leg
from test_pregame_capture import NOW,KICKOFF,Clock,fetch
from research.pregame_capture import capture
from test_accuracy_integration import scoped_audit_files


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf-8')
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def artifacts(tmp_path):
    bundle=tmp_path/'capture';capture(bundle,clock=Clock(),get=fetch)
    game=snapshot();as_of=NOW+timedelta(seconds=30)
    def retime(value):
        if isinstance(value,dict):
            if set(('provider','reference','observed_at'))<=value.keys():value['observed_at']=NOW.isoformat()
            for v in value.values():retime(v)
        elif isinstance(value,list):
            for v in value:retime(v)
    s=_json_ready(game);retime(s)
    s.update(game_id='401873037',as_of=as_of.isoformat(),kickoff=KICKOFF,team_profile_versions={'JAX':'v1','BAL':'v2'})
    s['players']['p1']['team_id']='JAX'
    for scenario in s['scenarios']:
        scenario['joint_samples']=[{'p1:rushing_yards:40':i<90,'p1:anytime_td:1':i<60} for i in range(100)]
    from football_engine.snapshot_evidence import decode,source
    typed=decode(s)
    legs=[replace(leg(),team_id='JAX',source=source(s['source'])),replace(leg('anytime_td',1),team_id='JAX',source=source(s['source']))]
    declaration={'schema':'MODEL_CAPABILITIES_V1','markets':['rushing_yards','anytime_td'],'features':['volume','touchdown_opportunity']}
    model={'artifact':{'version':'synthetic-v1','capabilities':declaration},'fitted_at':(NOW-timedelta(hours=1)).isoformat(),'training_sha256':'a'*64}
    model['sha256']=_content_hash(model['artifact'])
    model['frozen_sha256']=_content_hash({k:model[k] for k in ('artifact','fitted_at','training_sha256')})
    inputs={'artifact':{'game_id':s['game_id'],'team_profile_versions':s['team_profile_versions'],'player_ids':['p1']},
            'available_at':(NOW-timedelta(minutes=1)).isoformat()}
    inputs['sha256']=_content_hash(inputs['artifact']);inputs['frozen_sha256']=_content_hash(inputs)
    # Frozen inputs hash excludes the ordinary artifact digest per canonical schema.
    inputs['frozen_sha256']=_content_hash({k:inputs[k] for k in ('artifact','available_at')})
    decision=evaluate_ticket(typed,tuple(legs));assert decision.approved
    forecast={'game_id':s['game_id'],'ticket_id':'synthetic-ticket','model_sha256':model['sha256'],
              'inputs_sha256':inputs['sha256'],'team_profile_versions':s['team_profile_versions'],
              'observed_at':NOW.isoformat(),'leg_ids':['l1','l2'],
              'legs':[dict(leg_id=f'l{i+1}',player_id=l.player_id,team_id=l.team_id,market=l.market,line=l.line) for i,l in enumerate(legs)],
              'probabilities':{f'l{i+1}':decision.leg_probabilities[leg_key(l)] for i,l in enumerate(legs)}}
    quote=_json_ready(TicketQuote(3,source(s['source']),tuple(leg_key(l) for l in legs)))
    opp={'snapshot':s,'legs':_json_ready(legs),'quote':quote,'model_sha256':model['sha256'],
         'inputs_sha256':inputs['sha256'],'forecast_sha256':_content_hash(forecast)}
    payloads={'model':model,'model_inputs':inputs,'forecast':forecast,'opportunity':opp,'offered_quote':quote}
    inventory=[]
    for role,value in payloads.items():
        record=write(tmp_path/'supplied'/(role+'.json'),value)
        inventory.append({**record,'role':role,'game_id':s['game_id'],'reference':'fixture:'+role,
                          'published_at':(NOW-timedelta(hours=2)).isoformat(),
                          'available_at':(NOW-timedelta(minutes=1)).isoformat(),'observed_at':NOW.isoformat()})
    inv=tmp_path/'inventory.json';write(inv,inventory)
    return dict(bundle=bundle,payloads=payloads,inventory=inventory,inventory_path=inv,
                game={'game_id':s['game_id'],'kickoff':KICKOFF},as_of=as_of,tmp_path=tmp_path)


def bindings(f,payloads=None,manifest=None):
    return audit_bindings(payloads or f['payloads'],manifest or {},f['game'],f['as_of'])


def test_valid_constructible_bindings_and_sealed_pack_still_missing_history(artifacts):
    f=artifacts;assert bindings(f)['status']=='PASS'
    before=(f['bundle']/'manifest.json').read_bytes()
    report=run(f['bundle'],f['tmp_path']/'pack',inventory_path=f['inventory_path'],as_of=f['as_of'])
    assert report['audits']['bindings']['status']=='PASS'
    assert report['audits']['capabilities']['status']=='PASS'
    assert report['missing_roles']==['ledger']
    assert report['status']=='BLOCKED' and report['betting_ready'] is False
    assert report['performance_approval']=='NOT_ASSESSED'
    assert report['audits']['calibration']['status']=='BLOCKED'
    assert report['audits']['backtest']['status']=='BLOCKED'
    manifest=json.loads(open(report['manifest_path']).read())
    assert manifest['scope']['model_sha256']==f['payloads']['model']['sha256']
    assert manifest['scope']['model_version']=='synthetic-v1'
    assert manifest['scope']['team_profile_versions']=={'JAX':'v1','BAL':'v2'}
    assert verify_pack(f['tmp_path']/'pack')['status']=='PASS'
    assert (f['bundle']/'manifest.json').read_bytes()==before
    (f['tmp_path']/'pack'/'artifacts'/'forecast.json').write_text('{}')
    assert verify_pack(f['tmp_path']/'pack')['status']=='BLOCKED'


@pytest.mark.parametrize('damage',['game','profiles','model','input_version','player','swapped_ids','probability','quote','calibration','forecast_hash','future','stale'])
def test_exact_bindings_reject_mutations(artifacts,damage):
    f=artifacts;p=deepcopy(f['payloads']);manifest={}
    if damage=='game':p['opportunity']['snapshot']['game_id']='other'
    elif damage=='profiles':manifest={'scope':{'team_profile_versions':{'JAX':'wrong','BAL':'v2'}}}
    elif damage=='model':p['forecast']['model_sha256']='0'*64
    elif damage=='input_version':p['forecast']['inputs_sha256']='0'*64
    elif damage=='player':p['forecast']['legs'][0]['player_id']='unknown'
    elif damage=='swapped_ids':
        p['forecast']['legs'][0]['leg_id']='l2';p['forecast']['legs'][1]['leg_id']='l1'
        p['opportunity']['forecast_sha256']=_content_hash(p['forecast'])
    elif damage=='probability':
        p['forecast']['probabilities']['l1']=.01
        p['opportunity']['forecast_sha256']=_content_hash(p['forecast'])
    elif damage=='quote':p['offered_quote']={**p['offered_quote'],'decimal_odds':99}
    elif damage=='calibration':manifest={'calibration':{'model':{'different':True},'inputs':p['model_inputs']}}
    elif damage=='forecast_hash':p['opportunity']['forecast_sha256']='0'*64
    elif damage=='future':p['model']['fitted_at']=(f['as_of']+timedelta(hours=1)).isoformat()
    elif damage=='stale':p['forecast']['observed_at']=(NOW-timedelta(hours=1)).isoformat()
    assert bindings(f,p,manifest)['status']=='BLOCKED'


def capability(f,declaration=None,coverage=None):
    model=deepcopy(f['payloads']['model']['artifact'])
    if declaration is not None:model['capabilities']=declaration
    return audit_capabilities(model['capabilities'],model_sha256=_content_hash(model),model_artifact=model,
        markets=['rushing_yards'],game_id=f['game']['game_id'],profile_versions={'JAX':'v1','BAL':'v2'},
        as_of=f['as_of'],coverage=coverage)


def test_capabilities_are_constructible_and_hash_bound_without_self_hash(artifacts):
    f=artifacts;r=capability(f)
    assert r['status']=='PASS' and r['evidence']['requirements']=={'lineups':False,'charting':False}
    model=f['payloads']['model']['artifact'];changed=deepcopy(model['capabilities']);changed['features']=['pressure']
    assert audit_capabilities(changed,model_sha256=_content_hash(model),model_artifact=model,
        markets=['rushing_yards'],game_id=f['game']['game_id'],profile_versions={'JAX':'v1','BAL':'v2'},as_of=f['as_of'])['status']=='BLOCKED'
    for declaration in ({},dict(schema='MODEL_CAPABILITIES_V1',markets=['rushing_yards'],features=['unknown'])):
        assert capability(f,declaration)['status']=='BLOCKED'


def test_pressure_and_interaction_require_scoped_sourced_both_team_coverage(artifacts):
    f=artifacts;declaration={'schema':'MODEL_CAPABILITIES_V1','markets':['rushing_yards'],
                           'features':['pressure','personnel_interactions']}
    assert capability(f,declaration)['status']=='BLOCKED'
    model={**f['payloads']['model']['artifact'],'capabilities':declaration};digest=_content_hash(model)
    coverage={}
    for kind in ('lineups','charting'):
        rows=[{'game_id':f['game']['game_id'],'team_id':t,'features':['pressure'],
               'player_ids':[f'{t}-{i}' for i in range(11)]} for t in ('JAX','BAL')]
        rawsource=write(f['tmp_path']/(kind+'-source.json'),{'kind':kind,'game_id':f['game']['game_id'],
                        'observed_at':NOW.isoformat(),'rows':rows})
        source={'archive_file':rawsource['path'],'sha256':rawsource['sha256'],'reference':'fixture:chart', 'observed_at':NOW.isoformat()}
        payload={'kind':kind,'game_id':f['game']['game_id'],'model_sha256':digest,'team_profile_versions':{'JAX':'v1','BAL':'v2'},
                 'observed_at':NOW.isoformat(),'kickoff':KICKOFF,'rows':[{**r,'source':source} for r in rows]}
        coverage[kind]=write(f['tmp_path']/(kind+'.json'),payload)
    assert capability(f,declaration,coverage)['status']=='PASS'
    payload['rows'][0]['game_id']='unrelated'
    coverage['charting']=write(f['tmp_path']/'charting.json',payload)
    assert capability(f,declaration,coverage)['status']=='BLOCKED'


def test_metric_policy_uses_actual_leg_markets_and_immutable_model(artifacts):
    from research.accuracy_swarm import metric_audit
    from football_engine.team_quant import CALCULATION_REVISION,DEFINITION_REVISION
    f=artifacts;db=f['tmp_path']/'teams.sqlite'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE metadata (key TEXT,value TEXT)')
        conn.executemany('INSERT INTO metadata VALUES (?,?)',[
            ('summary',json.dumps({'plays':1})),('active_definition_revision',DEFINITION_REVISION),('calculation_revision',CALCULATION_REVISION)])
        conn.execute('CREATE TABLE lineups (game_id TEXT)');conn.execute('CREATE TABLE play_charting (game_id TEXT)')
    scope={'audit_mode':'game','game_id':f['game']['game_id'],'team_profile_versions':{'JAX':'v1','BAL':'v2'},'model_sha256':f['payloads']['model']['sha256']}
    record=write(f['tmp_path']/'opp.json',f['payloads']['opportunity'])
    evidence={'model':f['payloads']['model'],'opportunity_snapshot':record}
    assert metric_audit(db,as_of=f['as_of'],scope=scope,capability_evidence=evidence)['status']=='PASS'
    assert metric_audit(db,as_of=f['as_of'],scope=scope)['status']=='BLOCKED'
    missing=metric_audit(db,as_of=f['as_of'],scope=scope,capability_evidence={})
    assert any('missing hash-bound capability model evidence' in r for r in missing['reasons'])
    missing_opp=metric_audit(db,as_of=f['as_of'],scope=scope,capability_evidence={'model':f['payloads']['model']})
    assert any('missing frozen opportunity evidence' in r for r in missing_opp['reasons'])
    with sqlite3.connect(db) as conn:conn.execute("INSERT INTO play_charting VALUES ('unrelated')")
    changed=deepcopy(evidence);changed['model']['artifact']['capabilities']['features']=['pressure']
    assert metric_audit(db,as_of=f['as_of'],scope=scope,capability_evidence=changed)['status']=='BLOCKED'
    # Default contract still requires all-11/charting regardless of row count.
    assert metric_audit(db,as_of=f['as_of'])['status']=='BLOCKED'


def test_context_only_capture_explicitly_missing_all_roles_and_no_placeholders(artifacts):
    f=artifacts;report=run(f['bundle'],f['tmp_path']/'empty-pack',as_of=f['as_of'])
    assert len(report['missing_roles'])==6
    manifest=json.loads(open(report['manifest_path']).read())
    assert set(manifest)=={'scope'}
    assert manifest['scope']=={'game_id':f['game']['game_id']}
    with pytest.raises(FileExistsError):run(f['bundle'],f['tmp_path']/'empty-pack',as_of=f['as_of'])


def test_wrong_hash_and_duplicate_roles_never_silently_promote(artifacts):
    f=artifacts;inventory=deepcopy(f['inventory']);inventory[0]['sha256']='0'*64
    inventory.append(deepcopy(inventory[1]));write(f['inventory_path'],inventory)
    report=run(f['bundle'],f['tmp_path']/'bad-pack',inventory_path=f['inventory_path'],as_of=f['as_of'])
    assert 'model' in report['missing_roles']
    assert any('duplicate role' in r for r in report['reasons'])
    assert any('hash mismatch' in r for r in report['reasons'])


def test_canonical_swarm_wires_capability_evidence_without_relaxing_other_audits(artifacts,scoped_audit_files):
    from research.accuracy_swarm import run as swarm
    from research.automatic_refresh import upcoming_game_context
    from football_engine.team_quant import CALCULATION_REVISION,DEFINITION_REVISION
    f=artifacts;files=scoped_audit_files
    payload=json.loads(files['archive'].read_bytes());event=payload['events'][0]
    event['id']=f['game']['game_id'];event['date']=KICKOFF
    write(files['archive'],payload)
    state=files['state'];source=state['upcoming_games']['JAX']['schedule_source']
    source['sha256']=hashlib.sha256(files['archive'].read_bytes()).hexdigest()
    state['upcoming_games']={r['team_id']:r for r in upcoming_game_context(event,source,f['as_of'])}
    write(files['state_path'],state)
    versions={t:p['version'] for t,p in json.loads(files['profile'].read_bytes())['profiles'].items() if t in ('JAX','BAL')}
    opp=deepcopy(f['payloads']['opportunity']);opp['snapshot']['team_profile_versions']=versions
    record=write(f['tmp_path']/'current-opportunity.json',opp)
    evidence={'calibration':{'model':f['payloads']['model']},'opportunity_snapshot':record,
              'scope':{'model_sha256':f['payloads']['model']['sha256']}}
    ev=f['tmp_path']/'evidence.json';write(ev,evidence)
    db=f['tmp_path']/'canonical.sqlite'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE metadata (key TEXT,value TEXT)')
        conn.executemany('INSERT INTO metadata VALUES (?,?)',[
            ('summary',json.dumps({'plays':1})),('active_definition_revision',DEFINITION_REVISION),('calculation_revision',CALCULATION_REVISION)])
        conn.execute('CREATE TABLE lineups (game_id TEXT)');conn.execute('CREATE TABLE play_charting (game_id TEXT)')
    report=swarm(files['config'],db,f['tmp_path']/'swarm',ev,f['as_of'],game_id=f['game']['game_id'],teams=['BAL','JAX'])
    assert report['auditors']['market']['status']=='PASS'
    assert report['auditors']['market']['evidence']['capabilities']['evidence']['requirements']=={'lineups':False,'charting':False}
    assert report['status']=='BLOCKED' and report['betting_ready'] is False
    evidence['scope']['model_sha256']='0'*64;write(ev,evidence)
    bad=swarm(files['config'],db,f['tmp_path']/'wrong-model-swarm',ev,f['as_of'],game_id=f['game']['game_id'],teams=['BAL','JAX'])
    assert bad['auditors']['market']['status']=='BLOCKED'
