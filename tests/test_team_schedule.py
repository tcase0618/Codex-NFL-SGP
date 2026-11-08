"""Calendar fixtures, not player-eligibility or forecast evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest
import requests

from research.team_schedule import ScheduleProvider, next_game

NOW = datetime(2026, 11, 4, 17, tzinfo=timezone.utc)


def event(identity, date, *, state="pre", opponent="TB", week=9):
    return {"id": identity, "date": date, "week": {"number": week}, "season": {"year": 2026},
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"abbreviation": "CHI", "displayName": "Chicago Bears"}},
                {"homeAway": "away", "team": {"abbreviation": opponent, "displayName": opponent}},
            ], "venue": {"fullName": "Soldier Field"},
                "status": {"type": {"state": state, "completed": state == "post", "name": "STATUS_SCHEDULED"}}}]}


def payload(*games):
    return {"team": {"abbreviation": "CHI"}, "events": list(games)}


def test_next_game_skips_calendar_gap_and_completed_games_without_guessing_bye():
    data = payload(event("final", "2026-11-02T01:00Z", state="post"),
                   event("later", "2026-11-22T18:00Z", week=12),
                   event("after-gap", "2026-11-15T18:00Z", week=11))
    game = next_game(data, "CHI", NOW)
    assert game["game_id"] == "after-gap" and game["week"] == 11
    assert game["opponent"] == "TB" and game["home_away"] == "home"
    assert game["days_until"] == 11
    assert "bye" not in game


def test_current_live_game_and_kickoff_instant_override_input_order():
    live = event("live", "2026-11-04T16:00Z", state="in")
    stale = event("unstarted-past", "2026-11-04T15:00Z")
    later = event("future", "2026-11-08T18:00Z")
    assert next_game(payload(later, stale, live), "CHI", NOW)["game_id"] == "live"


@pytest.mark.parametrize("status", ["STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_POSTPONED"])
def test_cancelled_or_postponed_placeholder_does_not_become_next_fixture(status):
    game = event("cancelled", "2026-11-08T18:00Z")
    game["competitions"][0]["status"]["type"]["name"] = status
    assert next_game(payload(game), "CHI", NOW) is None


def test_tbd_kickoff_does_not_present_provider_placeholder_as_confirmed_time():
    game = event("tbd", "2026-11-15T05:00Z")
    game["competitions"][0]["isTBDFlex"] = True
    assert next_game(payload(game), "CHI", NOW)["kickoff_confirmed"] is False


def test_unknown_team_response_is_rejected():
    with pytest.raises(ValueError, match="mismatch"):
        next_game(payload(), "SEA", NOW)


def test_duplicate_or_unknown_competitors_do_not_form_a_fixture():
    duplicate = event("duplicate", "2026-11-08T18:00Z", opponent="CHI")
    unknown = event("unknown", "2026-11-08T18:00Z", opponent="XXX")
    assert next_game(payload(duplicate, unknown), "CHI", NOW) is None


def test_provider_archives_exact_bytes_and_reuses_only_fresh_cache(tmp_path):
    raw = json.dumps(payload(event("next", "2026-11-08T18:00Z"))).encode()
    calls = []
    class Response:
        status_code = 200
        content = raw
        def raise_for_status(self): pass
    current = [NOW]
    def get(url, **options):
        calls.append((url, options))
        return Response()
    provider = ScheduleProvider(tmp_path, get=get, clock=lambda: current[0])
    first = provider.schedule("CHI")
    assert first["status"] == "AVAILABLE" and first["game"]["game_id"] == "next"
    assert first["source"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(list(tmp_path.glob("*.json"))) == 1
    provider.schedule("CHI")
    assert len(calls) == 1
    current[0] += timedelta(minutes=16)
    provider.schedule("CHI")
    assert len(calls) == 2
    assert calls[0][1]["allow_redirects"] is False


def test_acquisition_failure_never_returns_expired_fixture():
    def get(*args, **kwargs): raise requests.Timeout()
    provider = ScheduleProvider(get=get, clock=lambda: NOW)
    result = provider.schedule("CHI")
    assert result["status"] == "UNAVAILABLE" and result["game"] is None
    with pytest.raises(ValueError): provider.schedule("unknown")
