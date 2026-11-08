from dataclasses import replace
from datetime import timedelta

import pytest

from football_engine.engine import evaluate_ticket
from football_engine.player_history import PlayerProfile, TeamEvent, record_team_event, team_as_of, rebuild_after_team_event, save_profile_snapshot
from test_engine import NOW, SOURCE, snapshot, leg


def profile_with_trade():
    profile = PlayerProfile("p1", "Fixture Player", (("fixture", "p1"),), history_references=("fixture:old-team-stat",))
    joined = TeamEvent("join", "p1", None, "CHI", NOW - timedelta(days=100),
                       replace(SOURCE, observed_at=NOW - timedelta(days=100)))
    profile = record_team_event(profile, joined)
    traded = TeamEvent("trade", "p1", "CHI", "SEA", NOW - timedelta(days=1), SOURCE)
    return profile, traded


def test_trade_preserves_identity_and_old_history_without_backdating_knowledge():
    old, trade = profile_with_trade()
    new = record_team_event(old, trade)
    assert old.team_events != new.team_events
    assert new.provider_ids == old.provider_ids
    assert new.history_references == old.history_references
    assert team_as_of(new, NOW) == "SEA"
    # The trade is effective yesterday, but this source was observed today.
    assert team_as_of(new, NOW - timedelta(hours=12)) == "CHI"
    with pytest.raises(ValueError, match="duplicate"):
        record_team_event(new, trade)


def test_pending_trade_does_not_change_confirmed_membership():
    old, trade = profile_with_trade()
    pending = replace(trade, status="pending")
    profile = record_team_event(old, pending)
    assert team_as_of(profile, NOW) == "CHI"
    with pytest.raises(ValueError, match="confirmed"):
        rebuild_after_team_event(snapshot(), profile, pending)


def test_trade_invalidates_all_scenarios_and_requires_activation_again():
    old, trade = profile_with_trade()
    profile = record_team_event(old, trade)
    game = replace(snapshot(), team_profile_versions={"CHI": "old", "SEA": "old"})
    updated = rebuild_after_team_event(game, profile, trade)
    assert updated.players["p1"].team_id == "SEA"
    assert not updated.players["p1"].active
    assert not updated.scenarios
    assert updated.team_profile_versions == {}
    assert game.players["p1"].team_id == "CHI" and game.scenarios
    assert not evaluate_ticket(updated, (replace(leg(), team_id="SEA"),)).approved


def test_defender_not_in_prop_pool_still_invalidates_opponent_forecasts():
    old, trade = profile_with_trade()
    trade = replace(trade, player_id="defender")
    defender = PlayerProfile("defender", "Fixture Defender", (("fixture", "defender"),))
    defender = record_team_event(defender, trade)
    updated = rebuild_after_team_event(snapshot(), defender, trade)
    assert not updated.scenarios


def test_conflicting_old_team_is_rejected():
    old, trade = profile_with_trade()
    with pytest.raises(ValueError, match="conflicting"):
        record_team_event(old, replace(trade, from_team="TEN"))


def test_profile_snapshot_cannot_overwrite_or_duplicate_provider_identity(tmp_path):
    import json
    old, trade = profile_with_trade()
    new = record_team_event(old, trade)
    path = tmp_path / "version1.json"
    digest = save_profile_snapshot(path, (new,))
    saved = json.loads(path.read_text())
    assert saved["profiles_sha256"] == digest
    assert saved["profiles"][0]["history_references"] == ["fixture:old-team-stat"]
    with pytest.raises(FileExistsError):
        save_profile_snapshot(path, (old,))
    with pytest.raises(ValueError, match="identity"):
        save_profile_snapshot(tmp_path / "conflict.json", (new, replace(old, player_id="duplicate")))
