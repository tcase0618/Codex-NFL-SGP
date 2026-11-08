"""Scenario-specific target redistribution with explicit evidence and capacity.

Depth rank identifies candidates; it does not determine target shares. Caller
supplies supported scenario allocations (routes, personnel, coach reporting).
No TD conversion or yardage efficiency is inferred from an injured teammate.
"""
from dataclasses import dataclass, replace
from datetime import datetime
from math import isfinite

from .engine import Scenario, Source, _valid_source


@dataclass(frozen=True)
class ReceivingRole:
    player_id: str
    team_id: str
    active: bool
    baseline_targets: float
    target_capacity: float
    replacement_weight: float
    source: Source
    # "out" is scenario-specific; questionable players need separate scenarios.
    availability: str = "available"


def apply_receiving_roles(scenario: Scenario, roles: tuple[ReceivingRole, ...], *,
                          as_of: datetime, team_id: str, team_target_budget: float,
                          player_teams: dict[str, str],
                          max_source_age_seconds: int = 300) -> Scenario:
    if not roles or len({r.player_id for r in roles}) != len(roles):
        raise ValueError("missing or duplicate receiving roles")
    if not isfinite(team_target_budget) or team_target_budget < 0:
        raise ValueError("invalid team target budget")
    for role in roles:
        if (role.team_id != team_id or player_teams.get(role.player_id) != team_id
                or role.availability not in ("available", "out")
                or not all(isfinite(v) and v >= 0 for v in
                           (role.baseline_targets, role.target_capacity, role.replacement_weight))
                or role.baseline_targets > role.target_capacity
                or not _valid_source(role.source, as_of)
                or (as_of - role.source.observed_at).total_seconds() > max_source_age_seconds):
            raise ValueError("invalid, ambiguous, stale or future role evidence")
        if not role.active and role.availability != "out":
            raise ValueError("inactive recipient cannot inherit targets")
    if sum(r.baseline_targets for r in roles) > team_target_budget + 1e-9:
        raise ValueError("baseline exceeds team target budget")
    amounts = {r.player_id: r.baseline_targets if r.availability == "available" else 0.0 for r in roles}
    vacant = sum(r.baseline_targets for r in roles if r.availability == "out")
    # Capped weighted allocation leaves unsupported opportunity unassigned.
    while vacant > 1e-9:
        recipients = [r for r in roles if r.active and r.availability == "available"
                      and r.replacement_weight > 0 and r.target_capacity - amounts[r.player_id] > 1e-9]
        weight = sum(r.replacement_weight for r in recipients)
        if not weight:
            break
        distributed = 0.0
        for role in recipients:
            increment = min(vacant * role.replacement_weight / weight,
                            role.target_capacity - amounts[role.player_id])
            amounts[role.player_id] += increment
            distributed += increment
        vacant -= distributed
    remaining = {pid: dict(volume) for pid, volume in scenario.remaining.items()}
    for role in roles:
        if role.availability == "out":
            remaining[role.player_id] = {key: 0.0 for key in remaining.get(role.player_id, {})}
        remaining.setdefault(role.player_id, {})["targets"] = amounts[role.player_id]
    # Caller supplies the full team's receiving roles, including zero-target
    # rookies/backups. Reject unknown existing target recipients.
    role_ids = {r.player_id for r in roles}
    if any((pid not in player_teams or (player_teams[pid] == team_id and pid not in role_ids))
           and volume.get("targets", 0) > 0
           for pid, volume in scenario.remaining.items()):
        raise ValueError("role list must cover existing target recipients")
    if sum(remaining[r.player_id]["targets"] for r in roles) > team_target_budget + 1e-9:
        raise ValueError("projected targets exceed team budget")
    # Old joint samples describe the old workload. Require resimulation before
    # any multi-leg ticket can pass the existing correlation auditor.
    return replace(scenario, remaining=remaining, joint_samples=(),
                   role_sources=scenario.role_sources + tuple(r.source for r in roles))
