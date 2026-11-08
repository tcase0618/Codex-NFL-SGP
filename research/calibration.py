"""Chronological, market-specific logistic probability calibration.

Fit only on forecasts whose outcomes were known before the evaluation week.
The fitted probabilities are research estimates, never executable prices.
"""

from dataclasses import dataclass
from math import exp, isfinite, log, log1p
from numbers import Real
from datetime import datetime
import hashlib
import json


def _valid_probability(value) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and 0 <= value <= 1 and isfinite(value)


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + exp(-value))
    scaled = exp(value)
    return scaled / (1 + scaled)


def _logit(probability: float) -> float:
    bounded = max(0.001, min(0.999, probability))
    return log(bounded / (1 - bounded))


@dataclass(frozen=True)
class Calibrator:
    intercept: float
    slope: float
    sample_size: int

    def __post_init__(self):
        try:
            valid = all(isinstance(v, Real) and not isinstance(v, bool) and isfinite(v)
                        for v in (self.intercept, self.slope))
        except OverflowError:
            valid = False
        if not valid or type(self.sample_size) is not int or self.sample_size < 0:
            raise ValueError("invalid calibrator parameters")

    def predict(self, raw_probability: float) -> float:
        if not _valid_probability(raw_probability):
            raise ValueError("probability out of range")
        return _sigmoid(self.intercept + self.slope * _logit(raw_probability))


def fit_calibrator(examples: list[tuple[float, int]], *, minimum_examples: int = 100,
                   regularization: float = 10.0) -> Calibrator | None:
    try:
        finite_regularization = isfinite(regularization)
    except (TypeError, OverflowError):
        finite_regularization = False
    if (type(minimum_examples) is not int or minimum_examples < 1
            or not isinstance(regularization, Real) or isinstance(regularization, bool)
            or not finite_regularization or regularization <= 0):
        raise ValueError("invalid calibration configuration")
    for example in examples:
        if (not isinstance(example, (tuple, list)) or len(example) != 2
                or not _valid_probability(example[0])
                or type(example[1]) is not int or example[1] not in (0, 1)):
            raise ValueError("invalid calibration example")
    if (len(examples) < minimum_examples or len({row[1] for row in examples}) < 2
            or len({_logit(row[0]) for row in examples}) < 2):
        return None
    predictors = [(_logit(p), y) for p, y in examples]

    def objective(a, b):
        penalty = regularization / 2 * (a * a + (b - 1) ** 2)
        return penalty + sum(max(a + b*x, 0) - y*(a + b*x)
                             + log1p(exp(-abs(a + b*x))) for x, y in predictors)

    intercept, slope = 0.0, 1.0
    for _ in range(100):
        gradient_a = regularization * intercept
        gradient_b = regularization * (slope - 1)
        hessian_aa = regularization
        hessian_ab = 0.0
        hessian_bb = regularization
        for raw_probability, outcome in examples:
            predictor = _logit(raw_probability)
            fitted = _sigmoid(intercept + slope * predictor)
            difference = fitted - outcome
            weight = fitted * (1 - fitted)
            gradient_a += difference
            gradient_b += difference * predictor
            hessian_aa += weight
            hessian_ab += weight * predictor
            hessian_bb += weight * predictor * predictor
        determinant = hessian_aa * hessian_bb - hessian_ab * hessian_ab
        if not isfinite(determinant) or determinant <= 0:
            return None
        step_a = (hessian_bb * gradient_a - hessian_ab * gradient_b) / determinant
        step_b = (hessian_aa * gradient_b - hessian_ab * gradient_a) / determinant
        # Backtracking prevents a Newton step from bouncing between bounds.
        scale = 1.0
        current_loss = objective(intercept, slope)
        for _ in range(60):
            new_intercept = max(-5, min(5, intercept - scale * step_a))
            new_slope = max(0.05, min(3, slope - scale * step_b))
            if objective(new_intercept, new_slope) <= current_loss:
                break
            scale *= 0.5
        else:
            return None
        if abs(new_intercept - intercept) + abs(new_slope - slope) < 1e-8:
            intercept, slope = new_intercept, new_slope
            break
        intercept, slope = new_intercept, new_slope
    return Calibrator(intercept, slope, len(examples))


