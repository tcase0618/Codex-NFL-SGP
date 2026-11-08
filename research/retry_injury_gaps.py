"""Retry only missing club injury sources; never infer an active player."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from football_engine.team_profiles import content_hash, timestamp
from .collect_team_context import fetch_page
from .source_gap_repair import repair_injury
from .source_based_parlays import DOMAINS


def retry_injury_gaps(profiles, gaps, attempts, archive, *, now=None, fetch=fetch_page, expected_games=None):
    """Keep failures visible; accept only a report newer than the last final.

    A successful replacement proves current report context only. Stable player
    identities, complete transactions and game-day activity remain separate gates.
    Retries run only when the caller invokes this helper. A one-hour cooldown
    does not create a schedule or override game-day-morning automation settings.
    Missing or mismatched upcoming game scope cannot clear a source gap.
    """
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("aware retry time required")
    updated, pending, checks = deepcopy(profiles), deepcopy(gaps), deepcopy(attempts)
    changes, sources, due = [], [], []
    expected_games = expected_games or {}
    for gap in gaps:
        team = gap.get("team")
        if gap.get("kind") != "injury" or team not in DOMAINS or team not in updated:
            continue
        previous = checks.get(team)
        if previous:
            try:
                elapsed = now - timestamp(previous)
                if timedelta(0) <= elapsed < timedelta(hours=1):
                    continue
            except (ValueError, TypeError, AttributeError):
                pass
        checks[team] = now.isoformat()
        if team not in due:
            due.append(team)

    def retrieve(team):
        reference = f"https://www.{DOMAINS[team]}.com/team/injury-report/"
        try:
            return team, repair_injury(fetch((team, "injury", reference), Path(archive)), Path(archive))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return team, dict(team=team, kind="injury", reference=reference, status="FAILED",
                              eligibility="UNKNOWN", error=str(exc), rows=[])

    with ThreadPoolExecutor(max_workers=8) as pool:
        retrieved = list(pool.map(retrieve, due))
    for team, page in retrieved:
        sources.append(page)
        profile = updated[team]
        scope = page.get("report_scope", {})
        target = expected_games.get(team, {})
        try:
            kickoff = timestamp(target["kickoff"])
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        # A table for a different game/week cannot clear the upcoming-game gap.
        if (page.get("status") != "parsed" or not page.get("rows")
                or scope.get("phase") != "REG" or type(scope.get("week")) is not int
                or scope["week"] != target.get("week") or target.get("season_type") != 2
                or target.get("team_id") != team or not target.get("game_id")
                or kickoff <= now):
            continue
        observed = timestamp(page["retrieved_at"])
        season = now.year - (now.month <= 2)
        if (scope.get("season", season) != season or profile.get("season_year") != season
                or target.get("season") != season
                or not timedelta(0) <= observed - now <= timedelta(minutes=5)):
            continue
        personnel = profile.get("personnel", {})
        records = personnel.get("source_records", [])
        if sum(record.get("kind") == "injury" for record in records) != 1:
            continue
        fields = ("kind", "reference", "retrieved_at", "content_sha256", "status", "html_file",
                  "report_scope", "parse_errors", "reason", "eligibility")
        personnel["source_records"] = [
            {key: page.get(key) for key in fields} if r.get("kind") == "injury" else r
            for r in records]
        personnel["injury_report_rows"] = page["rows"]
        personnel["injury_coverage"] = {key: page.get(key) for key in
            ("status", "report_scope", "reason", "eligibility", "historical_reports")}
        personnel["injury_coverage"]["scheduled_game_context"] = deepcopy(target)
        profile["as_of"] = max(timestamp(profile["as_of"]), observed).isoformat()
        reason = "current injury report unavailable or incomplete; confirm eligibility before selection"
        profile["blocking_review_reasons"] = [r for r in profile.get("blocking_review_reasons", []) if r != reason]
        profile["version"] = content_hash({k:v for k,v in profile.items() if k != "version"})
        pending = [g for g in pending if not (g.get("team") == team and g.get("kind") == "injury")]
        changes.append(team)
    return updated, pending, checks, changes, sources
