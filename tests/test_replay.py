from copy import deepcopy

from research.replay_2026 import forecasts


def game(game_id, week, yards):
    return {"season": 2026, "week": week, "id": game_id, "kickoff": f"2026-09-{week + 9:02d}T17:00Z",
            "teams": {"CHI": {"p1": {"name": "Example", "markets": {
                "rushing_yards": {"volume": 10, "yards": yards}}}}, "SEA": {}}}


def test_replay_probability_uses_only_prior_games():
    history = {"CHI": [game("prior-1", 1, 50), game("prior-2", 2, 70)], "SEA": []}
    current = game("current", 3, 80)
    changed_final = deepcopy(current)
    changed_final["teams"]["CHI"]["p1"]["markets"]["rushing_yards"]["yards"] = 0
    first = forecasts(current, history)
    second = forecasts(changed_final, history)
    assert len(first) == len(second) == 1
    assert first[0]["probability"] == second[0]["probability"]
    assert first[0]["outcome"] == 1
    assert second[0]["outcome"] == 0
    assert first[0]["prior_game_ids"] == "prior-1;prior-2"


def test_offensive_participant_without_market_stat_is_graded_zero():
    history = {"CHI": [game("prior-1", 1, 50), game("prior-2", 2, 70)], "SEA": []}
    current = game("current", 3, 0)
    current["teams"]["CHI"]["p1"]["markets"] = {"receiving_yards": {"volume": 1, "yards": 2}}
    row = forecasts(current, history)[0]
    assert row["market"] == "rushing_yards"
    assert row["actual_yards"] == 0
    assert row["outcome"] == 0


def test_absent_offensive_player_remains_unresolved():
    history = {"CHI": [game("prior-1", 1, 50), game("prior-2", 2, 70)], "SEA": []}
    current = game("current", 3, 0)
    current["teams"]["CHI"] = {}
    row = forecasts(current, history)[0]
    assert row["actual_yards"] is None
    assert row["outcome"] is None
