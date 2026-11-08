import pytest

from research.calibration import fit_calibrator
from research.forward_calibrate import calibrate_by_week


def test_calibrator_is_finite_at_zero_and_one_and_responds_to_outcomes():
    examples = [(0.0, 1)] * 20 + [(0.0, 0)] * 80 + [(1.0, 1)] * 80 + [(1.0, 0)] * 20
    model = fit_calibrator(examples)
    assert model is not None
    assert 0 < model.predict(0) < model.predict(1) < 1
    assert model.predict(0) > 0.001
    assert model.predict(1) < 0.999


def test_calibrator_rejects_invalid_or_insufficient_training():
    assert fit_calibrator([(0.5, 1)]) is None
    with pytest.raises(ValueError):
        fit_calibrator([(1.5, 1)] * 100)


def test_forward_calibration_cannot_see_future_week_outcomes():
    prior = [{"market": "rushing_yards", "probability": 0.6, "outcome": int(index % 2 == 0)}
             for index in range(120)]
    future = [{"week": "1", "market": "rushing_yards", "probability": "0.7", "outcome": "1"},
              {"week": "2", "market": "rushing_yards", "probability": "0.7", "outcome": "0"}]
    first, first_log = calibrate_by_week(future, prior)
    future[1]["outcome"] = "1"
    second, second_log = calibrate_by_week(future, prior)
    assert first[0]["calibrated_probability"] == second[0]["calibrated_probability"]
    assert first[1]["calibrated_probability"] == second[1]["calibrated_probability"]
    assert first_log[0]["training_examples_by_market"]["rushing_yards"] == 120
    assert first_log[1]["training_examples_by_market"]["rushing_yards"] == 121
    assert second_log[:2] == first_log[:2]
    assert second_log[2] != first_log[2]


@pytest.mark.parametrize("examples", [[(float('nan'), 1)], [(0.5, True)], [(0.5, '1')], [(0.5, 1, 2)], [(True, 0)]])
def test_invalid_examples_are_rejected_even_below_minimum(examples):
    with pytest.raises(ValueError):
        fit_calibrator(examples)


@pytest.mark.parametrize("regularization", [float('nan'), float('inf'), 0, -1, True])
def test_invalid_regularization(regularization):
    with pytest.raises(ValueError):
        fit_calibrator([(0.2, 0), (0.8, 1)], regularization=regularization)


def test_empty_and_one_class_samples_do_not_fit():
    assert fit_calibrator([], minimum_examples=1) is None
    assert fit_calibrator([(0.2, 1), (0.8, 1)] * 100) is None


def evidence_manifest():
    # Synthetic unit-test records only; no historical outcomes are asserted.
    from research.calibration import _content_hash
    model = {"artifact": {"intercept": 0, "slope": 1}, "fitted_at": "2026-01-02T00:00:00Z"}
    inputs = {"artifact": {"source": "synthetic-test"}, "available_at": "2026-01-02T00:00:00Z"}
    training = {"rows": [{"event_id": "train", "outcome_available_at": "2026-01-01T00:00:00Z"}]}
    for block, key in ((model, "artifact"), (inputs, "artifact"), (training, "rows")):
        block["sha256"] = _content_hash(block[key])
    model['training_sha256'] = training['sha256']
    model['frozen_sha256'] = _content_hash({k: model[k] for k in ('artifact', 'fitted_at', 'training_sha256')})
    inputs['frozen_sha256'] = _content_hash({k: inputs[k] for k in ('artifact', 'available_at')})
    row = {"id": "test", "event_id": "holdout", "forecast_at": "2026-01-03T00:00:00Z",
           "event_at": "2026-01-04T00:00:00Z", "outcome_available_at": "2026-01-05T00:00:00Z",
           "input_available_at": "2026-01-02T00:00:00Z", "probability": 0.6,
           "baseline_probability": 0.5, "outcome": 1}
    result = {"model": model, "inputs": inputs, "training": training}
    for name in ("evaluation", "joint_validation"):
        rows = [dict(row, leg_ids=["a", "b"])] if name == "joint_validation" else [row]
        result[name] = {"rows": rows, "sha256": _content_hash(rows), "baseline": "synthetic constant",
                        "model_sha256": model["sha256"], "inputs_sha256": inputs["sha256"],
                        "frozen_model_sha256": model["frozen_sha256"],
                        "frozen_inputs_sha256": inputs["frozen_sha256"],
                        "metrics": {"brier": 0.16, "baseline_brier": 0.25},
                        "dependence_method": "synthetic joint model"}
    return result


