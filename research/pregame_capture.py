"""Append-only, clocked public evidence capture. No forecasts or ticket authority."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from urllib.parse import urlparse

import requests

from football_engine.accuracy import evidence_fingerprint
from .pregame_validation import parse_time, validate_evidence

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
ROLES = ("offered_quote", "forecast", "model", "model_inputs", "starter_proof", "active_proof")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def utc_now():
    return datetime.now(timezone.utc)


def next_game(data, after):
    """Only explicit scheduled NFL events; no fallback to cached or finished games."""
    if not any(league.get("abbreviation") == "NFL" for league in data.get("leagues", [])):
        raise ValueError("schedule does not identify NFL")
    candidates = []
    for event in data.get("events", []):
        if event.get("status", {}).get("type", {}).get("state") != "pre":
            continue
        kickoff = parse_time(event["date"])
        if kickoff <= after:
            continue
        competition = event["competitions"][0]
        teams = {c["homeAway"]: {"id": str(c["team"]["id"]), "name": c["team"]["displayName"]}
                 for c in competition["competitors"]}
        if set(teams) != {"home", "away"} or teams["home"]["id"] == teams["away"]["id"]:
            raise ValueError("invalid scheduled teams")
        game_id = str(event["id"])
        if not game_id.isdecimal() or str(competition["id"]) != game_id:
            raise ValueError("invalid scheduled event identity")
        candidates.append({"game_id": game_id, "name": event["name"], "kickoff": kickoff.isoformat(),
                           "teams": teams, "source_state": "pre"})
    return min(candidates, key=lambda g: (parse_time(g["kickoff"]), g["game_id"])) if candidates else None


class Archive:
    def __init__(self, output, clock):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.clock = clock
        self.last_time = None
        self.inventory = []

    def now(self):
        current = self.clock()
        if not isinstance(current, datetime) or current.utcoffset() is None:
            raise ValueError("capture clock must be timezone aware")
        current = current.astimezone(timezone.utc)
        if self.last_time is not None and current < self.last_time:
            raise ValueError("capture clock moved backwards; incomplete bundle must not be used")
        self.last_time = current
        return current.isoformat()

    def write(self, name, raw, purpose):
        path = self.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        self.inventory.append({"path": name, "bytes": len(raw), "sha256": sha(raw), "purpose": purpose})
        return name

    def fetch(self, url, role, get):
        record = {"role": role, "requested_url": url, "request_started_at": self.now()}
        try:
            # Fixed ESPN endpoints only; no credentials, paid APIs or redirects.
            response = get(url, timeout=(6, 20), allow_redirects=False,
                           headers={"User-Agent": "CaseCapitalResearch/0.1", "Accept-Encoding": "identity"})
            raw = response.content
            record.update({"retrieved_at": self.now(), "resolved_url": response.url,
                           "http_status": response.status_code, "response_headers": dict(response.headers),
                           "sha256": sha(raw), "bytes": len(raw)})
            record["path"] = self.write(f"sources/{len(self.inventory):03d}.body", raw, role)
            record["transport_verified"] = (response.status_code == 200 and response.url == url
                                              and urlparse(response.url).scheme == "https")
            if not record["transport_verified"]:
                record["error"] = "HTTP status or resolved URL failed strict source check"
        except requests.RequestException as exc:
            record.update(retrieved_at=self.now(), transport_verified=False,
                          error=f"{type(exc).__name__}: {exc}")
        self.write(f"sources/{len(self.inventory):03d}.json", encoded(record), "HTTP retrieval metadata")
        return record


def freeze_artifacts(archive, specification, game, cutoff):
    """Archive exact caller bytes. Source assertions are checked, never certified."""
    records, blockers = [], []
    if specification is None:
        return records, blockers
    path = Path(specification).resolve()
    raw = path.read_bytes()
    archive.write("caller/artifacts.json", raw, "caller artifact inventory as supplied")
    spec = json.loads(raw)
    if not isinstance(spec, list):
        raise ValueError("artifact inventory must be a JSON list")
    for index, item in enumerate(spec):
        if not isinstance(item, dict) or item.get("role") not in (*ROLES, "context"):
            raise ValueError("artifact inventory entry requires a supported role")
        role = item["role"]
        source_path = Path(item["path"])
        if not source_path.is_absolute():
            source_path = path.parent / source_path
        record = {"role": role, "caller_metadata": item, "read_started_at": archive.now(),
                  "validation_status": "CALLER_SUPPLIED_UNVERIFIED", "blockers": []}
        try:
            artifact_raw = source_path.read_bytes()
            record.update(path=archive.write(f"caller/{index:03d}.body", artifact_raw, role),
                          sha256=sha(artifact_raw), bytes=len(artifact_raw), frozen_at=archive.now())
            if item.get("sha256") != record["sha256"]:
                record["blockers"].append("missing or mismatched caller SHA256")
            if not game or item.get("game_id") != game["game_id"]:
                record["blockers"].append("caller artifact game mismatch")
            provenance = {**item, "purpose": role, "content_sha256": record["sha256"],
                          "archive_reference": record["path"], "version_available_at": record["frozen_at"]}
            record["blockers"].extend(validate_evidence(provenance, cutoff, role))
            for key in ("published_at", "observed_at", "available_at"):
                try:
                    if parse_time(item[key]) > parse_time(record["frozen_at"]):
                        record["blockers"].append(f"future caller {key}")
                except (KeyError, ValueError, TypeError, AttributeError):
                    record["blockers"].append(f"missing or invalid caller {key}")
            # Hashes bind bytes, not authenticity, starter status or model fitness.
            record["blockers"].append("source authenticity and role-specific semantics require independent validation")
        except OSError as exc:
            record.update(frozen_at=archive.now(), error=str(exc))
            record["blockers"].append("caller artifact unreadable")
        blockers.extend(f"{role}: {reason}" for reason in record["blockers"])
        records.append(record)
    return records, blockers


def capture(output, *, artifacts=None, clock=utc_now, get=requests.get):
    archive = Archive(output, clock)
    started = archive.now()
    sources, blockers, game = [], [], None
    # Daily endpoint is supported by ESPN; a failed day prevents a 'next' claim.
    first_day = parse_time(started).date() - timedelta(days=1)
    schedule_complete = True
    for offset in range(9):
        day = first_day + timedelta(days=offset)
        record = archive.fetch(f"{BASE}/scoreboard?dates={day:%Y%m%d}", "schedule", get)
        sources.append(record)
        try:
            if not record["transport_verified"]:
                raise ValueError(record["error"])
            game = next_game(json.loads((archive.output / record["path"]).read_bytes()),
                             parse_time(record["retrieved_at"]))
            if game:
                game["schedule_source_path"] = record["path"]
                break
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            schedule_complete = False
            blockers.append(f"schedule {day}: {exc}")
    if not game:
        blockers.append("no verified future scheduled NFL game found in bounded eight-day window")
    else:
        record = archive.fetch(f"{BASE}/summary?event={game['game_id']}", "current_context", get)
        sources.append(record)
        try:
            if not record["transport_verified"]:
                raise ValueError(record["error"])
            header = json.loads((archive.output / record["path"]).read_bytes())["header"]
            competition = header["competitions"][0]
            teams = {c["homeAway"]: str(c["team"]["id"]) for c in competition["competitors"]}
            if (str(header["id"]) != game["game_id"] or str(competition["id"]) != game["game_id"]
                    or parse_time(competition["date"]) != parse_time(game["kickoff"])
                    or teams != {side: value["id"] for side, value in game["teams"].items()}
                    or competition["status"]["type"]["state"] != "pre"):
                raise ValueError("context identity, kickoff, teams or scheduled state mismatch")
            game["context_source_path"] = record["path"]
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            blockers.append(f"current context: {exc}")
    cutoff = parse_time(game["kickoff"]) - timedelta(minutes=30) if game else parse_time(started)
    caller, caller_blockers = freeze_artifacts(archive, artifacts, game, cutoff)
    blockers.extend(caller_blockers)
    captured = archive.now()
    before_kickoff = bool(game and parse_time(captured) < parse_time(game["kickoff"]))
    before_cutoff = bool(game and parse_time(captured) <= cutoff)
    if not before_cutoff:
        blockers.append("capture finished after decision cutoff or game unavailable")
    missing = [role for role in ROLES if not any(r["role"] == role and r.get("path") for r in caller)]
    blockers.extend(f"missing {role}" for role in missing)
    blockers.append("research capture does not confer ticket or execution clearance")
    manifest = {"schema": "PREGAME_CAPTURE_V1", "capture_started_at": started,
                "captured_at": captured, "clock_basis": "system UTC at retrieval and freeze; no caller override in CLI",
                "game": game, "next_game_selection_complete": bool(game and schedule_complete),
                "decision_cutoff": cutoff.isoformat() if game else None,
                "captured_before_kickoff": before_kickoff, "captured_before_decision_cutoff": before_cutoff,
                "sources": sources, "caller_artifacts": caller, "missing_evidence": missing,
                "status": "RESEARCH_ONLY_BLOCKED", "ticket_ready": False,
                "starter_verified": False, "active_universe_verified": False,
                "offered_quote_verified": False, "forecast_provenance_verified": False,
                "blockers": blockers, "inventory": archive.inventory.copy()}
    # Reuse canonical fingerprint without inventing an engine snapshot or model.
    manifest["evidence_fingerprint"] = evidence_fingerprint(manifest)
    raw = encoded(manifest)
    archive.write("manifest.json", raw, "bundle manifest")
    archive.write("manifest.sha256", (sha(raw) + "\n").encode(), "manifest integrity seal")
    for path in archive.output.rglob("*"):
        if path.is_file():
            path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    return {"bundle": str(archive.output), "manifest_sha256": sha(raw),
            "game": game, "captured_at": captured, "missing_evidence": missing,
            "ticket_ready": False, "status": manifest["status"]}


def verify(output, expected_manifest_sha256=None):
    """Check exact closed inventory; external expected digest anchors the seal."""
    root = Path(output).resolve()
    errors = []
    try:
        paths = []
        # Inspect each directory before descent, including Windows junctions on 3.11.
        for directory, directories, files in os.walk(root, followlinks=False):
            for name in directories + files:
                path = Path(directory) / name
                attributes = getattr(path.lstat(), "st_file_attributes", 0)
                if path.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                    raise ValueError("links forbidden in frozen bundle")
                paths.append(path)
        raw = (root / "manifest.json").read_bytes()
        digest = sha(raw)
        if (root / "manifest.sha256").read_text().strip() != digest:
            errors.append("manifest seal mismatch")
        if expected_manifest_sha256 is not None and expected_manifest_sha256 != digest:
            errors.append("external manifest digest mismatch")
        manifest = json.loads(raw)
        if manifest.get("schema") != "PREGAME_CAPTURE_V1":
            errors.append("unknown manifest schema")
        fingerprint = {k: v for k, v in manifest.items() if k != "evidence_fingerprint"}
        if manifest.get("evidence_fingerprint") != evidence_fingerprint(fingerprint):
            errors.append("manifest fingerprint mismatch")
        names = [item["path"] for item in manifest["inventory"]]
        if len(names) != len(set(names)):
            errors.append("duplicate inventory entries")
        expected = set(names) | {"manifest.json", "manifest.sha256"}
        actual = {p.relative_to(root).as_posix() for p in paths if p.is_file()}
        if expected != actual:
            errors.append("file inventory mismatch")
        for item in manifest["inventory"]:
            relative = Path(item["path"])
            if relative.is_absolute() or ".." in relative.parts or "\\" in item["path"]:
                raise ValueError("unsafe inventory path")
            payload = (root / relative).read_bytes()
            if len(payload) != item["bytes"] or sha(payload) != item["sha256"]:
                errors.append(f"content mismatch: {item['path']}")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append(f"invalid or incomplete bundle: {exc}")
    return {"status": "FAIL" if errors else "PASS", "errors": errors, "ticket_ready": False,
            "external_digest_checked": expected_manifest_sha256 is not None,
            "meaning": "byte integrity only; no authenticity, predictive performance or wagering approval"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("capture")
    collect.add_argument("--output", type=Path, required=True, help="new immutable directory; never overwritten")
    collect.add_argument("--artifacts", type=Path, help="optional caller JSON inventory; relative paths resolve beside it")
    check = commands.add_parser("verify")
    check.add_argument("--bundle", type=Path, required=True)
    check.add_argument("--expected-manifest-sha256")
    args = parser.parse_args()
    try:
        if args.command == "capture":
            result = capture(args.output, artifacts=args.artifacts)
        else:
            result = verify(args.bundle, args.expected_manifest_sha256)
        print(json.dumps(result, indent=2))
        return 1 if result["status"] == "FAIL" else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Capture failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
