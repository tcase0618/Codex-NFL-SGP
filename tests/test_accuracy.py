from dataclasses import replace

from football_engine.accuracy import check_clearance, report_hash, evidence_fingerprint
from football_engine.engine import AUDITORS
from football_engine.construction import construct_audited_tickets
from test_engine import snapshot, leg, NOW


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
    game=replace(snapshot(),team_profile_versions={'CHI':'chi','SEA':'sea'})
    report=dict(as_of=NOW.isoformat(),status='PASS',scope={'game_id':game.game_id,'model_version':'m','team_profile_versions':game.team_profile_versions},
                auditors={n:{'status':'PASS','reasons':[],'evidence':{'reference':str(path)}} for n in AUDITORS},
                verified_artifacts=[{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}])
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
    denied=construct_audited_tickets(game,(replace(leg(),odds_decimal=5),),3,
        accuracy_report=report,model_version='m',audit_checks=checks)
    assert denied.status=='BLOCKED' and 'offered legs differ' in denied.reasons[0]
    malformed=construct_audited_tickets(game,(replace(leg(),odds_decimal=float('nan')),),3,
        accuracy_report=report,model_version='m',audit_checks=checks)
    assert malformed.status=='BLOCKED' and not malformed.candidates
    assert not check_clearance(report,game,model_version='other').cleared
    path.write_text('tampered')
    assert not check_clearance(report,game,model_version='m').cleared
