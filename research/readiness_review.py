"""Run current personnel/evidence preparation and all seven audits together.

Read-only inputs, new output directory, actual current clock. This command
cannot enable wagering or turn incomplete evidence into a forecast.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from research.accuracy_swarm import run as run_swarm


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def utc_now():
    return datetime.now(timezone.utc)


def run(config_path, database, output, *, game_id, teams, evidence_path=None,
        capture_path=None, inventory_path=None, personnel_sources=None,
        expected_capture_sha256=None):
    config_path = Path(config_path).resolve()
    database = Path(database).resolve()
    output = Path(output).resolve()
    config = read_json(config_path)
    runtime = Path(config["runtime"]).resolve()
    if output.exists():
        raise FileExistsError("new review directory required")
    if output.is_relative_to(runtime) or (capture_path and output.is_relative_to(Path(capture_path).resolve())):
        raise ValueError("review cannot write inside runtime or immutable capture")
    state_path = runtime / "state.json"
    state = read_json(state_path)
    profile_path = Path(state["latest_profiles"])
    inputs = [config_path, state_path, profile_path, database]
    inputs += [Path(path).resolve() for path in (evidence_path, inventory_path, personnel_sources) if path]
    if capture_path:
        inputs.append(Path(capture_path).resolve() / "manifest.json")
    if inventory_path and not capture_path:
        raise ValueError("artifact inventory requires a captured game context")
    inventory = {str(path): file_hash(path) for path in inputs}
    cutoff = utc_now()
    output.mkdir(parents=True, exist_ok=False)
    preparation = {}
    actual_evidence = evidence_path
    if capture_path:
        from research.readiness_evidence import run as assemble
        pack = assemble(capture_path, output / "evidence", inventory_path=inventory_path,
                        evidence_path=evidence_path, as_of=cutoff,
                        expected_capture_sha256=expected_capture_sha256)
        preparation["evidence"] = pack
        actual_evidence = pack["manifest_path"]
    if personnel_sources:
        from research.game_personnel_recovery import build_game_personnel_view
        personnel = build_game_personnel_view(state, read_json(profile_path), read_json(personnel_sources),
                                             game_id=game_id, as_of=cutoff.isoformat())
        if set(personnel["teams"]) != set(teams):
            raise ValueError("personnel view differs from selected game teams")
        path = output / "projected-personnel.json"
        path.write_text(json.dumps(personnel, indent=2, allow_nan=False), encoding="utf-8")
        preparation["personnel"] = {"path": str(path), "sha256": file_hash(path),
                                    "status": personnel["status"],
                                    "game_day_active_proof": personnel["game_day_active_proof"],
                                    "missingness": {t: p["missingness"] for t, p in personnel["teams"].items()}}
    evidence_manifest = read_json(actual_evidence) if actual_evidence else {}
    current_recording = evidence_manifest.get("current_recording")
    if current_recording is not None and not isinstance(current_recording, dict):
        preparation["current_recording"] = {
            "status": "BLOCKED", "reasons": ["current recording manifest must be an object"], "evidence": {}}
    elif current_recording is not None:
        from football_engine.ledger import audit_current_recording_evidence
        preparation["current_recording"] = audit_current_recording_evidence(
            current_recording.get("ledger", {}), cutoff,
            game_id=game_id, ticket_ids=current_recording.get("ticket_ids"))
    else:
        preparation["current_recording"] = {
            "status": "UNAVAILABLE", "reasons": ["no current frozen proposal ledger supplied"],
            "meaning": "Historical settlement validation is audited separately; no proposal is invented."}
    audit = run_swarm(config_path, database, output / "seven-auditor", actual_evidence,
                      cutoff, game_id=game_id, teams=teams)
    changed = [path for path, digest in inventory.items()
               if not Path(path).is_file() or file_hash(path) != digest]
    blockers = {name: result["reasons"] for name, result in audit["auditors"].items() if result["status"] != "PASS"}
    pack_blocked = preparation.get("evidence", {}).get("status") == "BLOCKED"
    recording_invalid = preparation["current_recording"]["status"] == "BLOCKED"
    status = "BLOCKED" if blockers or changed or pack_blocked or recording_invalid else "EVIDENCE_CONSISTENT_RESEARCH_ONLY"
    report = {"schema": "CURRENT_READINESS_REVIEW_V1", "as_of": cutoff.isoformat(),
              "game_id": game_id, "teams": list(teams), "status": status,
              "betting_ready": False, "exit_code": 2,
              "source_inputs": [{"path": path, "sha256": digest} for path, digest in inventory.items()],
              "changed_inputs": changed, "preparation": preparation,
              "audit_report": str(output / "seven-auditor/accuracy-report.json"),
              "audit_report_sha256": file_hash(output / "seven-auditor/accuracy-report.json"),
              "audit_blockers": blockers,
              "performance_approval": "NOT_ESTABLISHED",
              "meaning": "Evidence preparation and checks only; no authority to place bets."}
    (output / "review.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    lines = ["# Current game readiness review", "", f"Result: {status}. Betting readiness is not established.",
             "", f"Game: {game_id}. Teams: {', '.join(teams)}. Actual review time: {cutoff.isoformat()}.", "",
             "| Check | Remaining evidence |", "|---|---|"]
    lines += [f"| {name} | {'; '.join(reasons)} |" for name, reasons in blockers.items()]
    if pack_blocked:
        lines += ["", "Evidence pack remains blocked:"] + ["- " + reason for reason in preparation["evidence"]["reasons"]]
    if changed:
        lines += ["", "Inputs changed during review; results must not be used."]
    lines += ["", "Projected personnel is context only. Historical settlement validation remains separate from current recording integrity.",
              "A clean evidence audit is not proof of predictive skill. No schedules, sources, profiles or wagers were modified.", ""]
    (output / "review.md").write_text("\n".join(lines), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--team-database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--teams", nargs=2, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--personnel-sources", type=Path)
    parser.add_argument("--expected-capture-sha256")
    args = parser.parse_args()
    report = run(args.config, args.team_database, args.output, game_id=args.game_id, teams=args.teams,
                 evidence_path=args.evidence, capture_path=args.capture, inventory_path=args.inventory,
                 personnel_sources=args.personnel_sources, expected_capture_sha256=args.expected_capture_sha256)
    print(json.dumps({key: report[key] for key in ("status", "game_id", "betting_ready", "changed_inputs")}))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
