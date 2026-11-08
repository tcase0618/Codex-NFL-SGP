"""Stable player identity, dated team membership and mandatory scheme rebuilds.

A trade changes membership, not the identity used to join prior statistics.
Current confirmation cannot certify what was known at an earlier kickoff.
"""
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path

from .engine import Snapshot, Source, _valid_source


@dataclass(frozen=True)
class TeamEvent:
    event_id: str
    player_id: str
    from_team: str | None
    to_team: str | None
    effective_at: datetime
    source: Source
    status: str = "confirmed"


@dataclass(frozen=True)
class PlayerProfile:
    player_id: str
    display_name: str
    provider_ids: tuple[tuple[str, str], ...]
    team_events: tuple[TeamEvent, ...] = ()
    # References to immutable prior observations; never relabel the old team.
    history_references: tuple[str, ...] = ()


def save_profile_snapshot(path: Path, profiles: tuple[PlayerProfile, ...]) -> str:
    """Write a new version; refuse replacement or duplicate provider identities."""
    ids = [profile.player_id for profile in profiles]
    aliases = [alias for profile in profiles for alias in profile.provider_ids]
    if len(set(ids)) != len(ids) or len(set(aliases)) != len(aliases):
        raise ValueError("duplicate player or provider identity requires reconciliation")
    payload = [asdict(profile) for profile in profiles]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    with path.open("x", encoding="utf-8") as handle:
        json.dump({"kind": "PLAYER_PROFILE_SNAPSHOT", "profiles_sha256": digest,
                   "profiles": json.loads(encoded)}, handle, indent=2)
    return digest


def record_team_event(profile: PlayerProfile, event: TeamEvent) -> PlayerProfile:
    if (not profile.player_id or event.player_id != profile.player_id or not event.event_id
            or not event.effective_at.tzinfo or not event.source.observed_at.tzinfo
            or not event.source.provider or not event.source.reference
            or event.status not in ("confirmed", "pending")
            or event.from_team == event.to_team
            or any(old.event_id == event.event_id for old in profile.team_events)):
        raise ValueError("invalid or duplicate team event")
    if profile.team_events and event.source.observed_at < profile.team_events[-1].source.observed_at:
        raise ValueError("team events must be recorded in knowledge order")
    if event.status == "confirmed":
        known = [old for old in profile.team_events if old.status == "confirmed"]
        if known and (event.from_team != known[-1].to_team or event.effective_at <= known[-1].effective_at):
            raise ValueError("conflicting team history requires reconciliation")
    return replace(profile, team_events=profile.team_events + (event,))


def team_as_of(profile: PlayerProfile, as_of: datetime) -> str | None:
    if not as_of.tzinfo:
        raise ValueError("timezone required")
    events = [event for event in profile.team_events if event.status == "confirmed"
              and event.effective_at <= as_of and _valid_source(event.source, as_of)]
    return max(events, key=lambda event: event.effective_at).to_team if events else None


def rebuild_after_team_event(snapshot: Snapshot, profile: PlayerProfile, event: TeamEvent) -> Snapshot:
    """Withdraw all old scenarios until both teams and matchup are rebuilt.

    A confirmed roster move does not prove game-day activation, snap share,
    routes, red-zone usage, defensive pressure or a favorable scheme fit.
    """
    if (event not in profile.team_events or event.status != "confirmed"
            or event.effective_at > snapshot.as_of or not _valid_source(event.source, snapshot.as_of)
            or team_as_of(profile, snapshot.as_of) != event.to_team):
        raise ValueError("team event is not confirmed and available at snapshot time")
    players = dict(snapshot.players)
    if event.player_id in players:
        player = players[event.player_id]
        if player.team_id not in (event.from_team, event.to_team):
            raise ValueError("snapshot team conflicts with team event")
        if event.to_team is None:
            players.pop(event.player_id)
        else:
            players[event.player_id] = replace(player, team_id=event.to_team, active=False,
                                               source=event.source)
    # A defender may not be a prop candidate but still changes every matchup
    # projection. Do not keep stale opponent or former-team joint samples.
    return replace(snapshot, players=players, scenarios=(), team_profile_versions={})
