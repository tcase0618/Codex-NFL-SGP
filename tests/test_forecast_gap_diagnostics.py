"""Synthetic fixtures exercise diagnostics only; never historical evidence."""

import csv
import json
from pathlib import Path

import pytest

from research import forecast_gap_diagnostics as diagnostics
from research.accuracy_backtest import audit_backtest_evidence
from research.calibration import audit_calibration_evidence


CUTOFF = "2026-11-04T03:00:00Z"


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def forecast(**changes):
    return {"season": "2026", "week": "1", "game_id": "synthetic-game",
            "team_id": "TEST", "player_id": "test-player", "market": "passing_yards",
            "line": "200", "probability": "0.6", "outcome": "1", **changes}


def test_reconstruction_rows_report_missing_provenance_and_duplicate_grain(tmp_path):
    path = tmp_path / "rows.csv"
    write_csv(path, [forecast(), forecast(), forecast(game_id="another", outcome="")])
    report = diagnostics.csv_coverage(path, forecast=True)
    assert report["rows"] == 3
    assert report["games"] == 2
    assert report["resolved_rows"] == 2
    assert report["duplicate_candidate_keys"] == 1
    assert report["missing_row_fields"] == dict.fromkeys(diagnostics.FORECAST_FIELDS, 3)
    assert report["rows_with_all_required_fields"] == 0
    assert report["pregame_certified_rows"] == 0


@pytest.mark.parametrize("probability,outcome", [("nan", "1"), ("inf", "0"), ("-0.1", "1"),
                                                 ("1.1", "0"), ("bad", "1"), ("0.5", "True")])
def test_invalid_probability_or_label_is_visible(tmp_path, probability, outcome):
    path = tmp_path / "rows.csv"
    write_csv(path, [forecast(probability=probability, outcome=outcome)])
    assert diagnostics.csv_coverage(path, forecast=True)["invalid_probability_or_outcome"] == 1


def test_schema_presence_is_never_historical_certification(tmp_path):
    row = forecast(**dict.fromkeys(diagnostics.FORECAST_FIELDS, "synthetic-value"))
    path = tmp_path / "rows.csv"
    write_csv(path, [row])
    result = diagnostics.csv_coverage(path, forecast=True)
    assert result["rows_with_all_required_fields"] == 1
    assert result["pregame_certified_rows"] == 0


def test_field_missingness_counts_union_correctly(tmp_path):
    full = forecast(**dict.fromkeys(diagnostics.FORECAST_FIELDS, "synthetic"))
    path = tmp_path / "rows.csv"
    write_csv(path, [dict(full, forecast_at=""), dict(full, model_sha256=""), full])
    result = diagnostics.csv_coverage(path, forecast=True)
    assert result["rows_with_all_required_fields"] == 1
    assert result["missing_row_fields"] == {"forecast_at": 1, "model_sha256": 1}


@pytest.mark.parametrize("bundle", [None, [], {}, {"calibration": None, "backtest": {}},
                                    {"calibration": {"model": {}}, "backtest": {"kind": "reconstruction"}}])
def test_unchanged_auditor_results_are_returned_exactly(bundle):
    result = diagnostics.audit_bundle(bundle, CUTOFF)
    source = bundle if isinstance(bundle, dict) else {"calibration": None, "backtest": None}
    assert result["calibration"] == audit_calibration_evidence(source.get("calibration", {}), CUTOFF)
    assert result["backtest"] == audit_backtest_evidence(source.get("backtest", {}), CUTOFF)


def test_auditor_failure_fails_closed_without_suppressing_other_result(monkeypatch):
    def broken(*args):
        raise RuntimeError("test failure")
    monkeypatch.setattr(diagnostics, "audit_calibration_evidence", broken)
    result = diagnostics.audit_bundle({}, CUTOFF)
    assert result["calibration"]["status"] == "BLOCKED"
    assert result["calibration"]["evidence"]["check_completed"] is False
    assert result["backtest"] == audit_backtest_evidence({}, CUTOFF)


def test_real_run_preserves_sources_and_writes_diagnostics_only(tmp_path):
    source = tmp_path / "outputs" / "replay-2026-predictions.csv"
    write_csv(source, [forecast()])
    hist = tmp_path / "work" / "historical-nflverse" / "weekly.csv"
    write_csv(hist, [{"season": "2026", "week": "1", "status": "ACT"}])
    evidence = tmp_path / "outputs" / "prior" / "evidence-manifest.json"
    evidence.parent.mkdir()
    evidence.write_text('{"derived_metrics": {}}')
    gate = tmp_path / "outputs" / "strict-pregame-coverage.json"
    gate.write_text(json.dumps({"games": 1, "games_missing_evidence": 1,
                                "records": [{"reasons": ["missing inputs"]}]}))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    output = tmp_path / "outputs" / "gap-repair-forecast"
    result = diagnostics.run(tmp_path, output, as_of=CUTOFF)
    assert result["status"] == "BLOCKED"
    assert result["walk_forward_executed"] is False
    assert result["certified_accuracy"] is None
    assert result["roi"] is None
    assert result["changed_inputs"] == []
    assert result["saved_gates"][0]["reason_counts"] == {"missing inputs": 1}
    assert len(result["datasets"]) == 2
    assert len(result["evidence_bundles"]) == 1
    assert {p.name for p in output.iterdir()} == {"diagnostics.json", "report.md", "collector-path.md"}
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert json.loads((output / "diagnostics.json").read_text())["status"] == "BLOCKED"
    with pytest.raises(FileExistsError):
        diagnostics.run(tmp_path, output, as_of=CUTOFF)


