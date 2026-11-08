"""Offline assembly of supplied evidence, never generation of missing evidence."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from football_engine.accuracy import evidence_fingerprint
from football_engine.ledger import audit_ledger_evidence
from football_engine.snapshot_evidence import audit_opportunity_evidence, decode
from research.accuracy_backtest import audit_backtest_evidence
from research.calibration import _content_hash, audit_calibration_evidence
from research.pregame_capture import verify as verify_capture


ROLES = ('model', 'model_inputs', 'forecast', 'opportunity', 'offered_quote', 'ledger')
MARKETS = frozenset(('passing_yards','rushing_yards','receiving_yards','passing_td','anytime_td'))
FEATURES = frozenset(('volume','yardage_efficiency','touchdown_opportunity',
                      'personnel_interactions','pressure','coverage','concept'))


def stamp(value):
    value = value if isinstance(value,datetime) else datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.utcoffset() is None:raise ValueError('aware timestamp required')
    return value


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def hashed_file(record):
    path=Path(record['path'])
    if not path.is_absolute() or not path.is_file():raise ValueError('absolute existing artifact required')
    if file_hash(path)!=record['sha256']:raise ValueError('artifact byte hash mismatch')
    return path


def outcome(reasons, evidence):
    return {'status':'BLOCKED' if reasons else 'PASS','reasons':list(dict.fromkeys(reasons)),'evidence':evidence}


def audit_capabilities(declaration, *, model_sha256, markets, game_id, profile_versions, as_of,
                       model_artifact=None, coverage=None, kickoff=None):
    """Policy helper only; does not alter or replace the canonical metric auditor.

    Missing/unknown declarations fail closed. Required coverage must bind actual
    game/model/profiles and both teams; a database row count is never coverage.
    """
    reasons=[];evidence={'requirements':{'lineups':True,'charting':True},'coverage_verified':[]}
    try:
        if declaration.get('schema')!='MODEL_CAPABILITIES_V1':raise ValueError('explicit capability schema required')
        supported=declaration['markets'];features=declaration['features'];requested=set(markets)
        if (not requested or not requested<=MARKETS or not isinstance(supported,list) or not supported
                or len(supported)!=len(set(supported)) or not set(supported)<=MARKETS
                or not requested<=set(supported)):
            raise ValueError('unknown or unsupported markets')
        if (not isinstance(features,list) or not features or len(features)!=len(set(features))
                or not set(features)<=FEATURES):raise ValueError('unknown or empty model features')
        if (not model_artifact or _content_hash(model_artifact)!=model_sha256 or
                model_artifact.get('capabilities')!=declaration):
            raise ValueError('capabilities must be inside exact hashed model artifact')
        if not game_id or len(profile_versions)!=2 or not all(profile_versions.values()):
            raise ValueError('two sourced profile versions required')
        required={'lineups':'personnel_interactions' in features,
                  'charting':bool(set(features)&{'pressure','coverage','concept'})}
        evidence['requirements']=required
        for kind,needed in required.items():
            if not needed:continue
            record=(coverage or {}).get(kind,{})
            payload=json.loads(hashed_file(record).read_bytes())
            if (payload.get('kind')!=kind or payload.get('game_id')!=game_id or
                    payload.get('model_sha256')!=model_sha256 or
                    payload.get('team_profile_versions')!=profile_versions):
                raise ValueError(f'{kind}: scoped coverage binding mismatch')
            if not stamp(payload['observed_at'])<=stamp(as_of)<stamp(payload['kickoff']):
                raise ValueError(f'{kind}: invalid coverage chronology')
            if kickoff is not None and stamp(payload['kickoff'])!=stamp(kickoff):raise ValueError(f'{kind}: kickoff mismatch')
            rows=payload['rows']
            if not isinstance(rows,list) or not rows:raise ValueError(f'{kind}: empty coverage')
            teams=set()
            for row in rows:
                if row.get('game_id')!=game_id or row.get('team_id') not in profile_versions:
                    raise ValueError(f'{kind}: unrelated coverage row')
                teams.add(row['team_id'])
                source=row['source'];source_path=hashed_file({'path':source['archive_file'],'sha256':source['sha256']})
                if not source.get('reference') or stamp(source['observed_at'])>stamp(payload['observed_at']):
                    raise ValueError(f'{kind}: missing source provenance')
                sourced=json.loads(source_path.read_bytes())
                if (sourced.get('kind')!=kind or sourced.get('game_id')!=game_id or
                        sourced.get('observed_at')!=source['observed_at'] or
                        {k:v for k,v in row.items() if k!='source'} not in sourced.get('rows',[])):
                    raise ValueError(f'{kind}: coverage row absent from scoped source archive')
                if kind=='lineups':
                    ids=row['player_ids']
                    if not isinstance(ids,list) or len(ids)!=11 or len(set(ids))!=11 or not all(isinstance(p,str) and p.strip() for p in ids):
                        raise ValueError('lineups: eleven distinct sourced player IDs required')
                elif not (set(features)&{'pressure','coverage','concept'})<=set(row.get('features',[])):
                    raise ValueError('charting: required feature coverage missing')
            if teams!=set(profile_versions):raise ValueError(f'{kind}: both scoped teams required')
            evidence['coverage_verified'].append(kind)
        evidence.update(markets=sorted(requested),features=features,model_sha256=model_sha256)
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
        reasons.append(str(exc))
    return outcome(reasons,evidence)


def audit_bindings(payloads, manifest, game, as_of):
    """Validate exact supplied role identities across existing engine schemas."""
    reasons=[];evidence={}
    try:
        model=payloads['model'];inputs=payloads['model_inputs'];forecast=payloads['forecast'];opp=payloads['opportunity']
        for role,block,keys in (('model',model,('artifact','fitted_at','training_sha256')),
                                ('model_inputs',inputs,('artifact','available_at'))):
            if not block['artifact'] or block['sha256']!=_content_hash(block['artifact']):
                raise ValueError(f'{role}: canonical artifact hash mismatch')
            if block['frozen_sha256']!=_content_hash({k:block[k] for k in keys}):
                raise ValueError(f'{role}: frozen metadata hash mismatch')
        snapshot=decode(opp['snapshot']);cutoff=stamp(as_of)
        if snapshot.game_id!=game['game_id'] or snapshot.kickoff!=stamp(game['kickoff']):
            raise ValueError('opportunity game/kickoff differs from captured schedule')
        versions=snapshot.team_profile_versions
        if len(versions)!=2 or not all(versions.values()):raise ValueError('exact two-team profile versions required')
        if (manifest.get('scope',{}).get('game_id',snapshot.game_id)!=snapshot.game_id or
                manifest.get('scope',{}).get('team_profile_versions',versions)!=versions):
            raise ValueError('opportunity game/profile scope mismatch')
        if not stamp(model['fitted_at'])<=snapshot.as_of<=cutoff or not stamp(inputs['available_at'])<=snapshot.as_of:
            raise ValueError('model/inputs unavailable at snapshot')
        digest=model['sha256']
        version=model['artifact'].get('version')
        if not isinstance(version,str) or not version.strip():raise ValueError('model version required')
        if opp.get('model_sha256')!=digest or forecast.get('model_sha256')!=digest:
            raise ValueError('forecast/opportunity model binding mismatch')
        if forecast.get('inputs_sha256')!=inputs['sha256']:raise ValueError('forecast input version binding mismatch')
        if (forecast.get('game_id')!=snapshot.game_id or forecast.get('team_profile_versions')!=versions or
                stamp(forecast['observed_at'])>snapshot.as_of):raise ValueError('forecast game/profile/time mismatch')
        if (snapshot.as_of-stamp(forecast['observed_at'])).total_seconds()>300:
            raise ValueError('forecast stale relative to opportunity')
        legs=forecast['legs'];ids=forecast['leg_ids']
        if not isinstance(legs,list) or not legs or not isinstance(ids,list) or len(ids)!=len(set(ids)) or set(ids)!={l['leg_id'] for l in legs}:
            raise ValueError('forecast exact leg IDs required')
        def identities(rows):
            keys=[(r['player_id'],r['team_id'],r['market'],r['line']) for r in rows]
            if len(keys)!=len(set(keys)):raise ValueError('duplicate player/market/line')
            return set(keys)
        if identities(legs)!=identities(opp['legs']):raise ValueError('forecast/opportunity player/market/line mismatch')
        for leg in legs:
            player=snapshot.players.get(leg['player_id'])
            if player is None or player.team_id!=leg['team_id'] or player.team_id not in versions:
                raise ValueError('forecast player identity/team mismatch')
        probabilities=forecast['probabilities']
        if set(probabilities)!=set(ids) or any(type(p) not in (float,int) or not math.isfinite(p) or not 0<=p<=1 for p in probabilities.values()):
            raise ValueError('forecast probabilities incomplete/invalid')
        # Leg IDs name probability entries, while engine keys name the precise
        # player/market/line. Bind both mappings; set equality alone hides swaps.
        from football_engine.engine import Leg, evaluate_ticket, leg_key
        from football_engine.snapshot_evidence import source
        engine_legs=tuple(Leg(l['player_id'],l['team_id'],l['market'],l['line'],l['current'],l['odds_decimal'],source(l['source'])) for l in opp['legs'])
        decision=evaluate_ticket(snapshot,engine_legs)
        engine_keys={ (l.player_id,l.team_id,l.market,l.line):leg_key(l) for l in engine_legs }
        for leg in legs:
            key=engine_keys[(leg['player_id'],leg['team_id'],leg['market'],leg['line'])]
            if abs(probabilities[leg['leg_id']]-decision.leg_probabilities.get(key,-1))>1e-9:
                raise ValueError('forecast leg-ID probability differs from opportunity engine forecast')
        if inputs['artifact'].get('game_id')!=snapshot.game_id or inputs['artifact'].get('team_profile_versions')!=versions:
            raise ValueError('model input game/profile version mismatch')
        if not {l['player_id'] for l in legs}<=set(inputs['artifact'].get('player_ids',[])):
            raise ValueError('model input player identities incomplete')
        if (opp.get('forecast_sha256')!=_content_hash(forecast) or opp.get('inputs_sha256')!=inputs['sha256']):
            raise ValueError('opportunity forecast/source version binding missing or mismatched')
        if payloads['offered_quote']!=opp['quote']:raise ValueError('offered quote differs from exact opportunity quote')
        if (stamp(opp['quote']['source']['observed_at'])>snapshot.as_of or
                (cutoff-stamp(opp['quote']['source']['observed_at'])).total_seconds()>300):
            raise ValueError('offered quote future/stale')
        calibration=manifest.get('calibration')
        if calibration and (calibration.get('model')!=model or calibration.get('inputs')!=inputs):
            raise ValueError('calibration model/input artifact mismatch')
        scope=manifest.get('scope',{})
        if 'kickoff' in scope and stamp(scope['kickoff'])!=snapshot.kickoff:raise ValueError('scope kickoff mismatch')
        if scope.get('model_sha256',digest)!=digest:raise ValueError('scope model mismatch')
        if scope.get('model_version',model['artifact'].get('version'))!=model['artifact'].get('version'):
            raise ValueError('scope model version mismatch')
        evidence.update(game_id=snapshot.game_id,kickoff=snapshot.kickoff.isoformat(),team_profile_versions=versions,model_sha256=digest,model_version=version,
                        inputs_sha256=inputs['sha256'],snapshot_fingerprint=evidence_fingerprint(snapshot),
                        markets=sorted({l['market'] for l in legs}))
    except (OSError,ValueError,KeyError,TypeError,AttributeError,OverflowError) as exc:
        reasons.append(f'exact artifact binding: {exc}')
    return outcome(reasons,evidence)


def _safe_audit(auditor,manifest,as_of):
    try:return auditor(manifest,as_of)
    except Exception as exc:return outcome([f'auditor failed: {type(exc).__name__}'],{'check_completed':False})


def assess_history(workspace, *, as_of=None):
    """Bounded actual contract rerun; never treat reconstructed CSVs as freezes."""
    from research.forecast_gap_diagnostics import csv_coverage
    cutoff=stamp(as_of) if as_of else datetime.now(timezone.utc)
    outputs=Path(workspace)/'outputs';bundles=[];errors=[]
    for path in sorted(outputs.glob('*/evidence-manifest.json')):
        try:
            value=json.loads(path.read_bytes())
            audits={'backtest':_safe_audit(audit_backtest_evidence,value.get('backtest',{}),cutoff),
                    'calibration':_safe_audit(audit_calibration_evidence,value.get('calibration',{}),cutoff)}
            bundles.append({'path':str(path),'sha256':file_hash(path),'audits':audits})
        except (OSError,ValueError,AttributeError) as exc:errors.append({'path':str(path),'error':str(exc)})
    datasets=[]
    for path in sorted(outputs.glob('replay-*-predictions.csv')):
        datasets.append({'path':str(path),'sha256':file_hash(path),**csv_coverage(path,forecast=True)})
    consistent=[b['path'] for b in bundles if all(a['status']=='PASS' for a in b['audits'].values())]
    return {'contract_consistent_candidates':consistent,'bundles':bundles,'datasets':datasets,'errors':errors,
            'walk_forward_executed':False,'performance_approval':'NOT_ASSESSED',
            'reason':'Existing evidence contracts rerun; no fitting, backdating, or inferred historical availability',
            'discovery_scope':'outputs/*/evidence-manifest.json and outputs/replay-*-predictions.csv only'}


def run(capture_path, output, *, inventory_path=None, evidence_path=None, as_of=None, expected_capture_sha256=None):
    """Seal genuine supplied bytes and bridge existing evidence schemas.

    Return manifest_path for an independent accuracy_swarm.run; this function
    audits the four evidence roles and bindings, never replaces all seven roles.
    """
    cutoff=stamp(as_of) if as_of else datetime.now(timezone.utc)
    output=Path(output).resolve();capture_path=Path(capture_path).resolve()
    if output.exists():raise FileExistsError('new output directory required')
    if output==capture_path or output.is_relative_to(capture_path):raise ValueError('output cannot be inside immutable capture')
    verification=verify_capture(capture_path,expected_capture_sha256)
    reasons=[];inventory=[];payloads={};records={};dependencies=[]
    if verification['status']!='PASS':reasons+=['capture integrity: '+r for r in verification['errors']]
    capture=json.loads((capture_path/'manifest.json').read_bytes())
    game=capture.get('game') or {}
    if not game.get('game_id') or not capture.get('captured_before_decision_cutoff'):
        reasons.append('capture lacks verified before-cutoff scheduled game')
    if stamp(capture['captured_at'])>cutoff:reasons.append('capture after assessment cutoff')
    manifest=json.loads(Path(evidence_path).read_bytes()) if evidence_path else {}
    if not isinstance(manifest,dict):raise ValueError('evidence must be an object')
    manifest=deepcopy(manifest)
    if manifest.get('scope',{}).get('game_id',game.get('game_id'))!=game.get('game_id'):
        reasons.append('supplied evidence game scope mismatch')
    manifest.setdefault('scope',{})['game_id']=game.get('game_id')
    for entry in capture.get('caller_artifacts',[]):
        role=entry.get('role')
        if role not in ROLES:continue
        item={**entry.get('caller_metadata',{}),'role':role,'path':str(capture_path/entry['path']),
              'sha256':entry['sha256'],'frozen_at':entry['frozen_at']}
        inventory.append(item)
    if inventory_path:
        supplied=json.loads(Path(inventory_path).read_bytes())
        if not isinstance(supplied,list):raise ValueError('inventory must be a JSON list')
        inventory+=supplied
    # Stage only into a new directory. Input paths and all embedded archive bytes
    # remain untouched. Invalid inputs can be preserved but never promoted.
    output.mkdir(parents=True,exist_ok=False);(output/'artifacts').mkdir()
    def remember(path):
        path=Path(path).resolve()
        item={'path':str(path),'sha256':file_hash(path)}
        if item not in dependencies:dependencies.append(item)
    remember(capture_path/'manifest.json')
    for path in (inventory_path,evidence_path):
        if path:remember(path)
    for item in inventory:
        role=item.get('role') if isinstance(item,dict) else None
        try:
            if role not in ROLES:raise ValueError('unsupported artifact role')
            if role in records:raise ValueError('duplicate role (never choose one silently)')
            original=hashed_file(item);remember(original)
            if role!='ledger' and item.get('game_id')!=game.get('game_id'):raise ValueError('artifact game mismatch')
            if not item.get('reference'):raise ValueError('source reference required')
            if role=='ledger' and Path(str(original)+'-wal').is_file() and Path(str(original)+'-wal').stat().st_size:
                raise ValueError('ledger archive has uncheckpointed WAL evidence')
            if role!='ledger':
                times=[stamp(item[k]) for k in ('published_at','available_at','observed_at')]
                if not times[0]<=times[1]<=times[2]<=cutoff:raise ValueError('invalid artifact availability order')
                if times[2]>stamp(capture['decision_cutoff']):raise ValueError('artifact after decision cutoff')
                if 'frozen_at' in item and not times[2]<=stamp(item['frozen_at'])<=stamp(capture['decision_cutoff']):
                    raise ValueError('captured artifact frozen after decision cutoff')
            target=output/'artifacts'/(role+('.sqlite' if role=='ledger' else '.json'))
            raw=original.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('artifact changed during ingestion')
            with target.open('xb') as stream:stream.write(raw)
            if role!='ledger':payloads[role]=json.loads(raw)
            records[role]={'path':str(target),'sha256':item['sha256']}
        except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
            reasons.append(f'{role or "unknown role"}: {exc}')
    for role,key in (('opportunity','opportunity_snapshot'),('ledger','ledger')):
        if role in records:
            if key in manifest and manifest[key].get('sha256')!=records[role]['sha256']:
                reasons.append(f'{role}: conflicting supplied evidence artifact')
            else:manifest[key]=records[role]
    # Inspect every referenced archive before any audit, preserving original
    # paths because frozen backtest selection JSON binds them exactly.
    def inspect(value):
        if isinstance(value,dict):
            if value.get('path') and value.get('sha256'):
                try:remember(hashed_file(value))
                except (OSError,ValueError,KeyError,TypeError) as exc:reasons.append(f'linked artifact: {exc}')
            for child in value.values():inspect(child)
        elif isinstance(value,list):
            for child in value:inspect(child)
    inspect(manifest)
    missing=[role for role in ROLES if role not in records]
    reasons.extend(f'missing genuine {role}' for role in missing)
    audits={'bindings':audit_bindings(payloads,manifest,game,cutoff),
            'opportunity':_safe_audit(audit_opportunity_evidence,manifest.get('opportunity_snapshot',{}),cutoff),
            'calibration':_safe_audit(audit_calibration_evidence,manifest.get('calibration',{}),cutoff),
            'backtest':_safe_audit(audit_backtest_evidence,manifest.get('backtest',{}),cutoff),
            'ledger':_safe_audit(audit_ledger_evidence,manifest.get('ledger',{}),cutoff)}
    binding=audits['bindings']['evidence']
    if audits['bindings']['status']=='PASS':
        model=payloads['model'];markets=binding['markets']
        audits['capabilities']=audit_capabilities(model['artifact'].get('capabilities',{}),model_sha256=model['sha256'],
            markets=markets,game_id=game['game_id'],profile_versions=binding['team_profile_versions'],as_of=cutoff,
            model_artifact=model['artifact'],coverage=manifest.get('metric_capabilities',{}).get('coverage'),kickoff=game['kickoff'])
        from research.evidence_scope import bind_game_evidence
        scope={**manifest['scope'],'audit_mode':'game',**{k:binding[k] for k in
               ('game_id','kickoff','team_profile_versions','model_sha256','model_version')}}
        manifest['scope']=scope
        for name,role in (('opportunity','remaining'),('calibration','correlation'),('backtest','scenario'),('ledger','ledger')):
            audits[name]=bind_game_evidence(audits[name],role,scope,manifest)
    for name,audit in audits.items():
        reasons.extend(f'{name}: {reason}' for reason in audit['reasons'])
    changed=[d['path'] for d in dependencies if not Path(d['path']).is_file() or file_hash(d['path'])!=d['sha256']]
    if changed:reasons.append('inputs changed during assessment')
    manifest_path=output/'evidence-manifest.json'
    manifest_path.write_text(json.dumps(manifest,indent=2,allow_nan=False),encoding='utf-8')
    report={'schema':'READINESS_EVIDENCE_V1','as_of':cutoff.isoformat(),
            'status':'BLOCKED' if reasons else 'EVIDENCE_CONSISTENT','betting_ready':False,
            'manifest_path':str(manifest_path),'report_path':str(output/'report.json'),
            'scope':manifest['scope'],'missing_roles':missing,'reasons':list(dict.fromkeys(reasons)),
            'capture_verification':verification,'audits':audits,'artifacts':records,'dependencies':dependencies,
            'changed_inputs':changed,'sample_sufficiency':'NOT_ASSESSED','performance_approval':'NOT_ASSESSED',
            'historical_authenticity':'NOT_ESTABLISHED','seven_auditor_rerun_required':True}
    (output/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    sealed=[{'path':p.relative_to(output).as_posix(),'sha256':file_hash(p)} for p in output.rglob('*') if p.is_file()]
    (output/'seal.json').write_text(json.dumps(sealed,indent=2),encoding='utf-8')
    return report


def verify_pack(output, *, expected_seal_sha256=None):
    """Recheck sealed file inventory and original linked dependencies read-only."""
    root=Path(output).resolve();reasons=[]
    try:
        raw=(root/'seal.json').read_bytes()
        if expected_seal_sha256 is not None and hashlib.sha256(raw).hexdigest()!=expected_seal_sha256:
            raise ValueError('external seal hash mismatch')
        inventory=json.loads(raw);names=[r['path'] for r in inventory]
        if len(names)!=len(set(names)):raise ValueError('duplicate sealed path')
        actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
        if actual!=set(names)|{'seal.json'}:raise ValueError('sealed file inventory changed')
        for record in inventory:
            p=root/record['path']
            if not p.resolve().is_relative_to(root) or p.is_symlink():raise ValueError('unsafe sealed path')
            if file_hash(p)!=record['sha256']:raise ValueError('sealed artifact changed')
        report=json.loads((root/'report.json').read_bytes())
        for record in report['dependencies']:hashed_file(record)
    except (OSError,ValueError,KeyError,TypeError) as exc:reasons.append(str(exc))
    return outcome(reasons,{'external_seal_checked':expected_seal_sha256 is not None,
                            'meaning':'byte integrity only, not authenticity or betting approval'})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--inventory',type=Path);parser.add_argument('--evidence',type=Path)
    parser.add_argument('--expected-capture-sha256')
    args=parser.parse_args()
    report=run(args.capture,args.output,inventory_path=args.inventory,evidence_path=args.evidence,
               expected_capture_sha256=args.expected_capture_sha256)
    print(json.dumps({k:report[k] for k in ('status','manifest_path','missing_roles','betting_ready')}))
    return 2 if report['status']=='BLOCKED' else 0


if __name__=='__main__':raise SystemExit(main())