def _evidence_time(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return parsed


def _content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def audit_calibration_evidence(manifest, as_of) -> dict:
    """Audit supplied research evidence; PASS is not betting or model approval.

    Schema v1: model and inputs contain JSON ``artifact`` and canonical-JSON
    ``sha256``. Model also has ``fitted_at``; inputs have ``available_at``.
    training contains hashed ``rows`` with event_id/outcome_available_at.
    evaluation and joint_validation contain hashed rows, a named ``baseline``,
    and ``metrics`` holding brier and baseline_brier (or log_loss equivalents).
    Both evaluation sections bind model_sha256 and inputs_sha256.
    Each row has id, event_id, forecast_at, event_at, input_available_at,
    outcome_available_at, probability, baseline_probability, and binary outcome.
    Joint rows additionally require at least two distinct leg_ids and the
    section must name a dependence_method. Event IDs must be disjoint from
    training. Metrics are recomputed on the identical supplied samples.

    Schema v2 additionally requires model.training_sha256, and frozen_sha256
    on model/inputs. The frozen model digest hashes artifact, fitted_at and
    training_sha256; the frozen inputs digest hashes artifact and available_at.
    Both evaluation sections bind frozen_model_sha256/frozen_inputs_sha256.
    These protect metadata and training membership as well as artifact bytes.
    PASS means evidence consistency only: sample sufficiency, performance and
    deployment approval are explicitly NOT_ASSESSED.

    Hashes establish internal integrity only, not authenticity of archives or
    independence of model development. External provenance still needs review.
    """
    reasons, evidence = [], {}
    try:
        cutoff = _evidence_time(as_of)
    except (ValueError, TypeError, AttributeError, OverflowError):
        return {"status": "BLOCKED", "reasons": ["invalid as_of timestamp"], "evidence": {}}
    if not isinstance(manifest, dict):
        return {"status": "BLOCKED", "reasons": ["manifest must be an object"], "evidence": {}}

    def section(name, payload):
        block = manifest.get(name)
        if not isinstance(block, dict):
            raise ValueError(f"missing {name}")
        digest = _content_hash(block[payload])
        if block.get("sha256") != digest:
            raise ValueError(f"{name} hash mismatch")
        evidence[name + "_sha256"] = digest
        return block

    try:
        model = section("model", "artifact")
        inputs = section("inputs", "artifact")
        fitted = _evidence_time(model["fitted_at"])
        available = _evidence_time(inputs["available_at"])
        if fitted > cutoff or available > cutoff:
            raise ValueError("model or inputs unavailable as_of")
        if not model["artifact"] or not inputs["artifact"]:
            raise ValueError("empty model or input artifact")
        training_block = section("training", "rows")
        training = training_block["rows"]
        if model.get("training_sha256") != training_block["sha256"]:
            raise ValueError("model/training binding mismatch")
        for name, block, keys in (
            ("model", model, ("artifact", "fitted_at", "training_sha256")),
            ("inputs", inputs, ("artifact", "available_at")),
        ):
            frozen = _content_hash({key: block[key] for key in keys})
            if block.get("frozen_sha256") != frozen:
                raise ValueError(f"{name} frozen provenance hash mismatch")
            evidence["frozen_" + name + "_sha256"] = frozen
        if not isinstance(training, list) or not training:
            raise ValueError("missing training rows")
        training_events = set()
        for row in training:
            event = row["event_id"]
            if not isinstance(event, str) or not event.strip():
                raise ValueError("invalid training event_id")
            training_events.add(event)
            if _evidence_time(row["outcome_available_at"]) >= fitted:
                raise ValueError("training outcome unavailable before model fit")
        evidence["training_events"] = len(training_events)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return {"status": "BLOCKED", "reasons": [f"provenance: {exc}"], "evidence": evidence}

    for name in ("evaluation", "joint_validation"):
        try:
            block = section(name, "rows")
            if (block.get("model_sha256") != model["sha256"]
                    or block.get("inputs_sha256") != inputs["sha256"]
                    or block.get("frozen_model_sha256") != model["frozen_sha256"]
                    or block.get("frozen_inputs_sha256") != inputs["frozen_sha256"]):
                raise ValueError("model/input binding mismatch")
            if not isinstance(block.get("baseline"), str) or not block["baseline"].strip():
                raise ValueError("missing comparison baseline")
            rows = block["rows"]
            if not isinstance(rows, list) or not rows:
                raise ValueError("empty evaluation")
            if name == "joint_validation" and (not isinstance(block.get("dependence_method"), str)
                                                 or not block["dependence_method"].strip()):
                raise ValueError("missing ticket dependence method")
            ids = set()
            scores, baseline_scores = [], []
            for row in rows:
                identity, event = row["id"], row["event_id"]
                if (not isinstance(identity, str) or not identity.strip() or identity in ids
                        or not isinstance(event, str) or not event.strip()):
                    raise ValueError("invalid or duplicate evaluation identity")
                ids.add(identity)
                if event in training_events:
                    raise ValueError("training/evaluation event overlap")
                forecast = _evidence_time(row["forecast_at"])
                if not (fitted <= forecast < _evidence_time(row["event_at"])
                        < _evidence_time(row["outcome_available_at"]) <= cutoff):
                    raise ValueError("invalid out-of-sample chronology")
                if not (available <= _evidence_time(row["input_available_at"]) <= forecast):
                    raise ValueError("inputs unavailable at forecast")
                p, b, y = row["probability"], row["baseline_probability"], row["outcome"]
                if not (_valid_probability(p) and _valid_probability(b)
                        and type(y) is int and y in (0, 1)):
                    raise ValueError("invalid evaluation probability/outcome")
                if name == "joint_validation":
                    legs = row["leg_ids"]
                    if (not isinstance(legs, list) or len(legs) < 2
                            or any(not isinstance(leg, str) or not leg.strip() for leg in legs)
                            or len(set(legs)) != len(legs)):
                        raise ValueError("invalid ticket legs")
                scores.append((p, y))
                baseline_scores.append((b, y))
            metrics = block["metrics"]
            if not isinstance(metrics, dict):
                raise ValueError("metrics must be an object")
            verified = {}
            for metric in ("brier", "log_loss"):
                if (metric in metrics) != ("baseline_" + metric in metrics):
                    raise ValueError(f"incomplete {metric} comparison")
                if metric not in metrics:
                    continue

                def score(values):
                    if metric == "brier":
                        return sum((p - y) ** 2 for p, y in values) / len(values)
                    # Exact 0/1 predictions assigning zero mass are infinite loss.
                    likelihoods = [p if y else 1 - p for p, y in values]
                    if 0 in likelihoods:
                        raise ValueError("infinite log loss")
                    return -sum(log(p) for p in likelihoods) / len(values)

                for key, values in ((metric, scores), ("baseline_" + metric, baseline_scores)):
                    actual = score(values)
                    reported = metrics[key]
                    if (not isinstance(reported, Real) or isinstance(reported, bool)
                            or not isfinite(reported) or abs(reported - actual) > 1e-9):
                        raise ValueError(f"{key} does not match rows")
                    verified[key] = actual
            if not verified:
                raise ValueError("missing Brier or log-loss evidence")
            evidence[name] = {"sample_size": len(rows), "baseline": block["baseline"],
                              "metrics": verified}
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
            reasons.append(f"{name}: {exc}")
    evidence["assessment_scope"] = "SUPPLIED_EVIDENCE_CONSISTENCY_ONLY"
    evidence["sample_sufficiency"] = "NOT_ASSESSED"
    evidence["performance_approval"] = "NOT_ASSESSED"
    return {"status": "BLOCKED" if reasons else "PASS", "reasons": reasons, "evidence": evidence}
