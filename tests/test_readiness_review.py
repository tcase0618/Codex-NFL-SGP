"""Real seven-check wiring on synthetic local sources, never betting evidence."""
import json

import pytest

from research import readiness_review
from test_accuracy_integration import scoped_audit_files
from test_engine import NOW


@pytest.fixture(autouse=True)
def fixture_clock(monkeypatch):
    monkeypatch.setattr(readiness_review, "utc_now", lambda: NOW)


def test_review_runs_all_checks_and_preserves_source_inputs(scoped_audit_files):
    f = scoped_audit_files
    database = f["tmp_path"] / "empty.sqlite"
    database.write_bytes(b"not a usable play store")
    output = f["tmp_path"] / "review"
    report = readiness_review.run(f["config"], database, output,
                                  game_id="fixture-game", teams=["JAX", "BAL"])
    assert report["status"] == "BLOCKED"
    assert report["betting_ready"] is False and report["exit_code"] == 2
    assert report["changed_inputs"] == []
    assert report["preparation"]["current_recording"]["status"] == "UNAVAILABLE"
    audit = json.loads((output / "seven-auditor/accuracy-report.json").read_text())
    assert len(audit["auditors"]) == 7
    assert audit["scope"]["game_id"] == "fixture-game"
    assert (output / "review.md").is_file()
    with pytest.raises(FileExistsError):
        readiness_review.run(f["config"], database, output, game_id="fixture-game", teams=["JAX", "BAL"])


def test_review_detects_concurrent_input_change(scoped_audit_files, monkeypatch):
    f = scoped_audit_files
    database = f["tmp_path"] / "database.sqlite"
    database.write_bytes(b"fixture")
    original = readiness_review.run_swarm
    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        database.write_bytes(b"changed during audit")
        return result
    monkeypatch.setattr(readiness_review, "run_swarm", mutate)
    report = readiness_review.run(f["config"], database, f["tmp_path"] / "changed-review",
                                  game_id="fixture-game", teams=["BAL", "JAX"])
    assert report["changed_inputs"] == [str(database)]
    assert report["status"] == "BLOCKED"


def test_review_cannot_write_into_runtime(scoped_audit_files):
    f = scoped_audit_files
    with pytest.raises(ValueError, match="runtime"):
        readiness_review.run(f["config"], f["tmp_path"] / "irrelevant.sqlite", f["runtime"] / "review",
                              game_id="fixture-game", teams=["BAL", "JAX"])
