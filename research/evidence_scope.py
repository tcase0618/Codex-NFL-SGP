"""Bind successful evidence checks to one game; never grant betting authority."""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path

from football_engine.team_profiles import content_hash


def _current_markets(manifest):
    record = manifest.get("opportunity_snapshot", {})
    raw = Path(record["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != record["sha256"]:
        raise ValueError("current opportunity archive hash mismatch")
    legs = json.loads(raw)["legs"]
    if not isinstance(legs, list) or not legs:
        raise ValueError("current opportunity legs unavailable")
    markets = {leg["market"] for leg in legs}
    if any(not isinstance(market, str) or not market for market in markets):
        raise ValueError("current opportunity markets malformed")
    return markets


def bind_game_evidence(report, role, scope, manifest):
    """A local PASS cannot combine a different game, model, or validation cohort.

    Historical cohorts keep their historical game IDs. Their exact identities
    must instead be recorded in the selected model's hash-bound artifact under
    validation_evidence. Those links prove applicability, not authenticity or
    predictive skill. The original evidence checks must all still pass.
    """
    if scope.get("audit_mode") != "game" or report.get("status") != "PASS":
        return report
    bound = deepcopy(report)
    reasons = []
    try:
        evidence = bound["evidence"]
        model_hash = scope.get("model_sha256")
        if (not isinstance(model_hash, str) or len(model_hash) != 64 or
                any(c not in "0123456789abcdef" for c in model_hash)):
            raise ValueError("selected model artifact hash required")
        if role == "remaining":
            for field in ("game_id", "team_profile_versions", "model_sha256"):
                if evidence.get(field) != scope.get(field) or not scope.get(field):
                    reasons.append(f"opportunity {field} does not match selected game scope")
            actual = datetime.fromisoformat(evidence["kickoff"].replace("Z", "+00:00"))
            expected = datetime.fromisoformat(scope["kickoff"].replace("Z", "+00:00"))
            if actual.utcoffset() is None or expected.utcoffset() is None or actual != expected:
                reasons.append("opportunity kickoff does not match selected game scope")
        elif role == "correlation":
            if evidence.get("model_sha256") != model_hash:
                reasons.append("calibration model does not match selected model")
        elif role in {"scenario", "ledger"}:
            model = manifest["calibration"]["model"]
            artifact = model["artifact"]
            if content_hash(artifact) != model_hash or model.get("sha256") != model_hash:
                raise ValueError("historical evidence is not bound to selected calibration model")
            validation = artifact["validation_evidence"]
            supported = validation["markets"]
            if (not isinstance(supported, list) or not supported or
                    any(not isinstance(market, str) or not market for market in supported) or
                    len(supported) != len(set(supported)) or
                    not _current_markets(manifest) <= set(supported)):
                raise ValueError("historical validation does not cover current markets")
            if role == "scenario":
                backtest = manifest["backtest"]
                if validation.get("backtest_sha256") != content_hash(backtest):
                    raise ValueError("selected model backtest manifest binding mismatch")
                cohort_markets = {leg["market"] for ticket in backtest["tickets"] for leg in ticket["legs"]}
                if not _current_markets(manifest) <= cohort_markets:
                    raise ValueError("backtest cohort does not cover current markets")
            elif validation.get("ledger_sha256") != evidence.get("sha256") or not evidence.get("sha256"):
                raise ValueError("selected model historical ledger binding mismatch")
            elif not _current_markets(manifest) <= set(evidence.get("markets", [])):
                raise ValueError("historical ledger cohort does not cover current markets")
        else:
            return report
        evidence["game_scope_binding"] = {
            "game_id": scope["game_id"], "model_sha256": model_hash,
            "authority": "INTERNAL_EVIDENCE_CONSISTENCY_ONLY",
        }
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
        reasons.append(f"game scope binding: {exc}")
    if reasons:
        bound["status"] = "BLOCKED"
        bound["reasons"] = list(dict.fromkeys(bound.get("reasons", []) + reasons))
    return bound
