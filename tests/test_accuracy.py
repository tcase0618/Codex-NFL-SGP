from dataclasses import replace

from football_engine.accuracy import check_clearance, report_hash, evidence_fingerprint
from football_engine.engine import AUDITORS
from football_engine.construction import construct_audited_tickets
from test_engine import snapshot, leg, NOW, SOURCE
from test_market_first import capture


def test_nonfinite_auditor_evidence_blocks_only_the_affected_role():
    from football_engine.accuracy import run_auditors
    checks={name:(lambda:{'status':'PASS','reasons':[],'evidence':{'checked':True}}) for name in AUDITORS}
    checks['market']=lambda:{'status':'PASS','reasons':[],'evidence':{'probability':float('nan')}}
    report=run_auditors(checks,as_of=NOW)
    assert report['status']=='BLOCKED'
    assert report['auditors']['market']['status']=='BLOCKED'
    assert report['auditors']['source']['status']=='PASS'


def test_missing_or_forged_swarm_report_cannot_enable_candidates():
    game = snapshot()
    assert not check_clearance(None, game, model_version='m').cleared
    report = dict(as_of=NOW.isoformat(),status='PASS',auditors={n:{'status':'PASS'} for n in AUDITORS})
    report['report_hash'] = report_hash(report)
    assert not check_clearance(report, game, model_version='m').cleared
    assert construct_audited_tickets(game,(leg(),),3,accuracy_report=report,model_version='m').candidates == ()


def test_clearance_rechecks_scope_artifact_hash_and_all_seven_roles(tmp_path):
    import hashlib
    path=tmp_path/'proof';path.write_text('real artifact')
    game=replace(snapshot(),team_profile_versions={'CHI':'chi','SEA':'sea'},
                 qb_context={'CHI':{'current_starter_id':'qb-1','baseline_starter_id':'qb-1','source':SOURCE}})
    _, captured_leg, capture_path, capture_hash = capture(tmp_path)
    report=dict(as_of=NOW.isoformat(),status='PASS',scope={'game_id':game.game_id,'model_version':'m','team_profile_versions':game.team_profile_versions},
                auditors={n:{'status':'PASS','reasons':[],'evidence':{'reference':str(path)}} for n in AUDITORS},
                verified_artifacts=[{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},
                                    {'path':str(capture_path),'sha256':capture_hash}])
    report['auditors']['market']['evidence']['market_capture_sha256']=capture_hash
    report['scope']['model_sha256']='a'*64
    report['auditors']['correlation']['evidence']['model_sha256']='a'*64
    report['auditors']['remaining']['evidence'].update(game_id=game.game_id,team_profile_versions=game.team_profile_versions,
        model_sha256='a'*64,snapshot_fingerprint=evidence_fingerprint(game),leg_fingerprints=[evidence_fingerprint(leg())])
    report['report_hash']=report_hash(report)
    import copy
    checks={n:(lambda value=copy.deepcopy(report['auditors'][n]):copy.deepcopy(value)) for n in AUDITORS}
    assert not check_clearance(report,game,model_version='m').cleared
    assert check_clearance(report,game,model_version='m',checks=checks).cleared
    changed=replace(game,players={})
    assert not check_clearance(report,changed,model_version='m',checks=checks).cleared
    denied=construct_audited_tickets(game,(captured_leg,),3,
        accuracy_report=report,model_version='m',audit_checks=checks,
        market_capture_path=capture_path,market_capture_sha256=capture_hash)
    assert denied.status=='BLOCKED' and 'offered legs differ' in denied.reasons[0]
    bound=copy.deepcopy(report)
    bound['auditors']['remaining']['evidence']['leg_fingerprints']=[evidence_fingerprint(captured_leg)]
    bound['report_hash']=report_hash({k:v for k,v in bound.items() if k!='report_hash'})
    bound_checks={n:(lambda value=copy.deepcopy(bound['auditors'][n]):copy.deepcopy(value)) for n in AUDITORS}
    allowed=construct_audited_tickets(game,(captured_leg,),3,
        accuracy_report=bound,model_version='m',audit_checks=bound_checks,
        market_capture_path=capture_path,market_capture_sha256=capture_hash)
    assert allowed.status=='RESEARCH_ONLY'
    unbound=copy.deepcopy(bound)
    unbound['auditors']['market']['evidence']['market_capture_sha256']='0'*64
    unbound['report_hash']=report_hash({k:v for k,v in unbound.items() if k!='report_hash'})
    unbound_checks={n:(lambda value=copy.deepcopy(unbound['auditors'][n]):copy.deepcopy(value)) for n in AUDITORS}
    blocked=construct_audited_tickets(game,(captured_leg,),3,
        accuracy_report=unbound,model_version='m',audit_checks=unbound_checks,
        market_capture_path=capture_path,market_capture_sha256=capture_hash)
    assert 'not bound' in blocked.reasons[0]
    malformed=construct_audited_tickets(game,(replace(leg(),odds_decimal=float('nan')),),3,
        accuracy_report=report,model_version='m',audit_checks=checks,
        market_capture_path=capture_path,market_capture_sha256=capture_hash)
    assert malformed.status=='BLOCKED' and not malformed.candidates
    assert not check_clearance(report,game,model_version='other').cleared
    path.write_text('tampered')
    assert not check_clearance(report,game,model_version='m').cleared
