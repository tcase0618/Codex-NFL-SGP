"""Reviewable mixed-market candidates; engine approval remains mandatory."""
from dataclasses import dataclass
from datetime import datetime
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


def verify_qb_context(snapshot: Snapshot, offered_legs) -> tuple[str, ...]:
    """Require explicit sourced starter identity for QB-dependent offered markets."""
    from .engine import Source

    if not isinstance(snapshot, Snapshot) or not isinstance(snapshot.qb_context, dict):
        return ("missing game-specific quarterback starter context",)
    teams = {leg.team_id for leg in offered_legs if isinstance(leg, Leg)}
    reasons = []
    for team in sorted(teams):
        context = snapshot.qb_context.get(team)
        if not isinstance(context, dict):
            reasons.append(f"missing game-specific quarterback starter context: {team}")
            continue
        current = context.get("current_starter_id")
        baseline = context.get("baseline_starter_id")
        proof = context.get("source")
        if (not isinstance(current, str) or not current.strip()
                or not isinstance(baseline, str) or not baseline.strip()
                or not isinstance(proof, Source) or not proof.provider or not proof.reference
                or not isinstance(proof.observed_at, datetime) or proof.observed_at.utcoffset() is None
                or not 0 <= (snapshot.as_of - proof.observed_at).total_seconds() <= 300):
            reasons.append(f"quarterback starter identity or source unavailable: {team}")
            continue
        if current != baseline and team not in snapshot.qb_change_teams:
            reasons.append(f"quarterback change not declared in scenario snapshot: {team}")
    return tuple(reasons)


def verify_qb_underperformance(snapshot: Snapshot, offered_legs,
                               *, minimum_weight: float = 0.05) -> tuple[str, ...]:
    """Require one shared, sourced low-QB-output script for dependent markets.

    This validates scenario coverage, not its probability calibration or edge.
    Audited construction uses it before selecting any tickets.
    """
    from .engine import YARDAGE_OPPORTUNITY

    affected_markets = {"passing_yards", "passing_td", "receiving_yards", "anytime_td"}
    teams = {leg.team_id for leg in offered_legs
             if isinstance(leg, Leg) and leg.market in affected_markets}
    reasons = []

    def expected(scenario, leg):
        remaining = scenario.remaining.get(leg.player_id, {})
        if leg.market == "anytime_td":
            volume = remaining.get("unique_scoring_chances")
            if volume is None:
                if "red_zone_touches" not in remaining and "end_zone_targets" not in remaining:
                    return None
                volume = remaining.get("red_zone_touches", 0) + remaining.get("end_zone_targets", 0)
            rate = scenario.touchdown_rate.get(leg.player_id)
        elif leg.market == "passing_td":
            volume = remaining.get("attempts")
            rate = scenario.passing_td_per_attempt.get(leg.player_id)
        else:
            volume = remaining.get(YARDAGE_OPPORTUNITY[leg.market])
            rate = scenario.yards_per_opportunity.get(leg.player_id, {}).get(leg.market)
        return volume * rate if volume is not None and rate is not None else None

    for team in sorted(teams):
        legs = tuple(leg for leg in offered_legs if leg.team_id == team and leg.market in affected_markets)
        low = tuple(s for s in snapshot.scenarios if team in s.qb_underperformance_teams)
        normal = tuple(s for s in snapshot.scenarios if team not in s.qb_underperformance_teams)
        if not low or not normal:
            reasons.append(f"quarterback underperformance scenario missing: {team}")
            continue
        if sum(s.weight for s in low) < minimum_weight:
            reasons.append(f"quarterback underperformance scenario below stress weight: {team}")
        if not any(s.role_sources and all(
                expected(s, leg) is not None
                and all(expected(b, leg) is not None and expected(s, leg) < expected(b, leg)
                        for b in normal)
                for leg in legs) for s in low):
            reasons.append(f"quarterback underperformance does not lower every affected leg: {team}")
    return tuple(reasons)


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
    qb_reasons = verify_qb_context(snapshot, offered_legs)
    if qb_reasons:
        return AuditedConstructions('BLOCKED', qb_reasons, ())
    qb_downside_reasons = verify_qb_underperformance(snapshot, offered_legs)
    if qb_downside_reasons:
        return AuditedConstructions('BLOCKED', qb_downside_reasons, ())
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
    from .player_workup import verify_player_workups
    player_reasons = verify_player_workups(snapshot, offered_legs, accuracy_report)
    if player_reasons:
        return AuditedConstructions('BLOCKED', player_reasons, ())
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
