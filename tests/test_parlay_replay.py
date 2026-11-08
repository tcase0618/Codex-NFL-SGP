from copy import deepcopy

from research.parlay_replay import build_tickets, grade_ticket


def candidate(player, team, probability, outcome):
    return {"season": "2026", "week": "2", "game_id": "game-1", "kickoff": "2026-09-17T00:20Z",
            "team_id": team, "player_id": player, "player": player, "market": "rushing_yards",
            "line": "40", "calibrated_probability": str(probability),
            "calibration_status": "trained_on_prior_games", "prior_game_ids": "prior-1;prior-2",
            "outcome": outcome, "actual_yards": "50" if outcome == "1" else "20" if outcome == "0" else ""}


def seed():
    row = candidate("seed", "CHI", .8, "1")
    row["game_id"] = "prior-2"
    row["week"] = "1"
    row["kickoff"] = "2026-09-10T00:20Z"
    return row


def test_ticket_selection_is_independent_of_final_outcomes():
    rows = [seed(), candidate("a", "CHI", .8, "1"), candidate("b", "SEA", .75, "0"),
            candidate("c", "CHI", .7, "1"), candidate("d", "SEA", .6, "1")]
    first = build_tickets(rows)
    changed = deepcopy(rows)
    for row in changed:
        row["outcome"] = "0"
        row["actual_yards"] = "0"
    assert build_tickets(changed) == first
    assert [item["leg_count"] for item in first] == [3]
    assert len({leg["player_id"] for leg in first[0]["legs"]}) == 3
    assert {leg["team_id"] for leg in first[0]["legs"]} == {"CHI", "SEA"}


def test_one_losing_leg_makes_ticket_lost_even_if_another_is_unresolved():
    rows = [seed(), candidate("a", "CHI", .8, "1"), candidate("b", "SEA", .75, "0"),
            candidate("c", "CHI", .7, "")]
    ticket = build_tickets(rows)[0]
    outcomes = {(row["game_id"], row["player_id"], row["market"], row["line"]): row for row in rows}
    grade = grade_ticket(ticket, outcomes)
    assert grade["status"] == "lost"
    assert [leg["status"] for leg in grade["legs"]] == ["won", "lost", "unresolved"]


def test_tickets_require_both_teams_and_probability_floor():
    rows = [seed(), candidate("a", "CHI", .8, "1"), candidate("b", "CHI", .7, "1"),
            candidate("c", "SEA", .54, "1")]
    assert build_tickets(rows) == []


def test_week_one_without_current_season_roster_proxy_has_no_ticket():
    rows = [candidate("a", "CHI", .8, "1"), candidate("b", "SEA", .75, "1"),
            candidate("c", "CHI", .7, "1")]
    for row in rows:
        row["week"] = "1"
    assert build_tickets(rows) == []
