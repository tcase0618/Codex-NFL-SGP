import pytest

from research.compare_reconstructions import paired


def ticket(name, legs=None):
    return {"ticket_id": name, "game_id": "g", "game": "A at B", "type": "3", "week": 1,
            "legs": [] if legs is None else legs}


def test_pairing_is_by_game_family_and_preserves_blocked_category():
    leg = {"player_id": "p", "team_id": "A", "market": "passing_yards", "line": 150}
    rows = paired([ticket("old")], [{"ticket_id": "old", "status": "blocked"}],
                  [ticket("new", [leg])], [{"ticket_id": "new", "status": "won"}])
    assert rows[0]["old_status"] == "blocked"
    assert rows[0]["new_status"] == "won"
    assert rows[0]["selection_changed"]
    assert not rows[0]["both_constructed"]


def test_coverage_and_grade_alignment_mismatches_are_rejected():
    with pytest.raises(ValueError, match="coverage"):
        paired([ticket("old")], [{"ticket_id": "old", "status": "blocked"}], [], [])
    with pytest.raises(ValueError, match="alignment"):
        paired([ticket("old")], [{"ticket_id": "wrong", "status": "blocked"}],
               [ticket("new")], [{"ticket_id": "new", "status": "blocked"}])
