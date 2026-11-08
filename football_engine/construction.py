"""Reviewable mixed-market candidates; engine approval remains mandatory."""
from dataclasses import dataclass
from itertools import combinations

from .engine import Decision, Leg, Snapshot, evaluate_ticket, leg_key


@dataclass(frozen=True)
class Construction:
    legs: tuple[Leg, ...]
    decision: Decision


@dataclass(frozen=True)
class AuditedConstructions:
    status: str
    reasons: tuple[str, ...]
    candidates: tuple[Construction, ...]


def construct_audited_tickets(snapshot, offered_legs, length, *, accuracy_report,
                             model_version, max_candidates=20000, audit_checks=None,
                             market_capture_path=None, market_capture_sha256=None):
    """Production-facing research entry point; evidence gates precede selection."""
    from .accuracy import check_clearance, evidence_fingerprint

    from .market_first import verify_offered_pool
    preflight = verify_offered_pool(snapshot, offered_legs, market_capture_path,
                                    expected_sha256=market_capture_sha256)
    if preflight.status != 'PASS':
        return AuditedConstructions('BLOCKED', preflight.reasons, ())
    clearance = check_clearance(accuracy_report, snapshot, model_version=model_version, checks=audit_checks)
    if not clearance.cleared:
        return AuditedConstructions('BLOCKED', clearance.reasons, ())
    from pathlib import Path
    artifacts = accuracy_report.get('verified_artifacts', ())
    market_evidence = accuracy_report['auditors']['market']['evidence']
    if (market_evidence.get('market_capture_sha256') != preflight.archive_sha256
            or not any(Path(item['path']).resolve() == Path(market_capture_path).resolve()
                       and item['sha256'] == preflight.archive_sha256
                       for item in artifacts)):
        return AuditedConstructions('BLOCKED',
                                    ('market capture is not bound to audited evidence',), ())
    try:
        if not isinstance(offered_legs,(tuple,list)) or any(not isinstance(leg,Leg) for leg in offered_legs):
            raise ValueError('typed offered legs required')
        actual=sorted(evidence_fingerprint(leg) for leg in offered_legs)
    except (ValueError,TypeError,AttributeError,OverflowError):
        return AuditedConstructions('BLOCKED',('malformed offered market evidence',),())
    expected=accuracy_report['auditors']['remaining']['evidence'].get('leg_fingerprints')
    if actual!=expected:
        return AuditedConstructions('BLOCKED',('offered legs differ from audited prices, totals or sources',),())
    candidates = construct_tickets(snapshot, offered_legs, length, max_candidates=max_candidates)
    return AuditedConstructions('RESEARCH_ONLY', ('Independent price and game-day verification still required.',), candidates)


def construct_tickets(snapshot: Snapshot, offered_legs: tuple[Leg, ...], length: int,
                      *, max_candidates: int = 20000) -> tuple[Construction, ...]:
    """Allow QB yards + QB passing TD; exclude nested same-market thresholds.

Inputs must be real offered markets, not synthetic lines. Exact combined
book quotes are still required by screen_prices after this approval step.
"""
    if length not in (3, 5, 8) or max_candidates < 1:
        raise ValueError("invalid construction size or search bound")
    if len({leg_key(leg) for leg in offered_legs}) != len(offered_legs):
        raise ValueError("duplicate offered market")
    from math import comb
    if len(offered_legs) < length:
        return ()
    if comb(len(offered_legs), length) > max_candidates:
        raise ValueError("candidate search exceeds bound; narrow offered pool explicitly")
    approved = []
    for legs in combinations(offered_legs, length):
        if len({(leg.player_id, leg.market) for leg in legs}) != length:
            continue
        if len({leg.team_id for leg in legs}) != 2:
            continue
        decision = evaluate_ticket(snapshot, legs)
        if decision.approved:
            approved.append(Construction(legs, decision))
    return tuple(approved)


def construct_anytime_td_tickets(snapshot: Snapshot, offered_legs: tuple[Leg, ...],
                                 *, max_candidates: int = 20000) -> tuple[Construction, ...]:
    """Build a distinct three-scorer family from actual anytime-TD offers.

    The ordinary engine still checks game-day eligibility, scoring opportunity,
    common game scripts, and joint samples for every candidate.
    """
    if not isinstance(offered_legs, (tuple, list)) or any(
            not isinstance(leg, Leg) or leg.market != "anytime_td" or leg.line != 1
            for leg in offered_legs):
        raise ValueError("three-scorer family requires anytime-TD offers at line 1")
    return construct_tickets(snapshot, tuple(offered_legs), 3,
                             max_candidates=max_candidates)


def construct_audited_anytime_td_tickets(snapshot, offered_legs, *, accuracy_report,
                                         model_version, max_candidates=20000,
                                         audit_checks=None, market_capture_path=None,
                                         market_capture_sha256=None):
    """Apply the same evidence clearance as the mixed-market family."""
    if not isinstance(offered_legs, (tuple, list)) or any(
            not isinstance(leg, Leg) or leg.market != "anytime_td" or leg.line != 1
            for leg in offered_legs):
        return AuditedConstructions('BLOCKED',
                                    ('three-scorer family requires anytime-TD offers at line 1',), ())
    return construct_audited_tickets(snapshot, offered_legs, 3,
                                     accuracy_report=accuracy_report,
                                     model_version=model_version,
                                     max_candidates=max_candidates,
                                     audit_checks=audit_checks,
                                     market_capture_path=market_capture_path,
                                     market_capture_sha256=market_capture_sha256)
