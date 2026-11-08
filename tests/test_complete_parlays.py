from copy import deepcopy

from research.complete_parlays import candidates, select, settle


def prior():
    return {"id": "prior", "kickoff": "2025-12-20T18:00Z", "teams": {
        team: {f"{team}{index}": {"name": f"Player {team}{index}", "td": int(index < 2),
               "markets": {"rushing_yards": {"yards": 50, "volume": 10},
                           "receiving_yards": {"yards": 30, "volume": 4}}}
               for index in range(5)} for team in ("CHI", "SEA")}}


def test_all_ticket_sizes_can_use_prior_only_candidates():
    earlier = prior()
    game = {"id": "next", "kickoff": "2026-09-10T18:00Z", "scheduled_teams": ["CHI", "SEA"]}
    pool = candidates(game, {"CHI": [earlier], "SEA": [earlier]})
    for length in (3, 5, 8):
        legs = select([leg for leg in pool if leg["market"] != "anytime_td"], length)
        assert len(legs) == len({leg["player_id"] for leg in legs}) == length
        assert {leg["team_id"] for leg in legs} == {"CHI", "SEA"}
        assert all(leg["prior_game_ids"] == ["prior"] for leg in legs)
    assert len(select([leg for leg in pool if leg["market"] == "anytime_td"], 3)) == 3


def test_current_outcomes_change_grading_without_changing_selection():
    earlier = prior()
    game = {"id": "next", "kickoff": "2026-09-10T18:00Z", "scheduled_teams": ["CHI", "SEA"]}
    history = {"CHI": [earlier], "SEA": [earlier]}
    pool = [leg for leg in candidates(game, history) if leg["market"] == "anytime_td"]
    chosen = select(pool, 3)
    ticket = {"ticket_id": "test", "legs": chosen}
    final = deepcopy(earlier)
    final["id"] = "next"
    for leg in chosen:
        final["teams"][leg["team_id"]][leg["player_id"]]["td"] = 1
    assert settle(ticket, final)["status"] == "won"
    final["teams"][chosen[0]["team_id"]][chosen[0]["player_id"]]["td"] = 0
    assert settle(ticket, final)["status"] == "lost"
    assert select([leg for leg in candidates(game, history) if leg["market"] == "anytime_td"], 3) == chosen