def test_audit_validates_supplied_evidence_without_claiming_improvement():
    from research.calibration import audit_calibration_evidence
    manifest = evidence_manifest()
    # Worse than baseline also passes: this audits evidence, not profitability.
    from research.calibration import _content_hash
    manifest['evaluation']['rows'][0]['probability'] = 0.1
    manifest['evaluation']['sha256'] = _content_hash(manifest['evaluation']['rows'])
    manifest['evaluation']['metrics']['brier'] = 0.81
    result = audit_calibration_evidence(manifest, '2026-01-06T00:00:00Z')
    assert result['status'] == 'PASS'
    assert result['evidence']['joint_validation']['sample_size'] == 1
    assert result['evidence']['sample_sufficiency'] == 'NOT_ASSESSED'
    assert result['evidence']['performance_approval'] == 'NOT_ASSESSED'


@pytest.mark.parametrize('field,value', [('event_id', 'train'), ('forecast_at', '2026-01-04T00:00:00Z'),
    ('input_available_at', '2026-01-04T00:00:00Z'), ('outcome_available_at', '2027-01-05T00:00:00Z'),
    ('probability', float('nan')), ('outcome', True)])
def test_audit_blocks_leakage_and_malformed_rows(field, value):
    from research.calibration import audit_calibration_evidence, _content_hash
    manifest = evidence_manifest()
    block = manifest['evaluation']
    block['rows'][0][field] = value
    if field != 'probability':
        block['sha256'] = _content_hash(block['rows'])
    assert audit_calibration_evidence(manifest, '2026-01-06T00:00:00Z')['status'] == 'BLOCKED'


@pytest.mark.parametrize('mutation', ['missing_joint', 'hash', 'binding', 'metric', 'baseline', 'legs', 'duplicate', 'fit_time'])
def test_audit_blocks_missing_or_inconsistent_evidence(mutation):
    from research.calibration import audit_calibration_evidence, _content_hash
    manifest = evidence_manifest()
    if mutation == 'missing_joint':
        del manifest['joint_validation']
    elif mutation == 'hash':
        manifest['model']['artifact']['slope'] = 2
    elif mutation == 'binding':
        manifest['evaluation']['model_sha256'] = '0' * 64
    elif mutation == 'metric':
        manifest['evaluation']['metrics']['brier'] = 0
    elif mutation == 'baseline':
        del manifest['evaluation']['baseline']
    elif mutation == 'fit_time':
        manifest['model']['fitted_at'] = '2026-01-01T00:00:00Z'
    else:
        block = manifest['joint_validation']
        if mutation == 'legs':
            block['rows'][0]['leg_ids'] = ['a', 'a']
        else:
            block['rows'] *= 2
        block['sha256'] = _content_hash(block['rows'])
    assert audit_calibration_evidence(manifest, '2026-01-06T00:00:00Z')['status'] == 'BLOCKED'


@pytest.mark.parametrize('manifest,as_of', [(None, '2026-01-01T00:00:00Z'), ({}, None), ({}, '2026-01-01')])
def test_audit_bad_envelopes_fail_closed(manifest, as_of):
    from research.calibration import audit_calibration_evidence
    assert audit_calibration_evidence(manifest, as_of)['status'] == 'BLOCKED'


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), True, '0.5', 10**1000])
def test_prediction_rejects_nonfinite_and_malformed_probabilities(value):
    from research.calibration import Calibrator
    with pytest.raises(ValueError):
        Calibrator(0, 1, 100).predict(value)


