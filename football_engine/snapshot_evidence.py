"""Decode immutable evidence into engine types; no fabricated defaults."""
from datetime import datetime
import hashlib
import json
from pathlib import Path

from .engine import GameState, Leg, Player, Scenario, Snapshot, Source, TicketQuote, evaluate_ticket, leg_key
from .accuracy import evidence_fingerprint


def stamp(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.utcoffset() is None:raise ValueError('aware evidence timestamp required')
    return parsed


def source(value):
    return Source(value['provider'],value['reference'],stamp(value['observed_at']))


def decode(value):
    players={pid:Player(p['player_id'],p['team_id'],p['active'],frozenset(p['eligible_markets']),source(p['source'])) for pid,p in value['players'].items()}
    qb_context={team:{**item,'source':source(item['source'])} for team,item in value.get('qb_context',{}).items()}
    scenarios=[]
    for s in value['scenarios']:
        scenarios.append(Scenario(s['name'],s['weight'],s['remaining'],s['yards_per_opportunity'],s['touchdown_rate'],source(s['source']),
            s.get('maximum_remaining',{}),tuple(s.get('joint_samples',[])),s.get('passing_td_per_attempt',{}),tuple(source(v) for v in s.get('role_sources',[])),
            frozenset(s.get('qb_change_teams',[]))))
    state=value.get('state')
    if state is not None:state=GameState(state['seconds_remaining'],state['status'],source(state['source']),state.get('scores',{}))
    return Snapshot(value['game_id'],stamp(value['as_of']),stamp(value['kickoff']),players,tuple(scenarios),source(value['source']),state,value.get('team_profile_versions',{}),
                    frozenset(value.get('qb_change_teams',[])), source(value['qb_change_source']) if value.get('qb_change_source') else None,
                    qb_context)


def audit_opportunity_evidence(manifest, as_of):
    evidence={'snapshot_verified':False,'engine_checks_passed':False};reasons=[]
    try:
        if not isinstance(manifest,dict) or not manifest.get('path'):
            return {'status':'BLOCKED','reasons':['missing frozen game-scoped opportunity snapshot'],'evidence':evidence}
        cutoff=as_of if isinstance(as_of,datetime) else stamp(as_of)
        if cutoff.utcoffset() is None:raise ValueError('aware cutoff required')
        path=Path(manifest['path'])
        if not path.is_absolute():raise ValueError('absolute snapshot archive required')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=manifest.get('sha256'):raise ValueError('opportunity snapshot hash mismatch')
        value=json.loads(raw);snapshot=decode(value['snapshot'])
        if not 0<=(cutoff-snapshot.as_of).total_seconds()<=300:raise ValueError('game snapshot future or stale')
        legs=tuple(Leg(l['player_id'],l['team_id'],l['market'],l['line'],l['current'],l['odds_decimal'],source(l['source'])) for l in value['legs'])
        decision=evaluate_ticket(snapshot,legs)
        evidence.update(snapshot_verified=True,game_id=snapshot.game_id,as_of=snapshot.as_of.isoformat(),engine_auditors=decision.audit,engine_skills=decision.skills,sha256=manifest['sha256'],path=str(path),engine_checks_passed=decision.approved)
        evidence.update(team_profile_versions=snapshot.team_profile_versions,model_sha256=value.get('model_sha256'),kickoff=snapshot.kickoff.isoformat(),
                        snapshot_fingerprint=evidence_fingerprint(snapshot),
                        leg_fingerprints=sorted(evidence_fingerprint(l) for l in legs))
        model_hash=value.get('model_sha256')
        if not isinstance(model_hash,str) or len(model_hash)!=64 or any(c not in '0123456789abcdef' for c in model_hash):
            reasons.append('opportunity snapshot missing model artifact binding')
        if not decision.approved:reasons.extend(decision.reasons)
        quote=value.get('quote')
        if not isinstance(quote,dict):raise ValueError('exact combined ticket quote missing')
        from .engine import compare_ticket_prices
        q=TicketQuote(quote['decimal_odds'],source(quote['source']),tuple(quote['leg_keys']))
        if not compare_ticket_prices(snapshot,{'audit':(legs,q)}):reasons.append('combined quote failed exact leg/source validation')
    except (ValueError,KeyError,TypeError,AttributeError,OSError,OverflowError) as exc:
        reasons.append(f'opportunity evidence invalid: {exc}')
    return {'status':'BLOCKED' if reasons else 'PASS','reasons':list(dict.fromkeys(reasons)),'evidence':evidence}