def test_malformed_and_explicit_missing_evidence_are_reported(tmp_path):
    folder = tmp_path / "outputs" / "old"
    folder.mkdir(parents=True)
    (folder / "evidence-manifest.json").write_text("{bad json")
    result = diagnostics.run(tmp_path, tmp_path / "outputs" / "gap-repair-forecast",
                             evidence_path=tmp_path / "missing.json", as_of=CUTOFF)
    assert len(result["input_errors"]) == 2
    assert result["status"] == "BLOCKED"


def test_reconstruction_selection_is_audited_and_never_wrapped_as_certified(tmp_path):
    folder = tmp_path / "outputs" / "reconstruction"
    folder.mkdir(parents=True)
    manifest = {"kind": "UNPRICED_RETROSPECTIVE_STATISTICAL_RECONSTRUCTION", "tickets": []}
    (folder / "selection-manifest.json").write_text(json.dumps(manifest))
    result = diagnostics.run(tmp_path, tmp_path / "outputs" / "gap-repair-forecast", as_of=CUTOFF)
    bundle = result["evidence_bundles"][0]
    assert bundle["reconstruction_candidate"] is True
    assert bundle["audits"]["backtest"] == audit_backtest_evidence(manifest, CUTOFF)
    assert result["contract_consistent_bundles"] == []


def test_consistent_evidence_does_not_authorize_readiness(monkeypatch, tmp_path):
    # Mock consistency only; no fabricated passing historical fixture is emitted.
    passed = {"status": "PASS", "reasons": [], "evidence": {"scope": "consistency only"}}
    monkeypatch.setattr(diagnostics, "audit_bundle", lambda *args: {"calibration": passed, "backtest": passed})
    folder = tmp_path / "outputs" / "prior"
    folder.mkdir(parents=True)
    (folder / "evidence-manifest.json").write_text("{}")
    result = diagnostics.run(tmp_path, tmp_path / "outputs" / "gap-repair-forecast", as_of=CUTOFF)
    assert len(result["contract_consistent_bundles"]) == 1
    assert result["status"] == "BLOCKED"
    assert result["betting_ready"] is False
    assert result["historically_pregame_certified"] is False


@pytest.mark.parametrize("cutoff", ["2026-11-03", "bad-time"])
def test_invalid_cutoff_prevents_outputs(tmp_path, cutoff):
    output = tmp_path / "outputs" / "gap-repair-forecast"
    with pytest.raises(ValueError):
        diagnostics.run(tmp_path, output, as_of=cutoff)
    assert not output.exists()


def test_outside_output_scope_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="output must be within"):
        diagnostics.run(tmp_path, tmp_path / "work" / "state", as_of=CUTOFF)


def test_collector_plan_requires_real_frozen_evidence_and_joint_validation():
    plan = diagnostics.collector_plan()
    for required in ("untouched", "training_sha256", "frozen_sha256", "joint_validation",
                     "dependence_method", "quote", "full-ticket", "seven", "No bets"):
        assert required in plan


def test_jsonl_row_log_counts_malformed_rows_and_missing_provenance(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(forecast()) + '\n\n{bad\n[]\n')
    result = diagnostics.jsonl_coverage(path)
    assert result["rows"] == 3
    assert result["malformed_rows"] == 2
    assert result["missing_row_fields"] == dict.fromkeys(diagnostics.FORECAST_FIELDS, 1)
    assert result["pregame_certified_rows"] == 0


def test_run_discovers_row_logs_but_excludes_parallel_test_artifacts(tmp_path):
    folder = tmp_path / "outputs"
    folder.mkdir()
    (folder / "replay-2026-row-audit.jsonl").write_text(json.dumps(forecast()))
    parallel = folder / "gap-repair-another"
    parallel.mkdir()
    (parallel / "evidence-manifest.json").write_text('{"not": "actual evidence"}')
    result = diagnostics.run(tmp_path, folder / "gap-repair-forecast", as_of=CUTOFF)
    assert len(result["datasets"]) == 1
    assert result["datasets"][0]["rows"] == 1
    assert result["evidence_bundles"] == []