@pytest.mark.parametrize('intercept,slope,size', [(float('nan'), 1, 2), (0, float('inf'), 2),
    (10**1000, 1, 2), (0, 1, True), (0, 1, -1)])
def test_invalid_manual_calibrator_is_rejected(intercept, slope, size):
    from research.calibration import Calibrator
    with pytest.raises(ValueError):
        Calibrator(intercept, slope, size)


@pytest.mark.parametrize('regularization', [10**1000, '10', None])
def test_malformed_or_unrepresentable_regularization(regularization):
    with pytest.raises(ValueError):
        fit_calibrator([(0.1, 0), (0.9, 1)], regularization=regularization)


def test_clipped_constant_predictors_are_degenerate():
    assert fit_calibrator([(0, 0), (0.0001, 1)] * 100) is None


def test_damped_fit_improves_extreme_reversed_training_loss():
    from math import log
    examples = [(0.001, 1)] * 90 + [(0.001, 0)] * 10 + [(0.999, 0)] * 90 + [(0.999, 1)] * 10
    model = fit_calibrator(examples, regularization=0.01)
    assert model is not None
    def loss(prediction):
        return -sum(log(prediction(p) if y else 1 - prediction(p)) for p, y in examples)
    assert loss(model.predict) < loss(lambda p: p)
    assert all(0 < model.predict(p) < 1 for p in (0, 0.5, 1))


@pytest.mark.parametrize('mutation', ['fitted_at', 'available_at', 'training', 'frozen_binding', 'missing_frozen'])
def test_frozen_provenance_rejects_metadata_and_training_replacement(mutation):
    from research.calibration import audit_calibration_evidence, _content_hash
    manifest = evidence_manifest()
    if mutation == 'fitted_at':
        manifest['model']['fitted_at'] = '2026-01-02T12:00:00Z'
    elif mutation == 'available_at':
        manifest['inputs']['available_at'] = '2026-01-02T12:00:00Z'
    elif mutation == 'training':
        manifest['training']['rows'][0]['event_id'] = 'replacement'
        manifest['training']['sha256'] = _content_hash(manifest['training']['rows'])
    elif mutation == 'frozen_binding':
        manifest['joint_validation']['frozen_model_sha256'] = '0' * 64
    else:
        del manifest['inputs']['frozen_sha256']
    result = audit_calibration_evidence(manifest, '2026-01-06T00:00:00Z')
    assert result['status'] == 'BLOCKED'


@pytest.mark.parametrize('metrics', [None, [], 'brier', {'log_loss': float('nan')},
    {'brier': 0.16, 'baseline_brier': True}, {'brier': 0.16},
    {'brier': 0.16, 'baseline_brier': 0.25, 'baseline_log_loss': float('nan')}])
def test_metrics_must_be_finite_complete_pairs(metrics):
    from research.calibration import audit_calibration_evidence
    manifest = evidence_manifest()
    manifest['evaluation']['metrics'] = metrics
    assert audit_calibration_evidence(manifest, '2026-01-06T00:00:00Z')['status'] == 'BLOCKED'


def test_log_loss_only_evidence_and_infinite_loss():
    from math import log
    from research.calibration import audit_calibration_evidence, _content_hash
    manifest = evidence_manifest()
    for name in ('evaluation', 'joint_validation'):
        manifest[name]['metrics'] = {'log_loss': -log(0.6), 'baseline_log_loss': -log(0.5)}
    assert audit_calibration_evidence(manifest, '2026-01-06T00:00:00Z')['status'] == 'PASS'
    manifest['evaluation']['rows'][0]['probability'] = 0
    manifest['evaluation']['sha256'] = _content_hash(manifest['evaluation']['rows'])
    result = audit_calibration_evidence(manifest, '2026-01-06T00:00:00Z')
    assert result['status'] == 'BLOCKED'
    assert any('infinite log loss' in reason for reason in result['reasons'])
