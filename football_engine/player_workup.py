"""Per-player pregame injury and team review for audited ticket construction.

These checks bind archived evidence to the seven-auditor report. A matching
hash and reviewer claim do not authenticate a publisher or prove health.
"""

from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse


MAX_AGE = {
    "roster": timedelta(hours=72),
    "injury": timedelta(hours=72),
    "transactions": timedelta(hours=24),
    "active": timedelta(hours=6),
}
FINDINGS = {
    "roster": "on_team_roster",
    "transactions": "same_team_through_cutoff",
    "active": "confirmed_active",
}
CLEAR_INJURY = {"not_listed_on_complete_report", "listed_without_game_designation"}


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _artifact(entry, kind, player_id, team_id, as_of, verified):
    if not isinstance(entry, dict):
        return f"{kind} archive missing"
    if entry.get("player_id") != player_id or entry.get("team_id") != team_id:
        return f"{kind} player/team identity mismatch"
    reference = entry.get("reference")
    parsed = urlparse(reference) if isinstance(reference, str) else None
    if not parsed or parsed.scheme != "https" or not parsed.hostname:
        return f"{kind} source reference missing or not HTTPS"
    observed = _time(entry.get("observed_at"))
    if observed is None or not timedelta(0) <= as_of - observed <= MAX_AGE[kind]:
        return f"{kind} source is future, stale, or undated"
    path_text = entry.get("path")
    digest = entry.get("sha256")
    if not isinstance(path_text, str) or not Path(path_text).is_absolute():
        return f"{kind} absolute archive path missing"
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return f"{kind} archive SHA-256 missing"
    try:
        path = Path(path_text).resolve(strict=True)
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != digest:
            return f"{kind} archive hash mismatch"
    except (OSError, ValueError):
        return f"{kind} archive unavailable"
    if (str(path).casefold(), digest) not in verified:
        return f"{kind} archive not bound to auditor artifacts"
    return None


def verify_player_workups(snapshot, offered_legs, accuracy_report):
    """Return explicit blockers for every unique player on the offered ticket pool."""
    if not isinstance(accuracy_report, dict):
        return ("missing per-player injury and team workup report",)
    auditors = accuracy_report.get("auditors")
    roster_audit = auditors.get("roster", {}) if isinstance(auditors, dict) else {}
    evidence = roster_audit.get("evidence", {}) if isinstance(roster_audit, dict) else {}
    workups = evidence.get("player_workups") if isinstance(evidence, dict) else None
    if not isinstance(workups, dict):
        return ("missing per-player injury and team workups",)
    artifacts = accuracy_report.get("verified_artifacts")
    if not isinstance(artifacts, list):
        return ("missing bound player workup artifacts",)
    try:
        verified = {(str(Path(item["path"]).resolve(strict=True)).casefold(), item["sha256"])
                    for item in artifacts if isinstance(item, dict)}
    except (KeyError, TypeError, OSError, ValueError):
        return ("invalid bound player workup artifacts",)
    selected = {leg.player_id: leg.team_id for leg in offered_legs}
    reasons = []
    if any(selected[leg.player_id] != leg.team_id for leg in offered_legs):
        reasons.append("one player appears on conflicting teams in the offered pool")
    if set(workups) != set(selected):
        reasons.append("player workups must exactly cover every offered player")
    for player_id, team_id in sorted(selected.items()):
        record = workups.get(player_id)
        if not isinstance(record, dict):
            reasons.append(f"{player_id}: player workup missing")
            continue
        if (record.get("player_id") != player_id or record.get("team_id") != team_id
                or record.get("game_id") != snapshot.game_id
                or record.get("as_of") != snapshot.as_of.isoformat()):
            reasons.append(f"{player_id}: workup game, player, team, or snapshot mismatch")
            continue
        reviewed = _time(record.get("reviewed_at"))
        if (reviewed is None or not timedelta(0) <= snapshot.as_of - reviewed <= timedelta(minutes=30)
                or not isinstance(record.get("reviewer_reference"), str)
                or not record["reviewer_reference"].strip()):
            reasons.append(f"{player_id}: reviewer or fresh review time missing")
        for kind in MAX_AGE:
            entry = record.get(kind)
            problem = _artifact(entry, kind, player_id, team_id, snapshot.as_of, verified)
            if problem:
                reasons.append(f"{player_id}: {problem}")
                continue
            if kind == "injury":
                if entry.get("complete_team_report") is not True:
                    reasons.append(f"{player_id}: complete current injury report unverified")
                if entry.get("finding") not in CLEAR_INJURY:
                    reasons.append(f"{player_id}: injury designation requires review or player is unavailable")
            elif entry.get("finding") != FINDINGS[kind]:
                reasons.append(f"{player_id}: {kind} finding does not confirm eligibility")
        player = snapshot.players.get(player_id)
        if player is None or player.team_id != team_id or player.active is not True:
            reasons.append(f"{player_id}: snapshot player/team/active mismatch")
    return tuple(dict.fromkeys(reasons))
