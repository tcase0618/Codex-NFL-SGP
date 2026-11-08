from datetime import datetime, timezone

from research.reconstructed_replay import depth_index, eligibility, membership, roster_clear
from research import reconstructed_replay as replay
import pytest


def test_empty_player_history_is_not_silently_counted_as_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "box_with_td", lambda *args: {"teams": {"HOU": {}, "TEN": {}}})
    with pytest.raises(ValueError, match="incomplete player history"):
        replay.history_box({"id": "example"}, tmp_path)


def test_roster_state_is_not_automatically_game_day_eligibility():
    leg = {"team_id": "CHI", "player_id": "1"}
    key = (1, "CHI", "1")
    assert roster_clear(leg, 1, {key: {"ACT"}})
    for states in ({"INA"}, {"RES"}, {"EXE"}, {"ACT", "RES"}, set()):
        assert not roster_clear(leg, 1, {key: states})
    # Passing the roster screen still cannot pass a missing inactive report.
    cutoff = datetime(2026, 9, 13, 16, 30, tzinfo=timezone.utc)
    assert not eligibility({**leg, "player": "Example"}, cutoff,
                           {"CHI": [("2026-09-13T15:00:00Z", {"1"})]}, [])[0]


def test_source_team_aliases_join_engine_teams(tmp_path):
    path = tmp_path / "depth.csv"
    path.write_text("team,dt,espn_id\nLA,2026-09-13T15:00:00Z,1\nWAS,2026-09-13T15:00:00Z,2\n")
    index = depth_index(path)
    assert set(index) == {"LAR", "WSH"}
    assert index["LAR"][0][1] == {"1"}


def test_depth_cutoff_and_staleness():
    cutoff = datetime(2026, 9, 13, 16, 30, tzinfo=timezone.utc)
    index = {"CHI": [("2026-09-13T15:00:00Z", {"1"}), ("2026-09-13T18:00:00Z", {"2"})]}
    assert membership(index, "CHI", cutoff)[1] == {"1"}
    index["CHI"] = [("2026-09-10T15:00:00Z", {"1"})]
    assert membership(index, "CHI", cutoff)[1] == set()


def test_identity_and_inactives_are_independent_of_participation():
    cutoff = datetime(2026, 9, 13, 16, 30, tzinfo=timezone.utc)
    index = {"CHI": [("2026-09-13T15:00:00Z", {"1"})]}
    leg = {"team_id": "CHI", "player_id": "1", "player": "Example Person"}
    section = {"published_at": "2026-09-13T15:30:00Z", "names_as_published": ["WR Other Person"]}
    assert eligibility(leg, cutoff, index, [section])[0]
    section["names_as_published"] = ["WR Example Person (injury)"]
    assert eligibility(leg, cutoff, index, [section])[1] == "officially_listed_inactive"
    section["names_as_published"] = []
    leg["player_id"] = "2"
    assert not eligibility(leg, cutoff, index, [section])[0]


def test_future_and_missing_inactive_report_fail_closed():
    cutoff = datetime(2026, 9, 13, 16, 30, tzinfo=timezone.utc)
    index = {"CHI": [("2026-09-13T15:00:00Z", {"1"})]}
    leg = {"team_id": "CHI", "player_id": "1", "player": "Example Person"}
    assert not eligibility(leg, cutoff, index, [])[0]
    section = {"published_at": "2026-09-13T18:00:00Z", "names_as_published": []}
    assert eligibility(leg, cutoff, index, [section])[1] == "inactive_report_after_cutoff"
