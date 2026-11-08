"""Offline capture contracts; fixture prices/probabilities are never generated."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest
import requests

from research.pregame_capture import capture, main, next_game, verify


NOW = datetime(2026, 11, 4, 16, tzinfo=timezone.utc)
KICKOFF = "2026-11-06T01:15:00+00:00"


def event():
    competitors = [{"homeAway": side, "team": {"id": identity, "displayName": name}}
                   for side, identity, name in (("away", "30", "Jacksonville Jaguars"),
                                                ("home", "33", "Baltimore Ravens"))]
    return {"id": "401873037", "name": "Jacksonville Jaguars at Baltimore Ravens", "date": KICKOFF,
            "status": {"type": {"state": "pre"}},
            "competitions": [{"id": "401873037", "date": KICKOFF, "competitors": competitors,
                              "status": {"type": {"state": "pre"}}}]}


def schedule():
    return {"leagues": [{"abbreviation": "NFL"}], "events": [event()]}


def context():
    return {"header": {"id": event()["id"], "competitions": event()["competitions"]}}


def fetch(url, **kwargs):
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["Accept-Encoding"] == "identity"
    payload = context() if "/summary?" in url else schedule()
    return SimpleNamespace(content=json.dumps(payload).encode(), url=url, status_code=200,
                           headers={"Content-Type": "application/json", "Date": "source header, not capture clock"})


class Clock:
    def __init__(self):
        self.values = []

    def __call__(self):
        value = NOW + timedelta(seconds=len(self.values))
        self.values.append(value.isoformat())
        return value


def load(bundle):
    return json.loads((bundle / "manifest.json").read_bytes())


def mutate(path, raw):
    path.chmod(stat.S_IREAD | stat.S_IWRITE)
    path.write_bytes(raw)


def test_exact_clock_and_raw_bytes_inventory(tmp_path):
    clock = Clock()
    bundle = tmp_path / "capture"
    result = capture(bundle, clock=clock, get=fetch)
    manifest = load(bundle)
    assert manifest["capture_started_at"] == clock.values[0]
    assert manifest["captured_at"] == clock.values[-1] == result["captured_at"]
    assert manifest["sources"][0]["request_started_at"] == clock.values[1]
    assert manifest["sources"][0]["retrieved_at"] == clock.values[2]
    assert manifest["sources"][1]["request_started_at"] == clock.values[3]
    assert manifest["sources"][1]["retrieved_at"] == clock.values[4]
    assert len(clock.values) == 6
    assert manifest["captured_before_decision_cutoff"] is True
    for record in manifest["sources"]:
        assert (bundle / record["path"]).read_bytes() == fetch(record["requested_url"], allow_redirects=False,
                                                               headers={"Accept-Encoding": "identity"}).content
    files = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
    assert files == {i["path"] for i in manifest["inventory"]} | {"manifest.json", "manifest.sha256"}
    assert verify(bundle, result["manifest_sha256"])["status"] == "PASS"


def test_missing_evidence_is_explicit_and_never_ticket_ready(tmp_path):
    bundle = tmp_path / "capture"
    capture(bundle, clock=lambda: NOW, get=fetch)
    m = load(bundle)
    assert set(m["missing_evidence"]) == {"offered_quote", "forecast", "model", "model_inputs", "starter_proof", "active_proof"}
    assert m["ticket_ready"] is m["starter_verified"] is m["active_universe_verified"] is False
    assert m["offered_quote_verified"] is m["forecast_provenance_verified"] is False
    assert m["status"] == "RESEARCH_ONLY_BLOCKED"
    assert "probability" not in m and "model_version" not in m and "settlement" not in m


def test_capture_is_exclusive_and_does_not_refetch_or_overwrite(tmp_path):
    bundle = tmp_path / "capture"
    capture(bundle, clock=lambda: NOW, get=fetch)
    original = {p: p.read_bytes() for p in bundle.rglob("*") if p.is_file()}
    with pytest.raises(FileExistsError):
        capture(bundle, clock=lambda: pytest.fail("clock should not be read"),
                get=lambda *a, **kw: pytest.fail("should not refetch"))
    assert original == {p: p.read_bytes() for p in original}


@pytest.mark.parametrize("operation", ["alter", "remove", "extra", "manifest", "seal"])
def test_verifier_detects_tamper_and_full_inventory(tmp_path, operation):
    bundle = tmp_path / "capture"
    result = capture(bundle, clock=lambda: NOW, get=fetch)
    source = bundle / load(bundle)["inventory"][0]["path"]
    if operation == "alter":
        mutate(source, b"tampered")
    elif operation == "remove":
        source.chmod(stat.S_IREAD | stat.S_IWRITE)
        source.unlink()
    elif operation == "extra":
        (bundle / "untracked.txt").write_bytes(b"extra")
    elif operation == "manifest":
        mutate(bundle / "manifest.json", (bundle / "manifest.json").read_bytes() + b" ")
    else:
        mutate(bundle / "manifest.sha256", b"0" * 64)
    assert verify(bundle, result["manifest_sha256"])["status"] == "FAIL"


def test_external_digest_rejects_resealed_replacement(tmp_path):
    bundle = tmp_path / "capture"
    result = capture(bundle, clock=lambda: NOW, get=fetch)
    raw = (bundle / "manifest.json").read_bytes() + b" "
    mutate(bundle / "manifest.json", raw)
    mutate(bundle / "manifest.sha256", hashlib.sha256(raw).hexdigest().encode())
    assert verify(bundle)["status"] == "PASS"  # Self-seal alone cannot prove authenticity.
    assert verify(bundle, result["manifest_sha256"])["status"] == "FAIL"


@pytest.mark.parametrize("now", [datetime(2026, 11, 6, 1, tzinfo=timezone.utc),
                                 datetime(2026, 11, 6, 2, tzinfo=timezone.utc)])
def test_late_or_past_game_never_gets_pregame_clearance(tmp_path, now):
    bundle = tmp_path / "capture"
    capture(bundle, clock=lambda: now, get=fetch)
    m = load(bundle)
    assert m["captured_before_decision_cutoff"] is False
    assert m["ticket_ready"] is False
    if now.hour == 2:
        assert m["game"] is None and m["captured_before_kickoff"] is False


def test_clock_crossing_kickoff_during_network_fetch_blocks(tmp_path):
    stamps = iter([NOW, NOW, NOW, NOW, NOW, datetime(2026, 11, 6, 2, tzinfo=timezone.utc)])
    bundle = tmp_path / "capture"
    capture(bundle, clock=lambda: next(stamps), get=fetch)
    m = load(bundle)
    assert not m["captured_before_kickoff"] and not m["captured_before_decision_cutoff"]


@pytest.mark.parametrize("bad_clock", [datetime(2026, 11, 4), NOW - timedelta(seconds=1)])
def test_invalid_or_backwards_clock_cannot_complete_bundle(tmp_path, bad_clock):
    stamps = iter([NOW, bad_clock])
    bundle = tmp_path / "capture"
    with pytest.raises(ValueError, match="clock"):
        capture(bundle, clock=lambda: next(stamps), get=fetch)
    assert verify(bundle)["status"] == "FAIL"
    assert not (bundle / "manifest.sha256").exists()


@pytest.mark.parametrize("fault", ["http_error", "transport", "bad_json", "wrong_league", "redirect"])
def test_failed_sources_are_archived_and_not_verified_schedule(tmp_path, fault):
    def failing(url, **kwargs):
        if fault == "transport":
            raise requests.Timeout("offline fixture timeout")
        response = fetch(url, **kwargs)
        if fault == "http_error":
            response.status_code = 503
        elif fault == "redirect":
            response.status_code = 302
            response.headers["Location"] = "https://untrusted.example"
        elif fault == "bad_json":
            response.content = b"not JSON"
        else:
            response.content = b'{"leagues": [{"abbreviation": "NCAA"}], "events": []}'
        return response
    bundle = tmp_path / "capture"
    capture(bundle, clock=lambda: NOW, get=failing)
    m = load(bundle)
    assert m["game"] is None and not m["next_game_selection_complete"]
    assert len(m["sources"]) == 9 and m["blockers"]
    assert verify(bundle)["status"] == "PASS"  # Error evidence is intact, not usable schedule.
    assert all("path" in s for s in m["sources"]) == (fault != "transport")


@pytest.mark.parametrize("fault", ["id", "kickoff", "teams", "state"])
def test_current_context_must_match_game(tmp_path, fault):
    def changed(url, **kwargs):
        response = fetch(url, **kwargs)
        if "/summary?" in url:
            data = context()
            c = data["header"]["competitions"][0]
            if fault == "id":
                data["header"]["id"] = "different"
            elif fault == "kickoff":
                c["date"] = "2026-11-07T01:15Z"
            elif fault == "teams":
                c["competitors"][0]["team"]["id"] = "999"
            else:
                c["status"]["type"]["state"] = "post"
            response.content = json.dumps(data).encode()
        return response
    bundle = tmp_path / "capture"
    capture(bundle, clock=lambda: NOW, get=changed)
    m = load(bundle)
    assert "context_source_path" not in m["game"]
    assert any("current context" in b for b in m["blockers"])


def test_next_game_selection_is_chronological_and_ignores_finished():
    data = schedule()
    later = deepcopy(event())
    later["date"] = "2026-11-09T01:00Z"
    later["id"] = later["competitions"][0]["id"] = "401873038"
    final = deepcopy(event())
    final["status"]["type"]["state"] = "post"
    data["events"] = [later, final, event()]
    assert next_game(data, NOW)["game_id"] == "401873037"


def test_caller_artifacts_are_frozen_exactly_without_inferred_versions(tmp_path):
    raw = b"caller-owned model bytes\x00\xff"
    (tmp_path / "model.bin").write_bytes(raw)
    item = {"role": "model", "path": "model.bin", "game_id": "401873037", "reference": "caller:actual-model",
            "sha256": hashlib.sha256(raw).hexdigest(), "published_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(), "available_at": NOW.isoformat()}
    specification = tmp_path / "artifacts.json"
    specification.write_text(json.dumps([item]))
    bundle = tmp_path / "capture"
    capture(bundle, artifacts=specification, clock=Clock(), get=fetch)
    m = load(bundle)
    r = m["caller_artifacts"][0]
    assert (bundle / r["path"]).read_bytes() == raw
    assert (bundle / "caller/artifacts.json").read_bytes() == specification.read_bytes()
    assert r["caller_metadata"] == item and "model_version" not in r
    assert r["frozen_at"] > item["available_at"]
    assert "model" not in m["missing_evidence"]
    assert r["validation_status"] == "CALLER_SUPPLIED_UNVERIFIED"
    assert m["ticket_ready"] is False and r["blockers"]
    (tmp_path / "model.bin").write_bytes(b"caller modified original")
    assert verify(bundle)["status"] == "PASS"


@pytest.mark.parametrize("fault", ["hash", "game", "future", "missing_file", "postgame"])
def test_invalid_caller_evidence_cannot_be_upgraded(tmp_path, fault):
    raw = b"original supplied evidence"
    (tmp_path / "evidence.bin").write_bytes(raw)
    item = {"role": "active_proof", "path": "evidence.bin", "game_id": "401873037", "reference": "caller:proof",
            "sha256": hashlib.sha256(raw).hexdigest(), "published_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(), "available_at": NOW.isoformat()}
    if fault == "hash":
        item["sha256"] = "0" * 64
    elif fault == "game":
        item["game_id"] = "wrong"
    elif fault == "future":
        item["available_at"] = "2026-11-10T00:00Z"
    elif fault == "missing_file":
        item["path"] = "absent.bin"
    else:
        item["uses_postgame_participation"] = True
    specification = tmp_path / "artifacts.json"
    specification.write_text(json.dumps([item]))
    bundle = tmp_path / "capture"
    capture(bundle, artifacts=specification, clock=lambda: NOW, get=fetch)
    m = load(bundle)
    assert m["caller_artifacts"][0]["blockers"]
    if fault != "missing_file":
        assert len(m["caller_artifacts"][0]["blockers"]) >= 2
    assert not m["active_universe_verified"] and not m["ticket_ready"]
    if fault == "missing_file":
        assert "active_proof" in m["missing_evidence"]


def test_cli_has_no_as_of_override(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["pregame_capture", "capture", "--output", str(tmp_path / "bundle"),
                                    "--as-of", NOW.isoformat()])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert not (tmp_path / "bundle").exists()


def test_empty_days_are_archived_before_next_game_selection(tmp_path):
    calls = []

    def daily(url, **kwargs):
        calls.append(url)
        response = fetch(url, **kwargs)
        if "dates=20261103" in url or "dates=20261104" in url:
            response.content = b'{"leagues": [{"abbreviation": "NFL"}], "events": []}'
        return response
    bundle = tmp_path / "capture"
    capture(bundle, clock=lambda: NOW, get=daily)
    m = load(bundle)
    assert len(calls) == 4
    assert m["next_game_selection_complete"]
    assert len(m["sources"]) == 4
    assert "dates=20261105" in m["sources"][2]["requested_url"]


def test_failed_day_blocks_next_claim_even_when_later_game_found(tmp_path):
    def daily(url, **kwargs):
        response = fetch(url, **kwargs)
        if "dates=20261103" in url:
            response.status_code = 503
        return response
    bundle = tmp_path / "capture"
    capture(bundle, clock=lambda: NOW, get=daily)
    m = load(bundle)
    assert m["game"] and not m["next_game_selection_complete"]
    assert any("schedule" in b for b in m["blockers"])


def test_all_supplied_roles_are_preserved_without_ticket_clearance(tmp_path):
    roles = ["offered_quote", "forecast", "model", "model_inputs", "starter_proof", "active_proof"]
    items = []
    for role in roles:
        # Opaque fixture bytes test preservation; they claim no authentic price/model/proof.
        raw = f"unverified caller fixture {role}".encode()
        (tmp_path / role).write_bytes(raw)
        items.append({"role": role, "path": role, "game_id": "401873037", "reference": "fixture:caller",
                      "sha256": hashlib.sha256(raw).hexdigest(), "published_at": NOW.isoformat(),
                      "available_at": NOW.isoformat(), "observed_at": NOW.isoformat()})
    specification = tmp_path / "artifacts.json"
    specification.write_text(json.dumps(items))
    bundle = tmp_path / "capture"
    capture(bundle, artifacts=specification, clock=lambda: NOW, get=fetch)
    m = load(bundle)
    assert m["missing_evidence"] == []
    assert len(m["caller_artifacts"]) == 6
    assert not m["ticket_ready"]
    assert all(r["validation_status"] == "CALLER_SUPPLIED_UNVERIFIED" for r in m["caller_artifacts"])
    assert verify(bundle)["status"] == "PASS"
