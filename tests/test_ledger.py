import json
import sqlite3
from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from football_engine.ledger import Ledger
from test_engine import SOURCE, leg, snapshot
from football_engine.engine import GameState, TicketQuote


def pregame_payload():
    from football_engine.ledger import _json_ready
    from football_engine.engine import evaluate_ticket
    game = replace(snapshot(), kickoff=datetime.now(timezone.utc) + timedelta(hours=1))
    return _json_ready(dict(game_id=game.game_id, snapshot=game, decision=evaluate_ticket(game, (leg(),)), legs=(leg(),)))


def unresolved():
    return {"status": "unresolved", "player_id": "p1", "team_id": "CHI", "market": "rushing_yards", "threshold": 40}


def test_ledger_is_append_only_and_verifiable(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.append("ticket-1", "pregame", pregame_payload())
    with pytest.raises(ValueError):
        ledger.append("ticket-2", "settlement", {"status": "lost", "source": "box-score"})
    ledger.append("ticket-1", "settlement", {"status": "unresolved", "legs": [unresolved()]})
    assert ledger.verify()
    with pytest.raises(sqlite3.IntegrityError):
        ledger.connection.execute("DELETE FROM events")
    ledger.connection.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        ledger.append("ticket-1", "settlement", {"status": "unresolved", "legs": [unresolved()]})


def test_ledger_rejects_pregame_current_and_forged_approval(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    payload = pregame_payload()
    payload["legs"][0]["current"] = payload["legs"][0]["line"]
    with pytest.raises(ValueError, match="pregame current"):
        ledger.append("already-hit", "pregame", payload)
    payload = pregame_payload()
    payload["decision"]["leg_probabilities"]["p1:rushing_yards:40"] = .99
    payload["decision"]["joint_probability"] = .99
    with pytest.raises(ValueError, match="approved forecast differs"):
        ledger.append("forged", "pregame", payload)
    assert ledger.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_settlement_cannot_claim_win_with_unresolved_leg(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.append("ticket-1", "pregame", pregame_payload())
    with pytest.raises(ValueError):
        ledger.append("ticket-1", "settlement", {"status": "won", "source": "claim", "legs": [unresolved()]})


def test_evaluation_records_exact_pregame_inputs(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    decision = ledger.evaluate_and_record("ticket-1", replace(snapshot(), kickoff=datetime.now(timezone.utc) + timedelta(hours=1)),
                                          (leg(),), quote=TicketQuote(2.5, SOURCE, ("p1:rushing_yards:40",)))
    assert decision.approved
    assert ledger.verify()
    assert '"decimal_odds":2.5' in ledger.connection.execute("SELECT payload FROM events WHERE ticket_id='ticket-1'").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        ledger.evaluate_and_record("ticket-1", replace(snapshot(), kickoff=datetime.now(timezone.utc) + timedelta(hours=1)), (leg(),))


def test_stricter_evaluation_controls_are_frozen_and_replayed(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    game = replace(snapshot(), kickoff=datetime.now(timezone.utc) + timedelta(hours=1))
    decision = ledger.evaluate_and_record("strict", game, (leg(),),
                                          max_source_age_seconds=120,
                                          minimum_scenario_probability=0.06,
                                          aggressive_fraction=0.9)
    assert decision.approved and ledger.verify()
    payload = json.loads(ledger.connection.execute(
        "SELECT payload FROM events WHERE ticket_id='strict'").fetchone()[0])
    assert payload["evaluation_controls"] == {
        "max_source_age_seconds": 120,
        "minimum_scenario_probability": 0.06,
        "aggressive_fraction": 0.9,
    }


def test_historical_snapshot_cannot_be_backfilled_as_pregame(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    with pytest.raises(ValueError, match="precede kickoff"):
        ledger.evaluate_and_record("ticket-1", snapshot(), (leg(),))


def test_prospective_report_does_not_claim_edge_from_one_result(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.evaluate_and_record("ticket-1", replace(snapshot(), kickoff=datetime.now(timezone.utc) + timedelta(hours=1)), (leg(),))
    ledger.append("ticket-1", "settlement", {"status": "unresolved", "legs": [unresolved()]})
    report = ledger.prospective_report()
    assert report["status"] == "NOT MEASURABLE"
    assert report["settled_forecasts"] == 0
    assert report["account_roi"] is None


def test_live_proposal_is_frozen_and_cannot_be_reused(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    now = datetime.now(timezone.utc)
    source = replace(SOURCE, observed_at=now)
    game = snapshot()
    scenarios = tuple(replace(item, source=source, maximum_remaining={"p1": {"carries": 12}})
                      for item in game.scenarios)
    live = replace(game, as_of=now, kickoff=now - timedelta(hours=1), source=source,
                   state=GameState(600, "in_progress", source, {"CHI": 20, "SEA": 9}),
                   players={"p1": replace(game.players["p1"], source=source)}, scenarios=scenarios)
    live_leg = replace(leg(), source=source)
    decision = ledger.evaluate_and_record("ticket-1", live, (live_leg,))
    assert decision.approved
    assert ledger.connection.execute("SELECT kind FROM events").fetchone()[0] == "live"
    with pytest.raises(sqlite3.IntegrityError):
        ledger.evaluate_and_record("ticket-1", live, (live_leg,))


def test_combined_quote_must_match_frozen_legs(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    game = replace(snapshot(), kickoff=datetime.now(timezone.utc) + timedelta(hours=1))
    with pytest.raises(ValueError, match="price provenance"):
        ledger.evaluate_and_record("ticket-1", game, (leg(),),
                                   quote=TicketQuote(2.5, SOURCE, ("other:rushing_yards:40",)))
    assert ledger.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_final_source_cannot_predate_live_proposal(tmp_path, monkeypatch):
    from football_engine.settlement import settle_recorded_ticket
    from test_settlement import final_box, proof
    now = datetime.now(timezone.utc)
    source = replace(SOURCE, observed_at=now)
    base = snapshot()
    scenarios = tuple(replace(item, source=source, maximum_remaining={"p1": {"carries": 12}})
                      for item in base.scenarios)
    game = replace(base, game_id="123", as_of=now, kickoff=now - timedelta(hours=1),
                   source=source, state=GameState(600, "in_progress", source, {"CHI": 20, "SEA": 9}),
                   players={"p1": replace(base.players["p1"], source=source)}, scenarios=scenarios)
    ledger = Ledger(tmp_path / "ledger.sqlite")
    monkeypatch.setattr("football_engine.ledger._now", lambda: now)
    ledger.evaluate_and_record("ticket-1", game, (replace(leg(), source=source),))
    monkeypatch.setattr("football_engine.ledger._now", lambda: now + timedelta(hours=1))
    box = final_box()
    with pytest.raises(ValueError, match="retrieval precedes frozen proposal"):
        settle_recorded_ticket(ledger, "ticket-1", box,
                               proof(box, retrieved_at=(now - timedelta(minutes=1)).isoformat()))
    assert ledger.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_rejected_forecast_settles_without_inventing_probability(tmp_path, monkeypatch):
    from football_engine.settlement import settle_recorded_ticket
    from test_engine import NOW
    from test_settlement import final_box, proof
    ledger = Ledger(tmp_path / "ledger.sqlite")
    payload = pregame_payload()
    payload["game_id"] = payload["snapshot"]["game_id"] = "123"
    payload["snapshot"]["as_of"] = NOW.isoformat()
    payload["snapshot"]["kickoff"] = (NOW + timedelta(hours=1)).isoformat()
    payload["decision"].update(approved=False, leg_probabilities={}, joint_probability=None)
    monkeypatch.setattr("football_engine.ledger._now", lambda: NOW)
    ledger.append("ticket-1", "pregame", payload)
    monkeypatch.setattr("football_engine.ledger._now", lambda: NOW + timedelta(hours=8))
    box = final_box()
    result = settle_recorded_ticket(ledger, "ticket-1", box, proof(box))
    assert result["status"] == "won"
    assert result["legs"][0]["predicted_probability"] is None
    assert ledger.verify()
    assert ledger.prospective_report(minimum_settled=1)["settled_forecasts"] == 0


def test_prospective_report_scores_frozen_settled_forecast(tmp_path, monkeypatch):
    from test_settlement import final_box, proof
    from football_engine.settlement import settle_recorded_ticket
    ledger = Ledger(tmp_path / "ledger.sqlite")
    game = replace(snapshot(), game_id="123", kickoff=datetime.now(timezone.utc) + timedelta(hours=1))
    decision = ledger.evaluate_and_record("ticket-1", game, (leg(),))
    probability = decision.joint_probability
    settled_at = game.kickoff + timedelta(hours=3)
    monkeypatch.setattr('football_engine.ledger._now', lambda: settled_at)
    box = final_box()
    settle_recorded_ticket(ledger, "ticket-1", box, proof(box, retrieved_at=settled_at.isoformat()))
    report = ledger.prospective_report()
    assert report["status"] == "NOT MEASURABLE"
    assert report["settled_forecasts"] == 1
    assert report["hit_rate"] == 1
    assert report["brier_score"] == (probability - 1) ** 2
    assert report["account_roi"] is None


# All evidence below is synthetic and is never reported as historical performance.
def archived_settlement(tmp_path, monkeypatch):
    import hashlib
    import json
    from test_engine import NOW
    from test_settlement import final_box, proof
    from football_engine.settlement import settle_recorded_ticket
    path = tmp_path / "ledger.sqlite"
    ledger = Ledger(path)
    monkeypatch.setattr("football_engine.ledger._now", lambda: NOW)
    ledger.evaluate_and_record("ticket-1", replace(snapshot(), game_id="123"), (leg(),),
                               quote=TicketQuote(2.5, SOURCE, ("p1:rushing_yards:40",)))
    monkeypatch.setattr("football_engine.ledger._now", lambda: NOW + timedelta(hours=8))
    box = final_box()
    archive = tmp_path / "final.json"
    archive.write_bytes(json.dumps(box).encode())
    source = dict(proof(box), archive_file=str(archive), sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    settle_recorded_ticket(ledger, "ticket-1", box, source)
    return ledger, path


def manifest(path):
    import hashlib
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def rewrite_and_rehash(ledger, mutation):
    import hashlib
    import json
    db = ledger.connection
    db.execute("DROP TRIGGER immutable_events_update")
    rows = db.execute("SELECT sequence,ticket_id,kind,recorded_at,payload FROM events ORDER BY sequence").fetchall()
    previous = "0" * 64
    for sequence, ticket, kind, recorded, body in rows:
        payload = json.loads(body)
        mutation(kind, payload)
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(f"{previous}|{ticket}|{kind}|{recorded}|{body}".encode()).hexdigest()
        db.execute("UPDATE events SET payload=?,previous_hash=?,event_hash=? WHERE sequence=?", (body, previous, digest, sequence))
        previous = digest
    db.execute("CREATE TRIGGER immutable_events_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'immutable ledger'); END")
    db.commit()


def test_valid_archived_ledger_passes_internal_audit_without_authenticity_claim(tmp_path, monkeypatch):
    from football_engine.ledger import audit_ledger_evidence
    from test_engine import NOW
    ledger, path = archived_settlement(tmp_path, monkeypatch)
    report = audit_ledger_evidence(manifest(path), NOW + timedelta(hours=9))
    assert report["status"] == "PASS", report
    assert report["evidence"]["semantic_replay_verified"] is True
    assert report["evidence"]["external_authenticity_verified"] is False
    assert ledger.prospective_report(minimum_settled=1)["status"] == "MEASURABLE_RESEARCH"


@pytest.mark.parametrize("field,value", [
    ("observed", 10), ("predicted_probability", .123), ("odds_decimal", 8),
    ("threshold", 10), ("player_id", "stranger"), ("team_id", "SEA"),
    ("market", "passing_yards"), ("status", "lost"),
    ("observed", True), ("observed", float("inf")),
])
def test_rehashed_semantically_false_settlement_blocks_audit_and_scoring(tmp_path, monkeypatch, field, value):
    from football_engine.ledger import audit_ledger_evidence
    from test_engine import NOW
    ledger, path = archived_settlement(tmp_path, monkeypatch)
    def mutate(kind, payload):
        if kind == "settlement":
            payload["legs"][0][field] = value
    rewrite_and_rehash(ledger, mutate)
    assert not ledger.verify()
    assert ledger.prospective_report()["status"] == "INVALID_LEDGER"
    assert audit_ledger_evidence(manifest(path), NOW + timedelta(hours=9))["status"] == "BLOCKED"
    with pytest.raises(ValueError, match="invalid ledger"):
        ledger.append("other", "pregame", pregame_payload())


def test_consistent_false_result_still_conflicts_with_frozen_final_box(tmp_path, monkeypatch):
    from football_engine.ledger import audit_ledger_evidence
    from test_engine import NOW
    ledger, path = archived_settlement(tmp_path, monkeypatch)
    def mutate(kind, payload):
        if kind == "settlement":
            payload["status"] = payload["legs"][0]["status"] = "lost"
            payload["legs"][0].update(observed=10, error_category="unclassified")
    rewrite_and_rehash(ledger, mutate)
    report = audit_ledger_evidence(manifest(path), NOW + timedelta(hours=9))
    assert report["status"] == "BLOCKED"
    assert any("contradicts frozen final" in reason for reason in report["reasons"])


@pytest.mark.parametrize("change", ["wrong_game", "future_source", "quote_inventory", "joint_boolean", "probability_range", "plausible_probability", "pregame_current", "empty_legs"])
def test_rehashed_frozen_proposal_contradictions_block(tmp_path, monkeypatch, change):
    from football_engine.ledger import audit_ledger_evidence
    from test_engine import NOW
    ledger, path = archived_settlement(tmp_path, monkeypatch)
    def mutate(kind, payload):
        if kind != "pregame":
            return
        if change == "wrong_game": payload["snapshot"]["game_id"] = "456"
        if change == "future_source": payload["snapshot"]["source"]["observed_at"] = (NOW + timedelta(days=1)).isoformat()
        if change == "quote_inventory": payload["quote"]["leg_keys"] = ["stranger:rushing_yards:40"]
        if change == "joint_boolean": payload["decision"]["joint_probability"] = True
        if change == "probability_range": payload["decision"]["leg_probabilities"]["p1:rushing_yards:40"] = 2
        if change == "plausible_probability":
            payload["decision"]["leg_probabilities"]["p1:rushing_yards:40"] = .95
            payload["decision"]["joint_probability"] = .95
        if change == "pregame_current": payload["legs"][0]["current"] = 40
        if change == "empty_legs": payload["legs"] = []
    rewrite_and_rehash(ledger, mutate)
    assert audit_ledger_evidence(manifest(path), NOW + timedelta(hours=9))["status"] == "BLOCKED"
    assert not ledger.verify()


def test_raw_final_archive_cannot_substitute_different_frozen_content(tmp_path, monkeypatch):
    import hashlib
    import json
    from football_engine.ledger import audit_ledger_evidence
    from test_engine import NOW
    ledger, path = archived_settlement(tmp_path, monkeypatch)
    archive = tmp_path / "final.json"
    raw = json.loads(archive.read_bytes())
    raw["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["stats"][1] = "99"
    archive.write_bytes(json.dumps(raw).encode())
    def mutate(kind, payload):
        if kind == "settlement":
            payload["source"]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            payload["legs"][0]["source"] = payload["source"].copy()
    rewrite_and_rehash(ledger, mutate)
    report = audit_ledger_evidence(manifest(path), NOW + timedelta(hours=9))
    assert report["status"] == "BLOCKED"
    assert any("archive differs" in reason for reason in report["reasons"])


def test_audit_rejects_fake_named_immutability_triggers(tmp_path, monkeypatch):
    from football_engine.ledger import audit_ledger_evidence
    from test_engine import NOW
    ledger, path = archived_settlement(tmp_path, monkeypatch)
    ledger.connection.execute("DROP TRIGGER immutable_events_delete")
    ledger.connection.execute("CREATE TRIGGER immutable_events_delete BEFORE DELETE ON events BEGIN SELECT 1; END")
    ledger.connection.commit()
    report = audit_ledger_evidence(manifest(path), NOW + timedelta(hours=9))
    assert report["status"] == "BLOCKED"
    assert "ledger immutability triggers ineffective" in report["reasons"]
    assert ledger.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


@pytest.mark.parametrize("status", ["unresolved", "void"])
def test_all_outcomes_require_exact_leg_identity(tmp_path, status):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.append("t", "pregame", pregame_payload())
    with pytest.raises(ValueError):
        ledger.append("t", "settlement", {"status": status, "source": "unverified", "legs": [{"status": status}]})


@pytest.mark.parametrize("minimum", [True, 1.5, "1", None, 0, -1])
def test_report_minimum_requires_positive_integer(tmp_path, minimum):
    with pytest.raises(ValueError):
        Ledger(tmp_path / "ledger.sqlite").prospective_report(minimum)
