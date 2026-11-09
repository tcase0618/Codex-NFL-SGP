import hashlib
import json
from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from football_engine.ledger import Ledger
from football_engine.settlement import settle_recorded_ticket
from test_engine import leg, snapshot, NOW


def final_box():
    return {
        "header": {"id":"123", "competitions": [{"status": {"type": {"name": "STATUS_FINAL"}},
                                      "competitors": [{"team": {"abbreviation": "CHI"}},
                                                      {"team": {"abbreviation": "SEA"}}]}]},
        "boxscore": {"players": [
            {"team": {"abbreviation": "CHI"}, "statistics": [
                {"name": "rushing", "labels": ["CAR", "YDS", "AVG", "TD"],
                 "athletes": [{"athlete": {"id": "p1"}, "stats": ["27", "69", "2.6", "3"]}]}]},
            {"team": {"abbreviation": "SEA"}, "statistics": []}]}}


def proof(box, retrieved_at="2026-11-03T04:00:00+00:00"):
    return {"provider": "ESPN", "reference": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=123",
            "sha256": hashlib.sha256(json.dumps(box).encode()).hexdigest(), "content_sha256": hashlib.sha256(
                json.dumps(box, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "retrieved_at": retrieved_at}


def test_official_final_settles_frozen_ticket(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    with monkeypatch.context() as clock:
        clock.setattr('football_engine.ledger._now',lambda:NOW)
        ledger.evaluate_and_record("ticket-1", replace(snapshot(), game_id="123"), (leg(line=70),))
    box = final_box()
    result = settle_recorded_ticket(ledger, "ticket-1", box, proof(box))
    assert result["status"] == "lost"
    assert result["legs"][0]["observed"] == 69
    assert ledger.verify()


def test_nonfinal_and_changed_box_are_rejected(tmp_path):
    from test_ledger import fresh_snapshot
    ledger = Ledger(tmp_path / "ledger.sqlite")
    game = replace(fresh_snapshot(), game_id="123")
    ledger.evaluate_and_record("ticket-1", game, (replace(leg(), source=game.source),))
    box = final_box()
    source = proof(box)
    box["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["stats"][1] = "70"
    with pytest.raises(ValueError, match="changed"):
        settle_recorded_ticket(ledger, "ticket-1", box, source)
    box["header"]["competitions"][0]["status"]["type"]["name"] = "STATUS_IN_PROGRESS"
    with pytest.raises(ValueError, match="not final"):
        settle_recorded_ticket(ledger, "ticket-1", box, proof(box))


@pytest.mark.parametrize("value", ["nan", "inf", "6,9", "69.5", "6e1", None, True, 69, [], {}])
def test_malformed_final_yardage_is_never_a_result(value):
    from football_engine.settlement import _final_totals
    box = final_box()
    box["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["stats"][1] = value
    assert "rushing_yards" not in _final_totals(box)["p1"]


@pytest.mark.parametrize("value", ["-1", "1.5", "nan", "inf", "1e2", True, None, [], {}])
def test_malformed_touchdowns_do_not_create_anytime_credit(value):
    from football_engine.settlement import _final_totals
    box = final_box()
    box["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["stats"][3] = value
    assert "anytime_td" not in _final_totals(box)["p1"]


def recorded_fixture(tmp_path, monkeypatch, legs=None):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    monkeypatch.setattr("football_engine.ledger._now", lambda: NOW)
    game = replace(snapshot(), game_id="123")
    if legs and len(legs) > 1:
        from football_engine.engine import leg_key
        samples = tuple({leg_key(item): True for item in legs} for _ in range(100))
        game = replace(game, scenarios=tuple(replace(item, joint_samples=samples) for item in game.scenarios))
    ledger.evaluate_and_record("ticket-1", game, legs or (leg(),))
    monkeypatch.setattr("football_engine.ledger._now", lambda: NOW + timedelta(hours=8))
    return ledger


@pytest.mark.parametrize("change", ["game_id", "competition_id", "team", "kickoff", "retrieved_before", "retrieved_after", "naive", "raw_hash"])
def test_final_identity_and_chronology_cannot_be_rewritten(tmp_path, monkeypatch, change):
    ledger = recorded_fixture(tmp_path, monkeypatch)
    box = final_box()
    if change == "game_id": box["header"]["id"] = "456"
    if change == "competition_id": box["header"]["competitions"][0]["id"] = "456"
    if change == "team":
        box["header"]["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "NYG"
        box["boxscore"]["players"][0]["team"]["abbreviation"] = "NYG"
    if change == "kickoff": box["header"]["competitions"][0]["date"] = NOW.isoformat()
    source = proof(box)
    if change == "retrieved_before": source["retrieved_at"] = NOW.isoformat()
    if change == "retrieved_after": source["retrieved_at"] = (NOW + timedelta(days=1)).isoformat()
    if change == "naive": source["retrieved_at"] = "2026-11-03T04:00:00"
    if change == "raw_hash": source["sha256"] = "raw-fixture"
    with pytest.raises(ValueError):
        settle_recorded_ticket(ledger, "ticket-1", box, source)
    assert ledger.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_missing_player_stays_unresolved_and_preserves_identity(tmp_path, monkeypatch):
    ledger = recorded_fixture(tmp_path, monkeypatch)
    box = final_box()
    box["boxscore"]["players"][0]["statistics"][0]["athletes"] = []
    result = settle_recorded_ticket(ledger, "ticket-1", box, proof(box))
    assert result["status"] == "unresolved"
    assert result["legs"] == [{"status": "unresolved", "player_id": "p1", "team_id": "CHI", "market": "rushing_yards", "threshold": 40}]
    assert ledger.verify()
    assert ledger.prospective_report()["settled_forecasts"] == 0


@pytest.mark.parametrize("yards,td,expected", [("69", "0", "unresolved"), ("69", "1", "won")])
def test_anytime_td_offense_proves_a_win_but_not_a_loss(tmp_path, monkeypatch, yards, td, expected):
    ledger = recorded_fixture(tmp_path, monkeypatch, (leg("anytime_td", 1),))
    box = final_box()
    stats = box["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["stats"]
    stats[1], stats[3] = yards, td
    result = settle_recorded_ticket(ledger, "ticket-1", box, proof(box))
    assert result["status"] == expected
    if expected == "won":
        assert result["legs"][0]["observation_basis"] == "offensive_td_lower_bound"
    assert ledger.verify()


def test_partial_losing_ticket_does_not_bias_full_ticket_forecast_score(tmp_path, monkeypatch):
    ledger = recorded_fixture(tmp_path, monkeypatch, (leg(line=70), leg("anytime_td", 1)))
    box = final_box()
    box["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["stats"][3] = "0"
    result = settle_recorded_ticket(ledger, "ticket-1", box, proof(box))
    assert result["status"] == "lost"
    assert [item["status"] for item in result["legs"]] == ["lost", "unresolved"]
    assert ledger.verify()
    report = ledger.prospective_report(minimum_settled=1)
    assert report["status"] == "NOT MEASURABLE"
    assert report["settled_forecasts"] == 0 and report["brier_score"] is None


def test_caller_mutation_cannot_rewrite_frozen_final_evidence(tmp_path, monkeypatch):
    ledger = recorded_fixture(tmp_path, monkeypatch)
    box = final_box()
    source = proof(box)
    result = settle_recorded_ticket(ledger, "ticket-1", box, source)
    box["header"]["id"] = "456"
    source["content_sha256"] = "changed"
    result["legs"][0]["observed"] = 0
    result["final_box"]["header"]["id"] = "456"
    frozen = json.loads(ledger.connection.execute("SELECT payload FROM events WHERE kind='settlement'").fetchone()[0])
    assert frozen["final_box"]["header"]["id"] == "123"
    assert frozen["legs"][0]["observed"] == 69
    assert ledger.verify()


def test_missing_archive_does_not_pass_audit(tmp_path, monkeypatch):
    from football_engine.ledger import audit_ledger_evidence
    ledger = recorded_fixture(tmp_path, monkeypatch)
    box = final_box()
    settle_recorded_ticket(ledger, "ticket-1", box, proof(box))
    path = tmp_path / "ledger.sqlite"
    report = audit_ledger_evidence({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}, NOW + timedelta(hours=9))
    assert report["status"] == "BLOCKED"
    assert "settlement lacks inspectable final source archive" in report["reasons"]


@pytest.mark.parametrize("box", [None, [], {}, {"header": None}, {"header": {"competitions": [None]}}])
def test_malformed_final_box_is_a_validation_error(box):
    from football_engine.settlement import _final_totals
    with pytest.raises(ValueError):
        _final_totals(box)
