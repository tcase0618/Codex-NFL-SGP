"""Repeatable read-only accuracy audit for the actual football artifacts."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from football_engine.accuracy import run_auditors
from football_engine.team_profiles import content_hash


def time(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    if not isinstance(result,datetime) or result.tzinfo is None:
        raise ValueError('aware time required')
    return result


def result(reasons, evidence):
    return {'status':'BLOCKED' if reasons else 'PASS','reasons':reasons,'evidence':evidence}


def source_audit(profiles, source_roots, as_of, source_gaps, *, restrict_paths=False):
    reasons=[];checked={};claims={};references={};missing=set()
    def visit(item):
        if isinstance(item,dict):
            if item.get('reference') and (item.get('sha256') or item.get('content_sha256')):
                digest=item.get('sha256') or item.get('content_sha256')
                stamp=item.get('observed_at') or item.get('retrieved_at')
                if stamp and time(stamp)>as_of:
                    reasons.append('future source observation')
                path=item.get('archive_file') or item.get('html_file')
                claims.setdefault(digest,set())
                references.setdefault(digest,set()).add(item['reference'])
                if path:claims[digest].add(path)
            for value in item.values():visit(value)
        elif isinstance(item,list):
            for value in item:visit(value)
    for team,p in profiles.items():
        if p.get('version')!=content_hash({k:v for k,v in p.items() if k!='version'}):
            reasons.append(f'{team}: profile content hash mismatch')
        if not 0 <= (as_of-time(p['as_of'])).total_seconds() <= 86400:
            reasons.append(f'{team}: profile future or older than 24 hours')
        visit(p)
    for digest,paths in claims.items():
        if not paths:
            paths={str(root/(digest+suffix)) for root in source_roots for suffix in ('.html','.json','.png','.pdf','.csv','.gz') if (root/(digest+suffix)).is_file()}
            # Legacy ESPN cache names hash the URL, not the response. The
            # response still must match the recorded content hash below.
            for reference in references[digest]:
                filename=hashlib.sha256(reference.encode()).hexdigest()+'.json'
                paths.update(str(root/filename) for root in source_roots if (root/filename).is_file())
        if not paths:missing.add(digest);continue
        for path in paths:
            try:
                if restrict_paths and not any(Path(path).resolve().is_relative_to(root.resolve()) for root in source_roots):
                    reasons.append('source archive outside source roots');continue
                h=hashlib.sha256()
                with Path(path).open('rb') as f:
                    for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
                if h.hexdigest()!=digest:reasons.append('source archive hash mismatch')
                else:checked[path]=digest
            except OSError:missing.add(digest)
    if missing:reasons.append(f'{len(missing)} source claims lack an inspectable matching archive')
    if source_gaps:reasons.append(f'{len(source_gaps)} unresolved upstream source gaps')
    return result(list(dict.fromkeys(reasons)),{'profile_count':len(profiles),'source_hashes_verified':len(checked),'missing_archives':sorted(missing),'source_gaps':source_gaps,'freshness_window_hours':24})


def metric_audit(database, derived=None, as_of=None, profile_hash=None, *, capability_evidence=None, scope=None):
    if not database or not Path(database).is_file():return result(['missing canonical play store'],{'store_available':False})
    from football_engine.team_quant import CALCULATION_REVISION, DEFINITION_REVISION, RULES
    from football_engine.team_metrics import TEAM_CATALOG, TEAM_VIEWS, direction
    reasons=[];capability_report=None
    if capability_evidence is not None or (scope or {}).get('audit_mode')=='game':
        from research.readiness_evidence import audit_capabilities
        try:
            from football_engine.snapshot_evidence import decode
            if (not isinstance(capability_evidence,dict) or not isinstance(capability_evidence.get('model'),dict)
                    or not capability_evidence['model'].get('artifact') or not capability_evidence['model'].get('sha256')):
                raise ValueError('missing hash-bound capability model evidence')
            if (not isinstance(capability_evidence.get('opportunity_snapshot'),dict)
                    or not capability_evidence['opportunity_snapshot'].get('path')
                    or not capability_evidence['opportunity_snapshot'].get('sha256')):
                raise ValueError('missing frozen opportunity evidence for model capabilities')
            model=capability_evidence['model'];record=capability_evidence['opportunity_snapshot']
            raw=Path(record['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise ValueError('capability opportunity hash mismatch')
            payload=json.loads(raw);snapshot=decode(payload['snapshot'])
            if (not scope or scope.get('game_id')!=snapshot.game_id or
                    scope.get('team_profile_versions')!=snapshot.team_profile_versions or
                    scope.get('model_sha256')!=model['sha256'] or payload.get('model_sha256')!=model['sha256']):
                raise ValueError('capability model/opportunity/scope binding mismatch')
            markets=[leg['market'] for leg in payload['legs']]
            capability_report=audit_capabilities(model['artifact'].get('capabilities',{}),model_sha256=model['sha256'],
                markets=markets,game_id=snapshot.game_id,profile_versions=snapshot.team_profile_versions,as_of=as_of,
                model_artifact=model['artifact'],coverage=capability_evidence.get('coverage'),kickoff=snapshot.kickoff)
        except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
            capability_report=result([f'model capability evidence unavailable/invalid: {exc}'],{'requirements':{'lineups':True,'charting':True}})
        reasons.extend(capability_report['reasons'])
    with sqlite3.connect(f'file:{Path(database).as_posix()}?mode=ro',uri=True) as db:
        metadata=dict(db.execute('SELECT key,value FROM metadata'))
        summary=json.loads(metadata['summary'])
        active=metadata.get('active_definition_revision',metadata.get('definition_revision'))
        imported_revision_current=active==DEFINITION_REVISION and metadata.get('calculation_revision')==CALCULATION_REVISION
        if summary.get('missing_pbp_seasons') or summary.get('games_without_plays'):reasons.append('play coverage incomplete')
        if summary.get('score_conflicts_quarantined'):reasons.append('final-score source conflicts remain')
        if len(TEAM_CATALOG)!=401 or len(TEAM_VIEWS)!=24:reasons.append('team catalog contract changed')
        if direction('pass_defense','Explosive pass rate allowed')!='lower':reasons.append('defensive percentile direction invalid')
        lineup_count=db.execute('SELECT COUNT(*) FROM lineups').fetchone()[0]
        charting_count=db.execute('SELECT COUNT(*) FROM play_charting').fetchone()[0]
        if capability_report is None or capability_report['status']!='PASS':
            if not lineup_count:reasons.append('all-11 lineups unavailable for personnel interaction forecasts')
            if not charting_count:reasons.append('charting unavailable for pressure/coverage/concept forecasts')
    current_derivation=False
    if derived:
        from .rebuild_team_derivations import file_hash
        path=Path(derived['path'])
        if file_hash(path)!=derived['sha256']:reasons.append('rebuilt metric snapshot hash mismatch')
        else:
            rebuilt=json.loads(path.read_text())
            current_derivation=(rebuilt['calculation_revision']==CALCULATION_REVISION and rebuilt['definition_revision']==DEFINITION_REVISION
                and rebuilt['source_database_sha256']==file_hash(database) and rebuilt['base_profiles_sha256']==profile_hash
                and time(rebuilt['generated_at'])<=as_of and len(rebuilt['profiles'])==32)
            for p in rebuilt['profiles'].values():
                if p.get('version')!=content_hash({k:v for k,v in p.items() if k!='version'}) or len(p.get('team_quant',{}).get('metrics',[]))!=401:
                    current_derivation=False
            if not current_derivation:reasons.append('rebuilt metrics do not match current source/profile/code versions')
    if not imported_revision_current and not current_derivation:reasons.append('stored derived metrics differ from current reviewed code; rebuild required')
    evidence={'metric_slots':len(TEAM_CATALOG),'direct_rule_slots':len(RULES),'views':len(TEAM_VIEWS),'archived_plays':summary.get('plays'),'calculation_revision':CALCULATION_REVISION,'definition_revision':DEFINITION_REVISION,'lineups':lineup_count,'charted_plays':charting_count,'current_derived_snapshot_verified':current_derivation,'historically_pregame_certified':False}
    if capability_report is not None:evidence['capabilities']=capability_report
    return result(reasons,evidence)


def remaining_audit(evidence, as_of):
    from football_engine.snapshot_evidence import audit_opportunity_evidence
    return audit_opportunity_evidence(evidence.get('opportunity_snapshot',{}), as_of)


def game_scope(state, profiles, game_id, teams, source_roots, as_of):
    """Resolve exact identities from persisted schedule bytes, never from labels."""
    from research.automatic_refresh import BASE, upcoming_game_context
    from research.source_based_parlays import DOMAINS

    if (not isinstance(game_id,str) or not game_id.strip() or
            not isinstance(teams,(list,tuple)) or len(teams)!=2 or
            any(not isinstance(t,str) or t not in DOMAINS or t not in profiles for t in teams) or
            len(set(teams))!=2):
        raise ValueError('game scope requires an exact game_id and two distinct known profile teams')
    schedule=state.get('upcoming_games')
    if not isinstance(schedule,dict):raise ValueError('missing sourced upcoming_games schedule')
    verified=[];contexts={}
    for team in teams:
        row=schedule.get(team)
        if not isinstance(row,dict):raise ValueError('missing team schedule')
        if (row.get('team_id')!=team or row.get('game_id')!=game_id or
                row.get('opponent_id')!=next(t for t in teams if t!=team)):
            raise ValueError('game/team scope does not match upcoming_games')
        source=row.get('schedule_source',{})
        try:
            if not source['reference'].startswith(BASE+'/'):
                raise ValueError('unrecognized schedule source reference')
            path=Path(source['archive_file']).resolve(strict=True)
            if not any(path.is_relative_to(root.resolve()) for root in source_roots):
                raise ValueError('schedule archive outside source roots')
            raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
            if source['sha256']!=digest or time(source['observed_at'])>as_of:
                raise ValueError('schedule archive hash/time mismatch')
            payload=json.loads(raw)
            events=[event for event in payload['events'] if event.get('id')==game_id]
            if len(events)!=1:raise ValueError('schedule archive must contain exactly one matching game')
            actual={item['team_id']:item for item in upcoming_game_context(events[0],source,as_of)}
            if set(actual)!=set(teams) or actual.get(team)!=row:
                raise ValueError('upcoming_games differs from archived upcoming schedule')
        except (OSError,KeyError,TypeError,AttributeError) as exc:
            raise ValueError('missing or malformed schedule archive evidence') from exc
        contexts[team]=row
        item={'path':str(path),'sha256':digest}
        if item not in verified:verified.append(item)
    if time(contexts[teams[0]]['kickoff'])!=time(contexts[teams[1]]['kickoff']):
        raise ValueError('conflicting schedule kickoff')
    selected={team:profiles[team] for team in teams}
    if any(not isinstance(p,dict) or not p for p in selected.values()):
        raise ValueError('empty or malformed scoped profiles')
    return selected,contexts,verified


def run(config_path, database, output, evidence_path=None, as_of=None, *, game_id=None, teams=None):
    as_of=time(as_of) if as_of else datetime.now(timezone.utc)
    config_path=Path(config_path);cfg=json.loads(config_path.read_text());runtime=Path(cfg['runtime']);output=Path(output)
    state=json.loads((runtime/'state.json').read_text());profile_path=Path(state['latest_profiles']);artifact=json.loads(profile_path.read_text());profiles=artifact['profiles']
    if not isinstance(profiles,dict) or not profiles:raise ValueError('nonempty profile snapshot required')
    if game_id is None and teams is None and len(profiles)!=32:raise ValueError('complete all-32 profile snapshot required')
    evidence=json.loads(Path(evidence_path).read_text()) if evidence_path else {}
    if not isinstance(evidence,dict):raise ValueError('evidence manifest must be an object')
    source_roots=[runtime/'sources',runtime.parent/'coaching-sources',runtime.parent/'team-profile-sources',runtime.parent/'espn-cache',runtime.parent/'team-context',runtime.parent/'team-scheme-sources']
    gaps=state.get('context_gaps',[])
    scoped=game_id is not None or teams is not None
    scope=dict(evidence.get('scope',{}));schedule_artifacts=[];contexts={}
    if scoped:
        approved_profile_roots=[runtime]+([Path(cfg['output'])] if cfg.get('output') else [])
        if not any(profile_path.resolve().is_relative_to(root.resolve()) for root in approved_profile_roots):
            raise ValueError('scoped profile snapshot outside configured runtime/output')
        source_roots.append(runtime)
        if cfg.get('output'):source_roots.append(Path(cfg['output']))
        profiles,contexts,schedule_artifacts=game_scope(state,profiles,game_id,teams,source_roots,as_of)
        expected={'game_id':game_id,'team_profile_versions':{t:p['version'] for t,p in profiles.items()}}
        for key,value in expected.items():
            if key in scope and scope[key]!=value:raise ValueError('evidence scope does not match selected game/profiles')
        scope.update(expected)
        scope.update(audit_mode='game',profiles_selected=list(profiles))
        scope['kickoff']=time(contexts[teams[0]]['kickoff']).isoformat()
        # Unattributed/global gaps still block; only explicitly unrelated team
        # gaps are excluded. No evidence checks or thresholds are changed.
        from research.source_based_parlays import DOMAINS
        gaps=[gap for gap in gaps if not isinstance(gap,dict) or gap.get('team') not in DOMAINS or gap['team'] in profiles]
    else:
        scope.update(audit_mode='all_profiles',profiles_selected=list(profiles))
    from football_engine.accuracy_personnel import audit_personnel
    from research.accuracy_backtest import audit_backtest_evidence
    from research.calibration import audit_calibration_evidence
    from football_engine.ledger import audit_ledger_evidence
    personnel={}
    def roster_check():
        for team,p in profiles.items():
            personnel[team]=audit_personnel(p,as_of)
            if scoped and (p.get('team_id')!=team or p.get('game_id')!=game_id or
                    not p.get('kickoff') or time(p['kickoff'])!=time(contexts[team]['kickoff'])):
                personnel[team]=result(personnel[team]['reasons']+['profile does not match sourced game/team/kickoff'],personnel[team]['evidence'])
        blocked=[t for t,r in personnel.items() if r['status']!='PASS']
        return result([f'{len(blocked)} teams lack required game-specific personnel clearance'] if blocked else [],{'teams':personnel,'blocked_teams':blocked})
    checks={'source':lambda:source_audit(profiles,source_roots,as_of,gaps,restrict_paths=scoped),'roster':roster_check,
            'market':lambda:metric_audit(database,evidence.get('derived_metrics'),as_of,hashlib.sha256(profile_path.read_bytes()).hexdigest(),
                capability_evidence={'model':evidence.get('calibration',{}).get('model',{}),
                    'opportunity_snapshot':evidence.get('opportunity_snapshot',{}),
                    'coverage':evidence.get('metric_capabilities',{}).get('coverage',{})} if scoped else None,
                scope=scope if scoped else None),'remaining':lambda:remaining_audit(evidence,as_of),
            'scenario':lambda:audit_backtest_evidence(evidence.get('backtest',{}),as_of),
            'correlation':lambda:audit_calibration_evidence(evidence.get('calibration',{}),as_of),
            'ledger':lambda:audit_ledger_evidence(evidence.get('ledger',{}),as_of)}
    if scoped:
        from research.evidence_scope import bind_game_evidence
        for role in ('remaining','scenario','correlation','ledger'):
            original=checks[role]
            checks[role]=lambda original=original,role=role:bind_game_evidence(original(),role,scope,evidence)
    verified=[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in [profile_path,config_path]+([Path(evidence_path)] if evidence_path else [])]
    verified+=schedule_artifacts
    if scoped:verified.append({'path':str(runtime/'state.json'),'sha256':hashlib.sha256((runtime/'state.json').read_bytes()).hexdigest()})
    report=run_auditors(checks,as_of=as_of,scope=scope,verified_artifacts=verified)
    output.mkdir(parents=True,exist_ok=False)
    (output/'accuracy-report.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    lines=['# Football accuracy swarm','',f"Result: {report['status']}. Predictive accuracy and betting readiness are not established.",'',f'Profiles inspected: {len(profiles)}. Cutoff: {as_of.isoformat()}.','', '| Auditor | Result | Blocking evidence |','|---|---|---|']
    lines += [f"| {name} | {r['status']} | {'; '.join(r['reasons'])} |" for name,r in report['auditors'].items()]
    lines += ['',f"Audit scope: {scope['audit_mode']}; game_id: {scope.get('game_id','not selected')}; profiles selected: {', '.join(profiles)}."]
    lines += ['','A passing software test is not a calibrated forecast. Current-vintage historical files cannot certify old pregame knowledge.','']
    (output/'accuracy-report.md').write_text('\n'.join(lines),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--team-database',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--evidence',type=Path);parser.add_argument('--as-of')
    parser.add_argument('--game-id');parser.add_argument('--teams',nargs=2)
    args=parser.parse_args();report=run(args.config,args.team_database,args.output,args.evidence,args.as_of,game_id=args.game_id,teams=args.teams)
    print(json.dumps({'status':report['status'],'auditors':{n:r['status'] for n,r in report['auditors'].items()},'report':str(args.output/'accuracy-report.md'),'betting_ready':False}))
