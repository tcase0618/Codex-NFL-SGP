"""Offline forecast coverage diagnostics, never a pregame certification.

Read existing task artifacts and invoke the unchanged forecast auditors. No
network calls, fitting, source rewrites, timestamp inference or runtime writes.
New output directories are required so previous evidence remains immutable.
"""

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from .accuracy_backtest import audit_backtest_evidence
from .calibration import audit_calibration_evidence


FORECAST_FIELDS = (
    "forecast_at", "input_available_at", "outcome_available_at",
    "model_sha256", "inputs_sha256", "baseline_probability",
)


def aware_time(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return stamp


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def csv_coverage(path, *, forecast=False):
    """Full streamed coverage; column presence never certifies provenance."""
    missing = Counter()
    weeks, markets, statuses = Counter(), Counter(), Counter()
    keys, games = set(), set()
    duplicates = invalid = rows = complete = resolved = 0
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        for row in reader:
            rows += 1
            weeks[row.get("week") or "UNKNOWN"] += 1
            if not forecast:
                continue
            markets[row.get("market") or "UNKNOWN"] += 1
            statuses[row.get("calibration_status") or "UNSPECIFIED"] += 1
            games.add(row.get("game_id") or "UNKNOWN")
            complete += all(row.get(field) for field in FORECAST_FIELDS)
            resolved += row.get("outcome") in ("0", "1")
            for field in FORECAST_FIELDS:
                if not row.get(field):
                    missing[field] += 1
            key = tuple(row.get(k) for k in ("season", "game_id", "team_id", "player_id", "market", "line"))
            duplicates += key in keys
            keys.add(key)
            try:
                p = float(row["probability"])
                if not math.isfinite(p) or not 0 <= p <= 1 or row.get("outcome") not in ("", "0", "1"):
                    invalid += 1
            except (KeyError, ValueError, TypeError, OverflowError):
                invalid += 1
    result = {"rows": rows, "columns": columns, "by_week": dict(weeks),
              "use": "reconstruction/source research only; historical version availability unverified"}
    if forecast:
        result.update({"games": len(games), "by_market": dict(markets),
                       "duplicate_candidate_keys": duplicates, "invalid_probability_or_outcome": invalid,
                       "missing_row_fields": dict(missing), "calibration_statuses": dict(statuses),
                       "rows_with_all_required_fields": complete, "resolved_rows": resolved,
                       "pregame_certified_rows": 0,
                       "blockers": ["CSV lacks inspectable frozen model/training/input/evaluation bindings",
                                    "No independently verified historical source availability or untouched selection period",
                                    "Per-leg forecasts cannot substitute for joint ticket validation"]})
    return result


def audit_bundle(bundle, as_of):
    """Run exact contracts, isolating malformed inputs without relaxing checks."""
    result = {}
    for name, auditor in (("calibration", audit_calibration_evidence), ("backtest", audit_backtest_evidence)):
        try:
            value = bundle.get(name, {}) if isinstance(bundle, dict) else None
            result[name] = auditor(value, as_of)
        except Exception as exc:
            result[name] = {"status": "BLOCKED", "reasons": [f"auditor failed: {type(exc).__name__}"],
                            "evidence": {"check_completed": False}}
    return result


def jsonl_coverage(path):
    """Inspect retrospective row logs without treating outcome fields as inputs."""
    rows, malformed, complete = 0, 0, 0
    missing, fields = Counter(), set()
    with Path(path).open(encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows += 1
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("object required")
                fields.update(value)
                complete += all(value.get(field) not in (None, "") for field in FORECAST_FIELDS)
                for field in FORECAST_FIELDS:
                    if value.get(field) is None or value.get(field) == "":
                        missing[field] += 1
            except (ValueError, TypeError):
                malformed += 1
    return {"rows": rows, "columns": sorted(fields), "malformed_rows": malformed,
            "missing_row_fields": dict(missing), "rows_with_all_required_fields": complete,
            "pregame_certified_rows": 0,
            "use": "retrospective row log; observed outcomes and postgame volumes are not pregame inputs"}


def collector_plan():
    return """# Prospective forecast collection path

No historical model, forecast time, input availability or authenticity is inferred
by this tool. The existing archives support reconstruction, not pregame certification.
This checklist is a collector contract, not a running background collector.

1. Before an untouched future evaluation period, freeze selection rules, market
   definitions, comparison baseline, sample sufficiency and acceptance thresholds.
   Archive exact model bytes, training membership and code revision. Record actual
   fitting completion and each training label's observed availability. A newly
   fitted model must use today's actual time; never assign a historical fit date.
2. Before each decision cutoff and kickoff, archive actual source bytes and
   reference, absolute path, SHA-256, published_at, version_available_at, observed_at.
   Preserve game/team/player/leg IDs, complete active universe and model inputs.
   Current retrieval of an old article proves only current observation. Historical
   version verification needs separate evidence review; do not auto-set it true.
   research.collect_pregame_sources can collect current source leads; its output
   does not supply frozen model forecasts, active clearance or historical versions.
3. Freeze full selections separately, before outcome collection. For every ticket
   preserve frozen_at, kickoff, model_sha256, exact legs and four source purposes:
   forecast (per-leg probabilities), roster (explicit game-specific active IDs),
   quote (combined ticket decimal price or documented unavailable price), and
   model_inputs. Each source JSON must bind ticket_id/game_id/leg_ids/exact legs.
   No bets are needed. Unpriced statistical tests require archived unavailable
   quote evidence and price_available=false, roi_available=false; no ROI claim.
4. After each game, append authoritative final full-ticket settlements separately
   with frozen ticket hash and exact leg IDs. Do not infer ticket settlement from
   individual legs. Retain unresolved/void/push cases and applicable rules.
5. Build nonoverlapping walk-forward folds from real freezes: train_start <=
   train_end <= trained_at < test_start <= test_end; labels known by train_end;
   disjoint training/test game IDs and complete test coverage. Archive model/fold
   metadata and bind every ticket forecast to the actual trained model digest.
6. Supply calibration model/artifact/sha256/fitted_at/training_sha256/frozen_sha256;
   inputs/artifact/sha256/available_at/frozen_sha256; training/rows/sha256.
   Evaluation AND joint_validation need hashed rows, named baseline, ordinary and
   frozen model/input digests, and recomputable paired Brier or log-loss metrics.
   Rows require id/event_id/forecast_at/event_at/input_available_at/
   outcome_available_at/probability/baseline_probability/binary outcome.
   Joint rows require >=2 distinct leg_ids and a real named dependence_method.
   Do not multiply marginal probabilities and call that validated dependence.
   Canonical JSON digests differ from raw archive byte hashes; follow the exact
   contracts in research.calibration and research.accuracy_backtest.
7. Place the real sections under calibration and backtest in a new evidence JSON.
   Recheck offline using the command below, then run all seven existing auditors
   independently. Neither diagnostic output nor auditor consistency PASS proves
   archive authenticity, sample sufficiency, improvement, profitability or readiness.

    python -m research.forecast_gap_diagnostics --workspace <task-workspace> --evidence <absolute-evidence.json> --output <new-folder>

No subscriptions, paid APIs, wagers, runtime changes or automatic promotion are
part of this path. Historical repair is possible only with actual preexisting
versioned artifacts; otherwise the first valid evaluation starts prospectively.
"""


def run(workspace, output, *, evidence_path=None, as_of=None):
    workspace, output = Path(workspace).resolve(), Path(output).resolve()
    cutoff = aware_time(as_of) if as_of else datetime.now(timezone.utc)
    if not workspace.is_dir():
        raise ValueError("workspace directory missing")
    if output.exists():
        raise FileExistsError("new output directory required; previous reports are immutable")
    # Prevent accidental writes to an existing input tree.
    allowed = workspace / "outputs" / "gap-repair-forecast"
    if output != allowed and allowed not in output.parents:
        raise ValueError("output must be within workspace/outputs/gap-repair-forecast")
    inventory, datasets, archives, bundles, saved_gates, errors = [], [], [], [], [], []

    def record(path, purpose):
        inventory.append({"path": str(path.resolve()), "sha256": file_hash(path),
                          "bytes": path.stat().st_size, "purpose": purpose})

    def read_json(path, purpose):
        record(path, purpose)
        return json.loads(path.read_text(encoding="utf-8-sig"))

    candidates = sorted(set((workspace / "outputs").glob("replay-*-predictions.csv")) |
                        set((workspace / "outputs").glob("replay-*-calibrated.csv")) |
                        set((workspace / "work" / "historical-nflverse").glob("*.csv")))
    for path in candidates:
        try:
            record(path, "local dataset")
            datasets.append({"path": str(path), **csv_coverage(path, forecast=path.parent.name == "outputs")})
        except (OSError, ValueError, csv.Error, UnicodeError) as exc:
            errors.append({"path": str(path), "error": str(exc)})

    for path in sorted((workspace / "outputs").glob("replay-*-row-audit.jsonl")):
        try:
            record(path, "retrospective forecast row log")
            datasets.append({"path": str(path), **jsonl_coverage(path)})
        except (OSError, ValueError, UnicodeError) as exc:
            errors.append({"path": str(path), "error": str(exc)})

    # Deliberately bounded discovery: only known evidence/selection manifests,
    # never unrelated repos, runtime config/state, or parallel gap-repair output.
    outputs = workspace / "outputs"
    manifests = sorted(p for pattern in ("*/evidence-manifest.json", "*/selection-manifest.json")
                       for p in outputs.glob(pattern) if not p.parent.name.startswith("gap-repair"))
    if evidence_path:
        manifests.append(Path(evidence_path).resolve())
    for path in dict.fromkeys(manifests):
        try:
            value = read_json(path, "candidate evidence manifest")
            reconstruction = isinstance(value, dict) and "tickets" in value and "backtest" not in value
            bundle = {"backtest": value} if reconstruction else value
            bundles.append({"path": str(path), "reconstruction_candidate": reconstruction,
                            "audits": audit_bundle(bundle, cutoff.isoformat())})
        except (OSError, ValueError, UnicodeError) as exc:
            errors.append({"path": str(path), "error": str(exc)})

    for name in ("strict-pregame-coverage.json", "replay-2026-calibration-summary.json"):
        path = outputs / name
        if not path.exists():
            continue
        try:
            value = read_json(path, "saved research summary; not fresh certification")
            saved_gates.append({"path": str(path), "summary": {k: value[k] for k in (
                "kind", "status", "generated_at", "games", "games_missing_evidence", "method_selection",
                "historically_pregame_certified", "validation_status", "training_2025_resolved") if k in value},
                "reason_counts": dict(Counter(r for row in value.get("records", []) for r in row.get("reasons", []))),
                "warning": "Saved summary only; absent certification flags do not imply verified inputs"})
        except (OSError, ValueError, UnicodeError, TypeError, AttributeError) as exc:
            errors.append({"path": str(path), "error": str(exc)})

    cache = workspace / "work" / "espn-cache"
    files = sorted(cache.glob("*.json"))
    archives.append({"path": str(cache), "json_files": len(files), "content_verified": False,
                     "use": "cached schedules/final boxes are research leads; not frozen forecast/model archives"})
    for name in ("calibration.py", "accuracy_backtest.py", "accuracy_swarm.py", "forward_calibrate.py", "replay_2026.py"):
        record(Path(__file__).with_name(name), "unchanged executable contract/reconstruction implementation")
    record(Path(__file__), "diagnostic implementation")
    # Catch concurrent mutation of every inspected input, not just hash corruption.
    changed = [r["path"] for r in inventory if not Path(r["path"]).is_file() or file_hash(r["path"]) != r["sha256"]]
    complete = [b["path"] for b in bundles if all(a["status"] == "PASS" for a in b["audits"].values())]
    reasons = ["Historical authenticity and untouched evaluation are not established by local hashes",
               "No fitting/replay performed: available data does not establish pregame model/input provenance",
               "Seven-auditor readiness requires independent checks outside this diagnostic scope"]
    if not complete:
        reasons.insert(0, "No discovered evidence bundle passes both unchanged calibration and backtest contracts")
    if errors or changed:
        reasons.append("Input read errors or concurrent input changes require review")
    report = {"kind": "FORECAST_GAP_DIAGNOSTICS_V1", "generated_at": datetime.now(timezone.utc).isoformat(),
              "as_of": cutoff.isoformat(), "status": "BLOCKED", "betting_ready": False,
              "historically_pregame_certified": False, "certified_accuracy": None, "roi": None,
              "walk_forward_executed": False, "blocking_reasons": reasons,
              "discovery_scope": "task outputs replay CSVs/row-audit JSONL; historical-nflverse CSVs; one-level evidence/selection manifests; explicit evidence; other gap-repair outputs excluded",
              "datasets": datasets, "archive_leads": archives, "evidence_bundles": bundles,
              "contract_consistent_bundles": complete, "saved_gates": saved_gates,
              "input_errors": errors, "changed_inputs": changed, "input_inventory": inventory,
              "assessment_limits": "No sample sufficiency, independent improvement, archive authenticity, ROI or seven-role approval is assessed"}
    output.mkdir(parents=True, exist_ok=False)
    (output / "diagnostics.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    (output / "collector-path.md").write_text(collector_plan(), encoding="utf-8")
    lines = ["# Forecast gap diagnostics", "", "Status: BLOCKED. No certified predictive accuracy or betting readiness.", ""]
    lines += [f"- {r}" for r in reasons]
    lines += ["", "## Local data coverage", "", "| File | Rows | Games | Missing provenance fields |", "|---|---:|---:|---|"]
    for dataset in datasets:
        lines.append(f"| {Path(dataset['path']).name} | {dataset['rows']} | {dataset.get('games', 'N/A')} | {dataset.get('missing_row_fields', 'historical version unavailable')} |")
    lines += ["", "All data counts are coverage diagnostics, not validated forecast results.", "", "## Exact auditor results", ""]
    for bundle in bundles:
        lines.append(f"- {bundle['path']}")
        for name, audit in bundle["audits"].items():
            lines.append(f"  - {name}: {audit['status']}; {'; '.join(audit['reasons'])}")
    lines += ["", "See diagnostics.json for hashes, missingness, errors, saved gate summaries and discovery limits.",
              "See collector-path.md for the precise prospective acquisition and recheck path.",
              "No source files, runtime state, engine behavior or seven-auditor contracts were modified.", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--as-of")
    args = parser.parse_args()
    report = run(args.workspace, args.output, evidence_path=args.evidence, as_of=args.as_of)
    print(json.dumps({k: report[k] for k in ("status", "walk_forward_executed", "betting_ready", "blocking_reasons")}))
    return 2 if report["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
