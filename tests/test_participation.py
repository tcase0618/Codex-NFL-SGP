from research.resolve_participation import classify


def test_nonparticipation_is_not_graded_as_zero():
    leg = {"player_id": "123"}
    own = {"entries": [{"playerId": 123, "didNotPlay": True}]}
    assert classify(leg, own, {"entries": []}) == "did_not_play"
    own["entries"][0]["didNotPlay"] = False
    assert classify(leg, own, {"entries": []}) == "played_zero"


def test_roster_absence_is_an_invalid_selection_not_an_invented_loss():
    leg = {"player_id": "123"}
    empty = {"entries": []}
    other = {"entries": [{"playerId": 123, "didNotPlay": False}]}
    assert classify(leg, empty, other) == "wrong_team"
    assert classify(leg, empty, empty) == "not_on_game_roster"
    assert classify(leg, {"entries": [{"playerId": 123}]}, empty) == "participation_unknown"
