"""Synthetic adversarial archives only; no actual forecasts or performance data."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from research.accuracy_backtest import _digest, audit_backtest_evidence
from test_accuracy_integration import backtest_manifest


AS_OF = "2026-01-06T00:00:00+00:00"


def read(record):
    return json.loads(Path(record["path"]).read_text())


def write(record, payload):
    path = Path(record["path"])
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def rebind(manifest):
    """Keep outer hashes coherent so tests exercise the intended inner contract."""
    write(manifest["selection_archive"], manifest["tickets"])
    for settlement in manifest["settlements"]:
        ticket = next(t for t in manifest["tickets"] if t["ticket_id"] == settlement["ticket_id"])
        settlement["ticket_sha256"] = _digest(ticket)
        write(settlement["source"], {k: v for k, v in settlement.items() if k != "source"})


def blocked(manifest, text=None):
    result = audit_backtest_evidence(manifest, AS_OF)
    assert result["status"] == "BLOCKED", result
    assert result["reasons"]
    assert result["evidence"]["prospective_validation"] is False
    assert result["evidence"]["supplied_evidence_consistent"] is False
    if text:
        assert any(text in reason for reason in result["reasons"]), result["reasons"]


def test_consistent_fixture_pass_means_no_claim_of_authenticity_or_skill(tmp_path):
    manifest = backtest_manifest(tmp_path)
    before = copy.deepcopy(manifest)
    raw_before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    result = audit_backtest_evidence(manifest, AS_OF)
    assert result["status"] == "PASS", result
    assert result["evidence"]["supplied_evidence_consistent"] is True
    assert result["evidence"]["historical_authenticity_verified"] is False
    assert result["evidence"]["prospective_validation"] is False
    assert result["evidence"]["price_available"] is True
    assert result["evidence"]["roi_available"] is False
    assert result["evidence"]["roi"] is None
    assert not {"wins", "losses", "hit_rate"} & result.keys()
    assert manifest == before
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == raw_before


@pytest.mark.parametrize("manifest", [None, {}, [], "missing", {"current_vintage": True}])
def test_absent_or_retrospective_evidence_blocks(manifest):
    blocked(manifest)


@pytest.mark.parametrize("stamp", [None, "2026-01-06", "bad", 3, True])
def test_invalid_as_of_fails_closed(stamp):
    assert audit_backtest_evidence({}, stamp)["status"] == "BLOCKED"


@pytest.mark.parametrize("field", ["rules_frozen_at", "price_available", "roi_available", "tickets", "selection_archive", "settlements", "folds"])
def test_required_manifest_fields_cannot_disappear(tmp_path, field):
    manifest = backtest_manifest(tmp_path)
    del manifest[field]
    blocked(manifest)


@pytest.mark.parametrize("field", ["ticket_id", "game_id", "kickoff", "frozen_at", "legs", "sources", "model_sha256"])
def test_required_ticket_fields_cannot_disappear(tmp_path, field):
    manifest = backtest_manifest(tmp_path)
    del manifest["tickets"][0][field]
    blocked(manifest)


@pytest.mark.parametrize("purpose", ["forecast", "roster", "quote", "model_inputs"])
@pytest.mark.parametrize("field", ["ticket_id", "game_id", "leg_ids", "legs", "observed_at"])
def test_archived_source_identity_binding_is_required(tmp_path, purpose, field):
    manifest = backtest_manifest(tmp_path)
    source = next(s for s in manifest["tickets"][0]["sources"] if s["purpose"] == purpose)
    payload = read(source)
    del payload[field]
    write(source, payload)
    rebind(manifest)
    blocked(manifest)


@pytest.mark.parametrize("purpose", ["forecast", "roster", "quote", "model_inputs"])
def test_source_cannot_substitute_other_leg_with_same_id(tmp_path, purpose):
    manifest = backtest_manifest(tmp_path)
    source = next(s for s in manifest["tickets"][0]["sources"] if s["purpose"] == purpose)
    payload = read(source)
    payload["legs"][0]["line"] = 999
    write(source, payload)
    rebind(manifest)
    blocked(manifest, "binding mismatch")


@pytest.mark.parametrize("field", ["published_at", "version_available_at", "observed_at"])
def test_current_vintage_sources_never_backdate_from_publication(tmp_path, field):
    manifest = backtest_manifest(tmp_path)
    manifest["tickets"][0]["sources"][0][field] = "2026-01-05T00:00:00+00:00"
    rebind(manifest)
    blocked(manifest, "after freeze/cutoff")


@pytest.mark.parametrize("change", [
    {"generated_at": "2026-01-05T00:00:00+00:00"},
    {"uses_postgame_participation": True}, {"didNotPlay": False},
    {"current_vintage": True}, {"postgame_selected": True},
    {"nested": {"didNotPlay": False}},
])
def test_archived_hindsight_cannot_hide_behind_clean_wrapper(tmp_path, change):
    manifest = backtest_manifest(tmp_path)
    source = manifest["tickets"][0]["sources"][0]
    payload = read(source)
    payload.update(change)
    write(source, payload)
    rebind(manifest)
    blocked(manifest)


@pytest.mark.parametrize("probability", [None, True, float("nan"), float("inf"), -0.1, 1.1])
def test_forecast_probabilities_are_finite_nonboolean(tmp_path, probability):
    manifest = backtest_manifest(tmp_path)
    source = manifest["tickets"][0]["sources"][0]
    payload = read(source)
    payload["probabilities"]["l"] = probability
    write(source, payload)
    rebind(manifest)
    blocked(manifest, "probabilities missing")


@pytest.mark.parametrize("players", [[], [{"player_id": "p", "team_id": "CHI", "active": "True"}],
    [{"player_id": "p", "team_id": "CHI", "active": True}, {"player_id": "p", "team_id": "CHI", "active": False}]])
def test_archived_roster_requires_unambiguous_explicit_active_status(tmp_path, players):
    manifest = backtest_manifest(tmp_path)
    source = manifest["tickets"][0]["sources"][1]
    payload = read(source)
    payload["players"] = players
    write(source, payload)
    rebind(manifest)
    blocked(manifest)


@pytest.mark.parametrize("field", ["fold_id", "train_start", "train_end", "trained_at", "train_games", "test_game_ids", "rules_frozen_at"])
def test_model_archive_must_bind_training_identity(tmp_path, field):
    manifest = backtest_manifest(tmp_path)
    record = manifest["folds"][0]["model"]
    payload = read(record)
    del payload[field]
    write(record, payload)
    # Update the consumer model identifiers to isolate the archived fold check.
    ticket = manifest["tickets"][0]
    ticket["model_sha256"] = record["sha256"]
    forecast = ticket["sources"][0]
    payload = read(forecast)
    payload["model_sha256"] = record["sha256"]
    write(forecast, payload)
    rebind(manifest)
    blocked(manifest, "model/fold/training identity mismatch")


def test_ticket_cannot_use_other_model(tmp_path):
    manifest = backtest_manifest(tmp_path)
    ticket = manifest["tickets"][0]
    ticket["model_sha256"] = "a" * 64
    source = ticket["sources"][0]
    payload = read(source)
    payload["model_sha256"] = "a" * 64
    write(source, payload)
    rebind(manifest)
    blocked(manifest, "ticket/fold model identity mismatch")


@pytest.mark.parametrize("mutation,reason", [
    (lambda m: m["tickets"][0].update(frozen_at=m["tickets"][0]["kickoff"]), "strictly before kickoff"),
    (lambda m: m["folds"][0].update(trained_at="2026-01-03T12:00:00+00:00"), "model trained after ticket freeze"),
    (lambda m: m["folds"][0]["train_games"][0].update(labels_available_at="2026-01-05T00:00:00+00:00"), "training labels"),
    (lambda m: m["folds"][0].update(test_game_ids=["unknown"]), "unknown train/test games"),
    (lambda m: m["folds"][0]["train_games"][0].update(game_id="g"), "overlapping"),
    (lambda m: m["folds"].append(dict(copy.deepcopy(m["folds"][0]), fold_id="f2")), "overlapping test windows"),
])
def test_chronological_and_walk_forward_leakage(tmp_path, mutation, reason):
    manifest = backtest_manifest(tmp_path)
    mutation(manifest)
    rebind(manifest)
    blocked(manifest, reason)


def test_forecast_cannot_predate_bound_model_training(tmp_path):
    manifest = backtest_manifest(tmp_path)
    source = manifest["tickets"][0]["sources"][0]
    for field in ("published_at", "version_available_at", "observed_at"):
        source[field] = "2026-01-01T12:00:00+00:00"
    payload = read(source)
    payload["observed_at"] = source["observed_at"]
    write(source, payload)
    rebind(manifest)
    blocked(manifest, "forecast predates model training")


@pytest.mark.parametrize("change", [{"basis": "legs"}, {"final": False}, {"authoritative": False},
    {"outcome": "unresolved"}, {"leg_ids": ["other"]}, {"settled_at": "2026-01-02T00:00:00+00:00"}])
def test_final_full_ticket_settlement_cannot_be_inferred_or_partial(tmp_path, change):
    manifest = backtest_manifest(tmp_path)
    manifest["settlements"][0].update(change)
    rebind(manifest)
    blocked(manifest)


def test_missing_ticket_outcome_never_inferred_from_leg_outcomes(tmp_path):
    manifest = backtest_manifest(tmp_path)
    settlement = manifest["settlements"][0]
    del settlement["outcome"]
    settlement["legs"] = [{"leg_id": "l", "outcome": "won"}]
    rebind(manifest)
    blocked(manifest, "full-ticket outcome required")


def test_pre_kickoff_final_source_is_not_authoritative_final_evidence(tmp_path):
    manifest = backtest_manifest(tmp_path)
    source = manifest["settlements"][0]["source"]
    for field in ("published_at", "version_available_at", "observed_at"):
        source[field] = "2026-01-02T00:00:00+00:00"
    blocked(manifest, "predates kickoff")


@pytest.mark.parametrize("fault", ["missing_file", "hash_mismatch", "selection_mutation", "settlement_mutation"])
def test_real_bytes_and_immutable_hash_links_are_required(tmp_path, fault):
    manifest = backtest_manifest(tmp_path)
    if fault == "missing_file":
        Path(manifest["selection_archive"]["path"]).unlink()
    elif fault == "hash_mismatch":
        Path(manifest["tickets"][0]["sources"][0]["path"]).write_text("{}")
    elif fault == "selection_mutation":
        manifest["tickets"][0]["legs"][0]["line"] = 42
    else:
        manifest["settlements"][0]["outcome"] = "lost"
    blocked(manifest)


def test_unpriced_statistical_evidence_cannot_claim_roi(tmp_path):
    manifest = backtest_manifest(tmp_path)
    manifest["price_available"] = False
    source = manifest["tickets"][0]["sources"][2]
    payload = read(source)
    payload["decimal_odds"] = None
    write(source, payload)
    rebind(manifest)
    result = audit_backtest_evidence(manifest, AS_OF)
    assert result["status"] == "PASS", result
    assert result["evidence"]["price_available"] is False
    assert result["evidence"]["roi_available"] is False
    manifest["roi_available"] = True
    blocked(manifest, "ROI requires available combined ticket prices")


@pytest.mark.parametrize("odds", [None, True, 1, float("inf")])
def test_declared_price_requires_combined_ticket_quote(tmp_path, odds):
    manifest = backtest_manifest(tmp_path)
    source = manifest["tickets"][0]["sources"][2]
    payload = read(source)
    payload["decimal_odds"] = odds
    write(source, payload)
    rebind(manifest)
    blocked(manifest, "combined ticket decimal price")


def test_roi_is_unavailable_without_explicit_cashflows(tmp_path):
    manifest = backtest_manifest(tmp_path)
    manifest["roi_available"] = True
    blocked(manifest, "stake/payout/currency")


@pytest.mark.parametrize("field", ["published_at", "version_available_at"])
def test_inner_publication_metadata_cannot_contradict_archive_wrapper(tmp_path, field):
    manifest = backtest_manifest(tmp_path)
    source = manifest["tickets"][0]["sources"][0]
    payload = read(source)
    payload[field] = "2026-01-05T00:00:00+00:00"
    write(source, payload)
    rebind(manifest)
    blocked(manifest, f"archived {field} mismatch")


@pytest.mark.parametrize("field", ["generated_at", "forecast_at"])
def test_forecast_generation_cannot_predate_training_even_if_observed_later(tmp_path, field):
    manifest = backtest_manifest(tmp_path)
    source = manifest["tickets"][0]["sources"][0]
    payload = read(source)
    payload[field] = "2026-01-01T12:00:00+00:00"
    write(source, payload)
    rebind(manifest)
    blocked(manifest, "forecast generated before model training")


def test_model_cannot_be_archived_before_training_finishes(tmp_path):
    manifest = backtest_manifest(tmp_path)
    model = manifest["folds"][0]["model"]
    for field in ("published_at", "version_available_at", "observed_at"):
        model[field] = "2026-01-01T00:00:00+00:00"
    blocked(manifest, "model archive observed before declared training completion")


def test_every_required_pregame_purpose_must_be_present(tmp_path):
    manifest = backtest_manifest(tmp_path)
    manifest["tickets"][0]["sources"].pop()
    rebind(manifest)
    blocked(manifest, "missing forecast/roster/quote/model_inputs")


def test_duplicate_source_purposes_cannot_choose_convenient_evidence(tmp_path):
    manifest = backtest_manifest(tmp_path)
    sources = manifest["tickets"][0]["sources"]
    sources.append(copy.deepcopy(sources[0]))
    rebind(manifest)
    blocked(manifest, "duplicate source purpose")


def test_duplicate_tickets_do_not_expand_apparent_sample(tmp_path):
    manifest = backtest_manifest(tmp_path)
    manifest["tickets"].append(copy.deepcopy(manifest["tickets"][0]))
    blocked(manifest, "duplicate IDs")


def test_settlement_evidence_after_as_of_is_not_yet_known(tmp_path):
    manifest = backtest_manifest(tmp_path)
    result = audit_backtest_evidence(manifest, "2026-01-04T12:00:00+00:00")
    assert result["status"] == "BLOCKED"
    assert any("after as_of" in reason for reason in result["reasons"])
