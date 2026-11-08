"""Current recording does not certify settled history or betting readiness."""
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3

import pytest

from football_engine.ledger import Ledger, audit_current_recording_evidence, audit_ledger_evidence
from test_ledger import pregame_payload


@pytest.fixture
def pending(tmp_path):
    path = tmp_path / "pending.sqlite"
    ledger = Ledger(path)
    payload = pregame_payload()
    ledger.append("future-ticket", "pregame", payload)
    ledger.connection.close()
    manifest = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return path, payload["game_id"], manifest


def test_pending_recording_does_not_require_future_settlement_or_clear_history(pending):
    path, game_id, manifest = pending
    before = path.read_bytes()
    now = datetime.now(timezone.utc)
    report = audit_current_recording_evidence(manifest, now, game_id=game_id, ticket_ids=["future-ticket"])
    assert report["status"] == "PASS"
    assert report["evidence"]["recording_status"] == "RECORDED_UNSETTLED"
    assert report["evidence"]["betting_ready"] is False
    assert report["evidence"]["quoted_proposals"] == 0
    historical = audit_ledger_evidence(manifest, now)
    assert historical["status"] == "BLOCKED"
    assert "no resolved full-ticket settlement evidence" in historical["reasons"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["wrong-game", "missing-ticket", "duplicate-ticket", "past-cutoff", "hash", "trigger"])
def test_current_recording_rejects_scope_time_and_integrity_damage(pending, damage):
    path, game_id, manifest = pending
    now = datetime.now(timezone.utc)
    tickets = ["future-ticket"]
    if damage == "wrong-game": game_id = "other-game"
    if damage == "missing-ticket": tickets = ["other-ticket"]
    if damage == "duplicate-ticket": tickets *= 2
    if damage == "past-cutoff": now += timedelta(days=1)
    if damage == "hash": manifest["sha256"] = "0" * 64
    if damage == "trigger":
        with sqlite3.connect(path) as db:
            db.execute("DROP TRIGGER immutable_events_update")
        manifest["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = audit_current_recording_evidence(manifest, now, game_id=game_id, ticket_ids=tickets)
    assert report["status"] == "BLOCKED"
    assert report["evidence"]["recording_status"] == "UNVERIFIED"


def test_historical_market_coverage_is_derived_from_settled_proposal(tmp_path, monkeypatch):
    from test_engine import NOW
    from test_ledger import archived_settlement, manifest
    ledger, path = archived_settlement(tmp_path, monkeypatch)
    report = audit_ledger_evidence(manifest(path), NOW + timedelta(hours=9))
    assert report["status"] == "PASS"
    assert report["evidence"]["markets"] == ["rushing_yards"]
    ledger.connection.close()
