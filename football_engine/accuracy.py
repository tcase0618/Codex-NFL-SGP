"""Accuracy policy independent of agent prose or a majority vote.

Research calculations and permission to use a forecast are separate decisions.
Every required auditor must pass; an error, missing role, or stale report blocks.
"""
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from .engine import AUDITORS


def report_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def evidence_fingerprint(value):
    """Stable semantic fingerprint across typed snapshot and process boundaries."""
    def normalize(item):
        if is_dataclass(item):return normalize(asdict(item))
        if isinstance(item,datetime):
            if item.utcoffset() is None:raise ValueError('aware fingerprint time required')
            return item.astimezone(timezone.utc).isoformat()
        if isinstance(item,dict):return {k:normalize(v) for k,v in item.items()}
        if isinstance(item,(set,frozenset)):return sorted(normalize(v) for v in item)
        if isinstance(item,(tuple,list)):return [normalize(v) for v in item]
        return item
    return report_hash(normalize(value))


def run_auditors(checks, *, as_of, scope=None, verified_artifacts=()):
    """Execute seven independent checks; absent checks and exceptions block."""
    if as_of.tzinfo is None:
        raise ValueError('aware audit cutoff required')
    def execute(name):
        try:
            result = checks[name]()
            if (not isinstance(result, dict) or result.get('status') not in ('PASS','BLOCKED') or
                    not isinstance(result.get('reasons'), list) or not isinstance(result.get('evidence'), dict)):
                raise ValueError('invalid auditor result')
            if result['status'] == 'PASS' and (result['reasons'] or not result['evidence']):
                raise ValueError('pass requires evidence and no blockers')
            if any(not isinstance(reason,str) or not reason.strip() for reason in result['reasons']):
                raise ValueError('auditor reasons must be meaningful text')
            report_hash(result)
            return name, deepcopy(result)
        except Exception as exc:
            return name, {'status':'BLOCKED','reasons':[f'{name} auditor unavailable or failed: {type(exc).__name__}'],
                          'evidence':{'check_completed':False}}
    with ThreadPoolExecutor(max_workers=7) as pool:
        results = dict(pool.map(execute, AUDITORS))
    report = {'kind':'ACCURACY_SWARM_V1','as_of':as_of.isoformat(),'scope':scope or {},
              'status':'PASS' if all(r['status']=='PASS' for r in results.values()) else 'BLOCKED',
              'auditors':results,'verified_artifacts':list(verified_artifacts),
              'betting_ready':False,'accuracy_percentage':None,
              'meaning':'Evidence checks; no agent vote or test count establishes predictive accuracy.'}
    report['report_hash'] = report_hash(report)
    return report


@dataclass(frozen=True)
class AccuracyClearance:
    cleared: bool
    status: str
    reasons: tuple[str, ...]


def check_clearance(report, snapshot, *, model_version, max_age_seconds=300, checks=None):
    reasons = []
    if not isinstance(report, dict):
        return AccuracyClearance(False, 'BLOCKED', ('missing accuracy report',))
    if checks is None:
        reasons.append('independent audit re-execution required; saved PASS labels are not authority')
    try:
        expected = report_hash({k:v for k,v in report.items() if k != 'report_hash'})
        if report.get('report_hash') != expected:
            reasons.append('accuracy report changed or hash missing')
        observed = datetime.fromisoformat(report['as_of'].replace('Z', '+00:00'))
        if observed.tzinfo is None or not 0 <= (snapshot.as_of-observed).total_seconds() <= max_age_seconds:
            reasons.append('accuracy report future or stale')
        scope = report.get('scope', {})
        if scope.get('game_id') != snapshot.game_id:
            reasons.append('accuracy report does not cover this game')
        if not model_version or scope.get('model_version') != model_version:
            reasons.append('accuracy report does not cover this model version')
        if (not snapshot.team_profile_versions or
                scope.get('team_profile_versions') != snapshot.team_profile_versions):
            reasons.append('accuracy report does not cover current profile versions')
        auditors = report.get('auditors', {})
        if set(auditors) != set(AUDITORS):
            reasons.append('all seven accuracy auditors required')
        for name in AUDITORS:
            result = auditors.get(name, {})
            if result.get('status') != 'PASS' or result.get('reasons') or not result.get('evidence'):
                reasons.append(f'{name} accuracy audit not passed with evidence')
        remaining=auditors.get('remaining',{}).get('evidence',{})
        model_hash=scope.get('model_sha256')
        if (not isinstance(model_hash,str) or len(model_hash)!=64 or
                any(c not in '0123456789abcdef' for c in model_hash) or
                auditors.get('correlation',{}).get('evidence',{}).get('model_sha256')!=model_hash or
                remaining.get('model_sha256')!=model_hash):
            reasons.append('model artifact not bound across calibration and opportunity evidence')
        if (remaining.get('game_id')!=snapshot.game_id or
                remaining.get('team_profile_versions')!=snapshot.team_profile_versions or
                remaining.get('snapshot_fingerprint')!=evidence_fingerprint(snapshot)):
            reasons.append('opportunity audit does not match exact current snapshot')
        if report.get('status') != 'PASS':
            reasons.append('accuracy report not passed')
        # Revalidate linked evidence at use time. Report hashes detect changed
        # reports; they cannot substitute for the underlying frozen artifacts.
        artifacts = report.get('verified_artifacts', [])
        if not artifacts:
            reasons.append('no inspectable accuracy evidence artifacts')
        from pathlib import Path
        for item in artifacts:
            path = Path(item['path'])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != item['sha256']:
                reasons.append('accuracy evidence artifact changed')
        if checks is not None:
            rerun=run_auditors(checks,as_of=snapshot.as_of,scope=scope,verified_artifacts=artifacts)
            if rerun['status']!='PASS':
                reasons.append('independent accuracy audit re-execution blocked')
            if rerun['auditors']!=auditors:
                reasons.append('accuracy evidence differs from independent audit results')
    except (ValueError, TypeError, KeyError, OSError, OverflowError, AttributeError):
        reasons.append('malformed or unavailable accuracy evidence')
    return AccuracyClearance(not reasons, 'RESEARCH_CANDIDATES_ALLOWED' if not reasons else 'BLOCKED', tuple(dict.fromkeys(reasons)))
