"""Synthetic contract tests; no real forecasts or historical results."""
from copy import deepcopy
import hashlib
import json

import pytest

from football_engine.team_profiles import content_hash
from research.evidence_scope import bind_game_evidence


def good(evidence):
    return {"status": "PASS", "reasons": [], "evidence": evidence}


@pytest.fixture
def selected(tmp_path):
    opportunity = tmp_path / "opportunity.json"
    opportunity.write_text(json.dumps({"legs": [{"market": "passing_yards"}]}))
    backtest = {"tickets": [{"game_id": "old-game", "legs": [{"market": "passing_yards"}]}]}
    model = {"version": "fixture-v1", "validation_evidence": {
        "markets": ["passing_yards"], "backtest_sha256": content_hash(backtest),
        "ledger_sha256": "b" * 64,
    }}
    manifest = {"opportunity_snapshot": {"path": str(opportunity),
                    "sha256": hashlib.sha256(opportunity.read_bytes()).hexdigest()},
                "backtest": backtest,
                "calibration": {"model": {"artifact": model, "sha256": content_hash(model)}}}
    scope = {"audit_mode": "game", "game_id": "future-game", "model_sha256": content_hash(model),
             "team_profile_versions": {"BAL": "b", "JAX": "j"},
             "kickoff": "2026-11-06T01:15:00+00:00"}
    return scope, manifest


@pytest.mark.parametrize("field,value", [
    ("game_id", "other-game"), ("team_profile_versions", {"BAL": "stale", "JAX": "j"}),
    ("model_sha256", "a" * 64), ("kickoff", "2026-11-07T01:15:00+00:00"),
])
def test_success_for_other_game_or_model_does_not_clear_scope(selected, field, value):
    scope, manifest = selected
    evidence = {k: scope[k] for k in ("game_id", "team_profile_versions", "model_sha256", "kickoff")}
    evidence[field] = value
    original = good(evidence)
    result = bind_game_evidence(original, "remaining", scope, manifest)
    assert result["status"] == "BLOCKED"
    assert original["status"] == "PASS"  # no mutation of original auditor evidence


def test_kickoff_offsets_compare_instants(selected):
    scope, manifest = selected
    evidence = {k: scope[k] for k in ("game_id", "team_profile_versions", "model_sha256", "kickoff")}
    evidence["kickoff"] = "2026-11-05T20:15:00-05:00"
    assert bind_game_evidence(good(evidence), "remaining", scope, manifest)["status"] == "PASS"
    evidence["kickoff"] = "2026-11-06T01:15:00"
    assert bind_game_evidence(good(evidence), "remaining", scope, manifest)["status"] == "BLOCKED"


def test_calibration_for_other_model_blocks(selected):
    scope, manifest = selected
    assert bind_game_evidence(good({"model_sha256": "a" * 64}), "correlation", scope, manifest)["status"] == "BLOCKED"
    scope.pop("model_sha256")
    assert bind_game_evidence(good({"fixture": True}), "correlation", scope, manifest)["status"] == "BLOCKED"


def test_history_can_cover_past_games_but_not_unrelated_model_or_markets(selected):
    scope, manifest = selected
    assert bind_game_evidence(good({"fixture": True}), "scenario", scope, manifest)["status"] == "PASS"
    assert bind_game_evidence(good({"sha256": "b" * 64, "markets": ["passing_yards"]}), "ledger", scope, manifest)["status"] == "PASS"
    assert bind_game_evidence(good({"sha256": "b" * 64, "markets": ["rushing_yards"]}), "ledger", scope, manifest)["status"] == "BLOCKED"
    assert bind_game_evidence(good({"sha256": "b" * 64}), "ledger", scope, manifest)["status"] == "BLOCKED"
    assert bind_game_evidence(good({"sha256": "c" * 64}), "ledger", scope, manifest)["status"] == "BLOCKED"
    tampered = deepcopy(manifest)
    tampered["backtest"]["tickets"][0]["legs"][0]["market"] = "anytime_td"
    assert bind_game_evidence(good({"fixture": True}), "scenario", scope, tampered)["status"] == "BLOCKED"
    manifest["calibration"]["model"]["artifact"]["version"] = "different"
    assert bind_game_evidence(good({"fixture": True}), "ledger", scope, manifest)["status"] == "BLOCKED"


def test_original_failure_and_all_profile_checks_are_preserved(selected):
    scope, manifest = selected
    blocked = {"status": "BLOCKED", "reasons": ["missing real model"], "evidence": {}}
    assert bind_game_evidence(blocked, "correlation", scope, manifest) == blocked
    scope["audit_mode"] = "all_profiles"
    original = good({"fixture": True})
    assert bind_game_evidence(original, "correlation", scope, manifest) == original
