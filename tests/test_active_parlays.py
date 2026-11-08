import pytest

from research.active_parlays import filter_participants


def test_only_explicit_same_team_participants_can_pass():
    pool = [{"team_id": "CHI", "player_id": str(index)} for index in range(1, 5)]
    rosters = {"CHI": {"source": "fixture:game-roster", "entries": [
        {"playerId": 1, "didNotPlay": False}, {"playerId": 2, "didNotPlay": True},
        {"playerId": 3}]}, "SEA": {"source": "fixture:other-roster", "entries": [
        {"playerId": 4, "didNotPlay": False}]}}
    selected = filter_participants(pool, rosters)
    assert [leg["player_id"] for leg in selected] == ["1"]
    assert selected[0]["eligibility"] == "POSTGAME_PARTICIPATION_CONFIRMED_NOT_PREGAME"
    assert selected[0]["eligibility_source"] == "fixture:game-roster"
    assert selected[0]["roster_sha256"]


def test_missing_roster_provenance_fails_closed():
    with pytest.raises(ValueError, match="roster evidence"):
        filter_participants([{"team_id": "CHI", "player_id": "1"}],
                            {"CHI": {"entries": [{"playerId": 1, "didNotPlay": False}]}})
