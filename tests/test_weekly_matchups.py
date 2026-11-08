"""Synthetic calendars and frozen research; never real betting evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from research.weekly_matchups import parse_week, WeeklyProvider, game_tiers
from test_team_schedule import event


def calendar():
    return {"leagues": [{"abbreviation": "NFL", "calendar": [{"value": "2", "entries": [{"value": "9", "label": "Week 9"}]}]}],
            "season": {"year": 2026, "type": 2}, "week": {"number": 9, "teamsOnBye": [{"abbreviation": "TEN"}]},
            "events": [event("later", "2026-11-09T01:20Z"), event("earlier", "2026-11-06T01:15Z", opponent="SEA")]}


def test_week_retains_all_games_in_time_order_and_explicit_byes():
    value = parse_week(calendar())
    assert [game["game_id"] for game in value["games"]] == ["earlier", "later"]
    assert value["byes"] == ["TEN"]
    assert value["games"][0]["away"] == "SEA"
    assert value["weeks"][0]["label"] == "Week 9"


@pytest.mark.parametrize("damage", ["duplicate", "other-week", "not-nfl", "same-team", "unknown-team"])
def test_calendar_rejects_ambiguous_or_wrong_week_evidence(damage):
    value = calendar()
    if damage == "duplicate": value["events"].append(deepcopy(value["events"][0]))
    if damage == "other-week": value["week"]["number"] = 10
    if damage == "not-nfl": value["leagues"][0]["abbreviation"] = "NBA"
    if damage in ("same-team", "unknown-team"):
        value["events"][0]["competitions"][0]["competitors"][1]["team"]["abbreviation"] = "CHI" if damage == "same-team" else "X"
    with pytest.raises(ValueError): parse_week(value, (2026, 2, 9))


def test_current_week_is_provider_calendar_not_iso_week_and_raw_bytes_are_archived(tmp_path):
    calls = []
    class Response:
        status_code = 200
        content = json.dumps(calendar()).encode()
        def raise_for_status(self): pass
    def get(url, **kwargs): calls.append(url);return Response()
    provider = WeeklyProvider(tmp_path, get=get)
    value = provider.week()
    assert value["week"] == 9
    assert list(tmp_path.glob("*.json"))[0].read_bytes() == Response.content
    provider.week();assert len(calls) == 1
    provider.week(2026, 2, 9);assert "week=9" in calls[-1] and "seasontype=2" in calls[-1]
    with pytest.raises(ValueError): provider.week(2026, None, 9)


def test_missing_tickets_keep_four_empty_families_not_fabricated_picks():
    report = game_tiers(parse_week(calendar())["games"][0])
    assert [tier["leg_count"] for tier in report["tiers"]] == [3, 5, 8, 3]
    assert [tier["market_family"] for tier in report["tiers"]] == ["mixed"] * 3 + ["anytime_td"]
    assert all(not tier["tickets"] and tier["status"] == "NOT_GENERATED" for tier in report["tiers"])
    assert report["betting_ready"] is False


def test_recorded_tickets_bind_exact_game_kickoff_and_both_teams(tmp_path):
    from football_engine.ledger import Ledger
    from test_ledger import pregame_payload
    path = tmp_path / "ledger.sqlite"
    ledger = Ledger(path)
    proposal = pregame_payload()
    proposal["decision"] = {"approved": False, "leg_probabilities": {}, "joint_probability": None, "reasons": ["synthetic research"]}
    original = proposal["legs"][0]
    proposal["legs"] = [{**original, "player_id": str(i), "team_id": "CHI" if i != 1 else "SEA"} for i in range(3)]
    ledger.append("fixture-three", "pregame", proposal)
    ledger.connection.close()
    game = {"game_id": proposal["game_id"], "kickoff": proposal["snapshot"]["kickoff"], "home": "CHI", "away": "SEA"}
    report = game_tiers(game, path)
    assert report["tiers"][0]["tickets"][0]["ticket_id"] == "fixture-three"
    assert report["tiers"][0]["tickets"][0]["decision_status"] == "BLOCKED"
    assert report["tiers"][0]["tickets"][0]["tennessee_quote_verified"] is False
    for change in ({"game_id": "other"}, {"away": "TB"}, {"kickoff": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}):
        assert not game_tiers({**game, **change}, path)["tiers"][0]["tickets"]
    assert path.is_file()


def test_three_scorer_proposal_appears_only_in_anytime_td_family(tmp_path):
    from football_engine.ledger import Ledger
    from test_ledger import pregame_payload

    path = tmp_path / "td-ledger.sqlite"
    ledger = Ledger(path)
    proposal = pregame_payload()
    proposal["decision"] = {"approved": False, "leg_probabilities": {},
                            "joint_probability": None, "reasons": ["fixture"]}
    original = proposal["legs"][0]
    proposal["legs"] = [{**original, "player_id": str(i),
                         "team_id": "CHI" if i != 1 else "SEA",
                         "market": "anytime_td", "line": 1} for i in range(3)]
    ledger.append("fixture-td", "pregame", proposal)
    ledger.connection.close()
    game = {"game_id": proposal["game_id"], "kickoff": proposal["snapshot"]["kickoff"],
            "home": "CHI", "away": "SEA"}
    report = game_tiers(game, path)
    assert not report["tiers"][0]["tickets"]
    assert report["tiers"][3]["tickets"][0]["ticket_id"] == "fixture-td"
