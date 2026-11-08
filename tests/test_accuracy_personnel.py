"""Offline metadata fixtures, not real personnel or eligibility assertions."""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from football_engine.accuracy_personnel import audit_personnel
from research.team_candidates import team_candidates


NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


def source(kind, observed=NOW):
    return dict(kind=kind, reference="fixture:" + kind, retrieved_at=observed.isoformat(),
                content_sha256="fixture-hash", status="parsed")


def profile():
    player = dict(player="Fixture Player", team_id="CHI", position="QB", roster_sections=["Active"],
                  player_reference="fixture:player", depth_roles=[dict(position="QB", depth=1, alignment_row="0:1")],
                  review_flags=[], eligibility="REQUIRES_GAME_DAY_CONFIRMATION",
                  provider_identities={"gsis_id": "fixture-001", "espn_id": "00123"})
    player["identity_review"] = dict(team_id="CHI", player_reference=player["player_reference"], position="QB",
        reviewed_at=NOW.isoformat(), matched_biographical_fields=["college", "height", "birth_date"],
        provider_identities=copy.deepcopy(player["provider_identities"]),
        sources=[dict(reference="fixture:" + label, observed_at=NOW.isoformat(), sha256="fixture-hash")
                 for label in ("bio", "crosswalk")])
    return dict(team_id="CHI", as_of=NOW.isoformat(), personnel=dict(
        status="CURRENT_SOURCE_CONTEXT_NOT_GAME_DAY_ELIGIBILITY", players=[player],
        source_records=[source(kind) for kind in ("roster", "depth", "injury")],
        injury_report_rows=[{"columns": {"Player": "Fixture Player", "Injury": "Fixture only"}}],
        injury_coverage=dict(status="parsed", report_scope=dict(phase="REG", week=9))))


def test_reviewed_identity_and_active_membership_never_make_automatic_profile_ready():
    p = profile()
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert set(result["reasons"]) == {"PERSONNEL_COMPLETENESS_UNVERIFIED",
        "GAME_SPECIFIC_ACTIVE_PROOF_UNAVAILABLE", "INJURY_TARGET_GAME_SCOPE_UNVERIFIED",
        "TRANSACTION_RECONCILIATION_UNVERIFIED"}
    player = result["evidence"]["players"][0]
    assert player["membership"] == "ROSTER_ACTIVE_CONTEXT_ONLY"
    assert player["identity_metadata_verified"] is True
    assert player["game_specific_active_verified"] is False
    assert result["evidence"]["archive_content_reverified"] is False


def test_actual_automatic_candidate_shape_needs_provider_mapping_not_name_or_url():
    records = [dict(source(kind), team="CHI", rows=[dict(player="Fixture Player", position="QB",
        section="Active", player_reference="fixture:player", depth=1, alignment_row="0:1")])
        for kind in ("roster", "depth")]
    p = profile()
    p["personnel"]["players"] = team_candidates(records, "CHI")
    result = audit_personnel(p, NOW)
    assert "STABLE_PROVIDER_ID_MISSING" in result["reasons"]
    assert result["evidence"]["players"][0]["membership"] == "ROSTER_ACTIVE_CONTEXT_ONLY"


@pytest.mark.parametrize("kind", ["roster", "depth", "injury"])
def test_source_age_boundary_and_future_cutoff(kind):
    p = profile()
    s = next(s for s in p["personnel"]["source_records"] if s["kind"] == kind)
    s["retrieved_at"] = (NOW - timedelta(hours=24)).isoformat()
    assert kind.upper() + "_SOURCE_STALE" not in audit_personnel(p, NOW)["reasons"]
    s["retrieved_at"] = (NOW - timedelta(hours=24, microseconds=1)).isoformat()
    assert kind.upper() + "_SOURCE_STALE" in audit_personnel(p, NOW)["reasons"]
    s["retrieved_at"] = (NOW + timedelta(microseconds=1)).isoformat()
    assert kind.upper() + "_SOURCE_AFTER_CUTOFF" in audit_personnel(p, NOW)["reasons"]


@pytest.mark.parametrize("label,code", [
    ("2026-10-27", None), ("2026-10-26", "DEPTH_DEPTH_CHART_STALE"),
    ("2026-11-04", "DEPTH_DEPTH_CHART_AFTER_CUTOFF"), ("bad", "DEPTH_DEPTH_CHART_DATE_INVALID")])
def test_fresh_fetch_does_not_refresh_old_depth_chart(label, code):
    p = profile()
    depth = p["personnel"]["source_records"][1]
    depth.update(status="VISUALLY_TRANSCRIBED", transcription=dict(
        chart_label_date=label, reviewed_at=NOW.isoformat(), content_sha256="fixture-image"))
    result = audit_personnel(p, NOW)
    if code:
        assert code in result["reasons"]
    else:
        assert not any("DEPTH_CHART_" in reason for reason in result["reasons"])


def test_depth_uses_eastern_calendar_not_utc_date():
    p = profile()
    p["personnel"]["source_records"][1]["transcription"] = dict(
        chart_label_date="2026-10-26", reviewed_at="2026-11-03T00:00:00Z", content_sha256="image")
    assert "DEPTH_DEPTH_CHART_STALE" not in audit_personnel(p, "2026-11-03T01:00:00Z")["reasons"]


def test_missing_injury_coverage_preserves_historical_rows_without_backfill():
    p = profile()
    p["personnel"]["injury_report_rows"] = []
    p["personnel"]["injury_coverage"] = dict(status="CURRENT_REPORT_NOT_AVAILABLE",
        historical_reports=[dict(status="HISTORICAL_ONLY", rows=[{"fixture": True}])])
    p["personnel"]["source_records"][2]["status"] = "CURRENT_REPORT_NOT_AVAILABLE"
    result = audit_personnel(p, NOW)
    assert "CURRENT_INJURY_COVERAGE_UNAVAILABLE" in result["reasons"]
    assert "INJURY_SOURCE_UNAVAILABLE" in result["reasons"]
    assert result["evidence"]["injury_report_row_count"] == 0
    assert result["evidence"]["historical_injury_report_count"] == 1


@pytest.mark.parametrize("field,value,code", [
    ("player_reference", "fixture:other", "EXACT_IDENTITY_REVIEW_MISMATCH"),
    ("position", "WR", "EXACT_IDENTITY_REVIEW_MISMATCH"),
    ("team_id", "SEA", "PLAYER_TEAM_MISMATCH"),
    ("provider_identities", {"espn_id": "123"}, "EXACT_IDENTITY_REVIEW_MISMATCH"),
    ("provider_identities", {"player_name": "Fixture Player"}, "STABLE_PROVIDER_ID_MISSING"),
    ("provider_identities", {"espn_id": 123}, "STABLE_PROVIDER_ID_MISSING"),
    ("provider_identities", {"espn_id": " 123 "}, "STABLE_PROVIDER_ID_MISSING"),
    ("identity_review", None, "EXACT_IDENTITY_REVIEW_MISSING"),
    ("roster_sections", ["Practice Squad"], "ROSTER_MEMBERSHIP_REQUIRES_RECONCILIATION"),
    ("roster_sections", [], "ROSTER_MEMBERSHIP_REQUIRES_RECONCILIATION"),
    ("depth_roles", [], "PLAYER_DEPTH_UNAVAILABLE"),
    ("review_flags", ["DEPTH_PLAYER_MISSING_FROM_ROSTER"], "PLAYER_REVIEW_FLAGS_PRESENT"),
])
def test_identity_is_exact_and_membership_limitations_are_blockers(field, value, code):
    p = profile()
    p["personnel"]["players"][0][field] = value
    assert code in audit_personnel(p, NOW)["reasons"]


@pytest.mark.parametrize("field,value,code", [
    ("reviewed_at", "2026-11-03T12:00:01Z", "IDENTITY_REVIEW_TIME_INVALID_OR_FUTURE"),
    ("reviewed_at", "2026-11-03T12:00:00", "IDENTITY_REVIEW_TIME_INVALID_OR_FUTURE"),
    ("matched_biographical_fields", ["height"] * 3, "IDENTITY_BIOGRAPHY_INCOMPLETE"),
    ("sources", [], "IDENTITY_SOURCES_INCOMPLETE"),
])
def test_identity_review_requires_dated_independent_evidence(field, value, code):
    p = profile()
    p["personnel"]["players"][0]["identity_review"][field] = value
    assert code in audit_personnel(p, NOW)["reasons"]


def test_duplicate_identity_and_reference_not_silently_collapsed():
    p = profile()
    p["personnel"]["players"].append(copy.deepcopy(p["personnel"]["players"][0]))
    result = audit_personnel(p, NOW)
    assert "DUPLICATE_STABLE_PROVIDER_ID" in result["reasons"]
    assert "DUPLICATE_PLAYER_REFERENCE" in result["reasons"]
    assert result["evidence"]["player_count"] == 2
    assert result["evidence"]["duplicate_provider_ids"][0]["id"] == "00123"
    assert all(not player["identity_metadata_verified"] for player in result["evidence"]["players"])
    assert all("DUPLICATE_STABLE_PROVIDER_ID" in player["issues"] for player in result["evidence"]["players"])


@pytest.mark.parametrize("invalid", [None, {}, [], "bad"])
def test_missing_or_malformed_profiles_fail_closed(invalid):
    result = audit_personnel(invalid, NOW)
    assert result["status"] == "BLOCKED"
    assert "PERSONNEL_MISSING_OR_INVALID" in result["reasons"]


@pytest.mark.parametrize("invalid", [None, "bad", NOW.replace(tzinfo=None), "2026-11-03"])
def test_invalid_cutoffs_fail_closed(invalid):
    assert "AS_OF_INVALID" in audit_personnel(profile(), invalid)["reasons"]


def test_source_failure_missing_provenance_and_duplicate_kind_are_visible():
    p = profile()
    s = p["personnel"]["source_records"][0]
    s.update(status="FAILED", content_sha256=None, retrieved_at=None)
    p["personnel"]["source_records"].append(copy.deepcopy(s))
    reasons = audit_personnel(p, NOW)["reasons"]
    assert {"ROSTER_SOURCE_MISSING_OR_AMBIGUOUS", "ROSTER_SOURCE_UNAVAILABLE",
            "ROSTER_SOURCE_TIME_INVALID", "ROSTER_SOURCE_PROVENANCE_MISSING"} <= set(reasons)


def test_identity_and_sources_cannot_postdate_profile_even_at_later_audit_time():
    p = profile()
    p["as_of"] = (NOW - timedelta(hours=1)).isoformat()
    reasons = audit_personnel(p, NOW)["reasons"]
    assert "ROSTER_SOURCE_AFTER_PROFILE" in reasons
    assert "IDENTITY_REVIEW_TIME_INVALID_OR_FUTURE" in reasons


def test_audit_is_repeatable_json_serializable_and_does_not_mutate_or_alias_input():
    p = profile()
    before = copy.deepcopy(p)
    result = audit_personnel(p, NOW)
    assert result == audit_personnel(p, NOW.isoformat().replace("+00:00", "Z"))
    assert p == before
    json.dumps(result, allow_nan=False)
    result["evidence"]["players"][0]["provider_identities"]["gsis_id"] = "changed"
    assert p == before


@pytest.mark.parametrize("roles", [[None], [{}], ["QB"],
    [dict(position="QB", depth=True, alignment_row="0:1")],
    [dict(position="QB", depth=0, alignment_row="0:1")],
    [dict(position="QB", depth=1, alignment_row=None)]])
def test_nonempty_malformed_depth_roles_are_explicitly_blocked(roles):
    p = profile()
    p["personnel"]["players"][0]["depth_roles"] = roles
    assert "PLAYER_DEPTH_INVALID" in audit_personnel(p, NOW)["reasons"]


@pytest.mark.parametrize("rows", [[None], [{}], [dict(columns=[])], [dict(columns={})]])
def test_nonempty_malformed_injury_rows_do_not_establish_coverage(rows):
    p = profile()
    p["personnel"]["injury_report_rows"] = rows
    assert "CURRENT_INJURY_ROWS_INVALID" in audit_personnel(p, NOW)["reasons"]


@pytest.mark.parametrize("record", [None, "bad", {}, dict(kind="unknown")])
def test_extra_malformed_source_record_is_not_silently_ignored(record):
    p = profile()
    p["personnel"]["source_records"].append(record)
    assert "PERSONNEL_SOURCE_RECORD_INVALID" in audit_personnel(p, NOW)["reasons"]


def test_depth_review_after_profile_is_blocked_even_before_audit_cutoff():
    p = profile()
    p["as_of"] = (NOW - timedelta(hours=1)).isoformat()
    p["personnel"]["source_records"][1]["transcription"] = dict(
        chart_label_date="2026-11-03", reviewed_at=NOW.isoformat(), content_sha256="fixture-image")
    assert "DEPTH_DEPTH_REVIEW_TIME_INVALID_OR_FUTURE" in audit_personnel(p, NOW)["reasons"]


def test_depth_chart_cannot_postdate_profile_even_when_audit_is_later():
    p = profile()
    p["as_of"] = "2026-11-02T23:00:00Z"
    p["personnel"]["source_records"][1]["transcription"] = dict(
        chart_label_date="2026-11-03", reviewed_at=NOW.isoformat(), content_sha256="fixture-image")
    assert "DEPTH_DEPTH_CHART_AFTER_PROFILE" in audit_personnel(p, NOW)["reasons"]


@pytest.mark.parametrize("update,code", [
    (dict(error="failed"), "IDENTITY_SOURCE_UNAVAILABLE"),
    (dict(status="FAILED"), "IDENTITY_SOURCE_UNAVAILABLE"),
    (dict(observed_at="2026-11-03T12:00:01Z"), "IDENTITY_SOURCE_AFTER_REVIEW"),
    (dict(observed_at="2026-11-03T12:00:00"), "SOURCE_TIME_INVALID"),
    (dict(sha256=None), "SOURCE_PROVENANCE_MISSING"),
])
def test_identity_source_failure_and_time_mismatch_invalidate_metadata(update, code):
    p = profile()
    p["personnel"]["players"][0]["identity_review"]["sources"][0].update(update)
    result = audit_personnel(p, NOW)
    assert code in result["reasons"]
    assert result["evidence"]["players"][0]["identity_metadata_verified"] is False


def test_matching_missing_team_ids_do_not_verify_identity():
    p = profile()
    p["team_id"] = None
    player = p["personnel"]["players"][0]
    player["team_id"] = player["identity_review"]["team_id"] = None
    result = audit_personnel(p, NOW)
    assert "EXACT_IDENTITY_REVIEW_MISMATCH" in result["reasons"]
    assert not result["evidence"]["players"][0]["identity_metadata_verified"]


def test_nested_invalid_metadata_cannot_alias_input_through_evidence():
    p = profile()
    p["personnel"]["players"][0]["provider_identities"] = {"espn_id": {"bad": []}}
    before = copy.deepcopy(p)
    result = audit_personnel(p, NOW)
    result["evidence"]["players"][0]["provider_identities"]["espn_id"]["bad"].append("changed")
    assert p == before


@pytest.mark.parametrize("flags", [None, {}, "", False])
def test_malformed_empty_review_flags_are_not_treated_as_clear(flags):
    p = profile()
    p["personnel"]["players"][0]["review_flags"] = flags
    assert "PLAYER_REVIEW_FLAGS_INVALID" in audit_personnel(p, NOW)["reasons"]


def certificate_candidate_profile(empty_injury=False):
    """Synthetic reviewed normalized archives; never a real clearance artifact."""
    import hashlib
    p = profile()
    p.update(game_id="fixture-game", kickoff=(NOW + timedelta(hours=2)).isoformat(),
             snapshot_id="fixture-snapshot")
    player = p["personnel"]["players"][0]
    review = player["identity_review"]
    for s in review["sources"]:
        payload = {k: copy.deepcopy(review[k]) for k in ("team_id", "player_reference", "position",
                   "provider_identities", "matched_biographical_fields")}
        payload.update(kind="identity", observed_at=s["observed_at"])
        s["archive_content"] = json.dumps(payload, sort_keys=True)
        s["sha256"] = hashlib.sha256(s["archive_content"].encode()).hexdigest()
    identity = {k: copy.deepcopy(player[k]) for k in ("player_reference", "provider_identities")}
    roster = dict(identity, roster_sections=["Active"])
    injury = [] if empty_injury else [dict(identity, columns={"Injury": "Fixture only"})]
    p["personnel"]["injury_report_rows"] = injury
    scope = {k: p[k] for k in ("team_id", "game_id", "kickoff", "snapshot_id", "as_of")}
    rows = dict(roster=[roster], depth=[dict(identity, depth_roles=player["depth_roles"])],
                injury=injury, active=[dict(identity, status="ACTIVE")], transactions=[])
    cert = dict(schema="PERSONNEL_CLEARANCE_V1", scope=scope, reviewed_at=NOW.isoformat(),
                reviewer_reference="fixture:review", archives={})
    for kind, items in rows.items():
        payload = dict(kind=kind, scope=scope, observed_at=NOW.isoformat(), complete=True,
                       pagination_complete=True, row_count=len(items), rows=items)
        if kind == "roster":
            payload["covered_sections"] = ["Active", "Reserve", "Practice Squad", "Other"]
        if kind == "active":
            payload["evidence_type"] = "OFFICIAL_GAME_DAY_ACTIVE_LIST"
        if kind == "transactions":
            payload.update(baseline_rows=[roster], window_start=(NOW - timedelta(hours=5)).isoformat(),
                           window_end=NOW.isoformat())
        raw = json.dumps(payload, sort_keys=True)
        archive = dict(reference="fixture:" + kind, archive_content=raw,
                       content_sha256=hashlib.sha256(raw.encode()).hexdigest())
        cert["archives"][kind] = archive
        for s in p["personnel"]["source_records"]:
            if s["kind"] == kind:
                s["content_sha256"] = archive["content_sha256"]
    cert["snapshot_sha256"] = hashlib.sha256(json.dumps(p, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    p["personnel"]["clearance_certificate"] = cert
    return p


def rebind_snapshot(p):
    import hashlib
    snapshot = copy.deepcopy(p)
    cert = snapshot["personnel"].pop("clearance_certificate")
    p["personnel"]["clearance_certificate"]["snapshot_sha256"] = hashlib.sha256(json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def change_archive(p, kind, mutate, bind_source=True):
    import hashlib
    archive = p["personnel"]["clearance_certificate"]["archives"][kind]
    payload = json.loads(archive["archive_content"])
    mutate(payload)
    archive["archive_content"] = json.dumps(payload)
    archive["content_sha256"] = hashlib.sha256(archive["archive_content"].encode()).hexdigest()
    if bind_source:
        for s in p["personnel"]["source_records"]:
            if s["kind"] == kind:
                s["content_sha256"] = archive["content_sha256"]
    rebind_snapshot(p)


@pytest.mark.parametrize("empty_injury", [False, True])
def test_self_attested_complete_archives_never_satisfy_personnel_gates(empty_injury):
    p = certificate_candidate_profile(empty_injury)
    before = copy.deepcopy(p)
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED", result
    assert "PERSONNEL_CLEARANCE_AUTHORITY_UNVERIFIED" in result["reasons"]
    assert result["evidence"]["certificate_structure_verified"] is True
    assert all(i.get("code") == "SOURCE_AUTHORITY_UNVERIFIED" for i in result["evidence"]["certificate_issues"])
    assert result["evidence"]["archive_content_reverified"] is True
    for field in ("completeness_verified", "game_specific_active_verified",
                  "transaction_reconciliation_verified", "source_authority_verified", "clearance_supported"):
        assert result["evidence"][field] is False
    assert result["evidence"]["players"][0]["game_specific_active_verified"] is False
    assert p == before
    assert result == audit_personnel(p, NOW)
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("field", ["schema", "scope", "reviewed_at", "reviewer_reference", "snapshot_sha256", "archives"])
def test_missing_certificate_fields_are_exactly_reported(field):
    p = certificate_candidate_profile()
    del p["personnel"]["clearance_certificate"][field]
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert result["evidence"]["certificate_structure_verified"] is False
    assert any(i["field"] == "personnel.clearance_certificate." + field for i in result["evidence"]["certificate_issues"])


@pytest.mark.parametrize("field,value", [("game_id", "other-game"), ("snapshot_id", "other-snapshot"),
    ("team_id", "SEA"), ("kickoff", NOW.isoformat()), ("as_of", NOW.replace(tzinfo=None).isoformat())])
def test_certificate_cannot_be_reused_for_another_game_snapshot_or_time(field, value):
    p = certificate_candidate_profile()
    p[field] = value
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert not result["evidence"]["certificate_structure_verified"]


@pytest.mark.parametrize("kind", ["roster", "depth", "injury", "active", "transactions"])
@pytest.mark.parametrize("mutation", ["hash", "scope", "missing", "complete", "pagination", "count", "future", "stale", "naive", "absent_rows"])
def test_each_archive_requires_exact_identity_time_and_full_coverage(kind, mutation):
    p = certificate_candidate_profile()
    cert = p["personnel"]["clearance_certificate"]
    if mutation == "hash":
        cert["archives"][kind]["archive_content"] += " "
    elif mutation == "missing":
        del cert["archives"][kind]
    else:
        def mutate(payload):
            if mutation == "scope": payload["scope"]["game_id"] = "wrong"
            if mutation == "complete": payload["complete"] = False
            if mutation == "pagination": payload["pagination_complete"] = False
            if mutation == "count": payload["row_count"] = True
            if mutation == "future": payload["observed_at"] = (NOW + timedelta(seconds=1)).isoformat()
            if mutation == "stale": payload["observed_at"] = (NOW - timedelta(hours=24, seconds=1)).isoformat()
            if mutation == "naive": payload["observed_at"] = NOW.replace(tzinfo=None).isoformat()
            if mutation == "absent_rows": del payload["rows"]
        change_archive(p, kind, mutate)
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert "PERSONNEL_CLEARANCE_CERTIFICATE_INVALID" in result["reasons"]
    assert result["evidence"]["certificate_structure_verified"] is False
    assert {"PERSONNEL_COMPLETENESS_UNVERIFIED", "GAME_SPECIFIC_ACTIVE_PROOF_UNAVAILABLE",
            "TRANSACTION_RECONCILIATION_UNVERIFIED"} <= set(result["reasons"])
    assert result["evidence"]["certificate_issues"]


@pytest.mark.parametrize("kind", ["roster", "depth", "injury", "active"])
@pytest.mark.parametrize("mutation", ["name_only", "id_mismatch", "duplicate", "absent_player"])
def test_archive_rows_never_join_by_name_or_ignore_identity_conflicts(kind, mutation):
    p = certificate_candidate_profile()
    def mutate(payload):
        if mutation == "name_only":
            payload["rows"][0].pop("provider_identities")
            payload["rows"][0]["player"] = "Fixture Player"
        if mutation == "id_mismatch": payload["rows"][0]["provider_identities"] = {"espn_id": "different"}
        if mutation == "duplicate": payload["rows"].append(copy.deepcopy(payload["rows"][0]))
        if mutation == "absent_player": payload["rows"] = []
        payload["row_count"] = len(payload["rows"])
    change_archive(p, kind, mutate)
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert result["evidence"]["certificate_structure_verified"] is False


@pytest.mark.parametrize("status", [None, "INACTIVE", "ROSTER_ACTIVE_CONTEXT_ONLY", True])
def test_roster_or_inactive_absence_cannot_substitute_for_game_active_proof(status):
    p = certificate_candidate_profile()
    change_archive(p, "active", lambda a: a["rows"][0].update(status=status))
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert not result["evidence"]["certificate_structure_verified"]


def test_roster_inference_is_rejected_even_with_active_status_string():
    p = certificate_candidate_profile()
    change_archive(p, "active", lambda a: a.update(evidence_type="ROSTER"))
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert not result["evidence"]["certificate_structure_verified"]


def test_archive_hash_alone_does_not_bind_supplied_rows_or_source():
    p = certificate_candidate_profile()
    change_archive(p, "depth", lambda a: a["rows"][0]["depth_roles"][0].update(depth=2), bind_source=False)
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert not result["evidence"]["certificate_structure_verified"]


@pytest.mark.parametrize("mutation", ["baseline_missing", "end_gap", "bad_start", "missing_event", "future_event", "wrong_remove", "duplicate_event"])
def test_transactions_require_complete_interval_and_exact_roster_replay(mutation):
    p = certificate_candidate_profile()
    def mutate(payload):
        member = copy.deepcopy(payload["baseline_rows"][0])
        if mutation == "baseline_missing": del payload["baseline_rows"]
        if mutation == "end_gap": payload["window_end"] = (NOW - timedelta(seconds=1)).isoformat()
        if mutation == "bad_start": payload["window_start"] = "bad"
        if mutation == "missing_event": payload["baseline_rows"] = []
        if mutation in ("future_event", "wrong_remove", "duplicate_event"):
            event = dict(transaction_id="fixture-event", effective_at=NOW.isoformat(), action="REMOVE", member=member)
            if mutation == "future_event": event["effective_at"] = (NOW + timedelta(seconds=1)).isoformat()
            if mutation == "wrong_remove": event["member"]["provider_identities"] = {"espn_id": "other"}
            payload["rows"] = [event] * (2 if mutation == "duplicate_event" else 1)
            payload["row_count"] = len(payload["rows"])
    change_archive(p, "transactions", mutate)
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert not result["evidence"]["certificate_structure_verified"]


def test_valid_transaction_replay_can_establish_membership_without_implying_activity():
    p = certificate_candidate_profile()
    def mutate(payload):
        member = payload["baseline_rows"].pop()
        payload["rows"] = [dict(transaction_id="fixture-add", effective_at=NOW.isoformat(), action="ADD", member=member)]
        payload["row_count"] = 1
    change_archive(p, "transactions", mutate)
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert result["evidence"]["certificate_structure_verified"] is True
    del p["personnel"]["clearance_certificate"]["archives"]["active"]
    assert audit_personnel(p, NOW)["status"] == "BLOCKED"


@pytest.mark.parametrize("raw", ['{"rows": [], "rows": []}', '{"value": NaN}', 'null', '[1]', 'bad'])
def test_ambiguous_or_invalid_archive_json_fails_closed(raw):
    import hashlib
    p = certificate_candidate_profile()
    p["personnel"]["clearance_certificate"]["archives"]["active"].update(
        archive_content=raw, content_sha256=hashlib.sha256(raw.encode()).hexdigest())
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert not result["evidence"]["certificate_structure_verified"]


def test_certificate_does_not_override_existing_identity_or_eligibility_blockers():
    p = certificate_candidate_profile()
    p["personnel"]["players"][0]["review_flags"] = ["UNRESOLVED"]
    rebind_snapshot(p)
    result = audit_personnel(p, NOW)
    assert "PLAYER_REVIEW_FLAGS_PRESENT" in result["reasons"]
    assert not result["evidence"]["game_specific_active_verified"]
    assert not result["evidence"]["players"][0]["game_specific_active_verified"]


@pytest.mark.parametrize("mutation", ["missing", "hash", "ids", "observed"])
def test_certified_identity_reviews_require_matching_archive_bytes(mutation):
    import hashlib
    p = certificate_candidate_profile()
    s = p["personnel"]["players"][0]["identity_review"]["sources"][0]
    if mutation == "missing": del s["archive_content"]
    if mutation == "hash": s["sha256"] = "a" * 64
    if mutation in ("ids", "observed"):
        payload = json.loads(s["archive_content"])
        if mutation == "ids": payload["provider_identities"] = {"espn_id": "wrong"}
        if mutation == "observed": payload["observed_at"] = (NOW - timedelta(hours=1)).isoformat()
        s["archive_content"] = json.dumps(payload)
        s["sha256"] = hashlib.sha256(s["archive_content"].encode()).hexdigest()
    rebind_snapshot(p)
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert any(i["field"].startswith("personnel.players[0].identity_review.sources[0]")
               for i in result["evidence"]["certificate_issues"])


@pytest.mark.parametrize("claims", [{"verified": True, "authority_verified": True},
    {"signature": "manual-signature", "publisher": "NFL", "status": "VERIFIED"},
    {"full_inventory_verified": True, "trusted": True, "reviewer_reference": "official reviewer"}])
def test_manual_authority_claims_cannot_create_a_trust_root(claims):
    p = certificate_candidate_profile()
    p["personnel"]["clearance_certificate"].update(claims)
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert result["evidence"]["certificate_structure_verified"] is True
    assert not result["evidence"]["source_authority_verified"]
    assert {"PERSONNEL_COMPLETENESS_UNVERIFIED", "GAME_SPECIFIC_ACTIVE_PROOF_UNAVAILABLE",
            "TRANSACTION_RECONCILIATION_UNVERIFIED"} <= set(result["reasons"])


def test_official_looking_urls_and_matching_hashes_do_not_authenticate_archives():
    p = certificate_candidate_profile()
    cert = p["personnel"]["clearance_certificate"]
    for kind, archive in cert["archives"].items():
        archive["reference"] = "https://www.nfl.com/fixture-only/" + kind
        for s in p["personnel"]["source_records"]:
            if s["kind"] == kind:
                s["reference"] = archive["reference"]
    rebind_snapshot(p)
    result = audit_personnel(p, NOW)
    assert result["evidence"]["certificate_structure_verified"] is True
    assert result["status"] == "BLOCKED"
    assert all(i.get("code") == "SOURCE_AUTHORITY_UNVERIFIED" for i in result["evidence"]["certificate_issues"])


def test_no_evidence_reports_exact_missing_fields_and_trusted_source_requirements():
    result = audit_personnel(profile(), NOW)
    issues = result["evidence"]["certificate_issues"]
    fields = {i["field"] for i in issues}
    assert {"game_id", "kickoff", "snapshot_id", "personnel.clearance_certificate.scope",
            "personnel.clearance_certificate.snapshot_sha256"} <= fields
    for kind in ("roster", "depth", "injury", "active", "transactions"):
        assert "personnel.clearance_certificate.archives." + kind + ".publisher_provenance" in fields
    assert result["evidence"]["clearance_supported"] is False


def test_full_inventory_claim_for_single_player_never_establishes_source_completeness():
    # A malicious producer can remove absent real players before hashing every
    # collection. Internal equality cannot detect those undisclosed source rows.
    p = certificate_candidate_profile()
    result = audit_personnel(p, NOW)
    assert result["evidence"]["certificate_structure_verified"] is True
    assert not result["evidence"]["completeness_verified"]
    assert "PERSONNEL_COMPLETENESS_UNVERIFIED" in result["reasons"]


@pytest.mark.parametrize("mutation", ["missing_section", "unknown_identity", "missing_active_row"])
def test_candidate_diagnostics_identify_the_actual_inventory_failure(mutation):
    p = certificate_candidate_profile()
    if mutation == "missing_section":
        change_archive(p, "roster", lambda a: a.update(covered_sections=["Active"]))
        expected = ".archives.roster.covered_sections"
    elif mutation == "unknown_identity":
        change_archive(p, "active", lambda a: a["rows"][0].update(player_reference="unknown"))
        expected = ".archives.active.rows"
    else:
        change_archive(p, "active", lambda a: a.update(rows=[], row_count=0))
        expected = ".archives.active.rows"
    result = audit_personnel(p, NOW)
    assert any(i["field"].endswith(expected) and not i.get("code")
               for i in result["evidence"]["certificate_issues"])


@pytest.mark.parametrize("bad", [None, [], "manual", True])
def test_malformed_certificate_objects_are_diagnostics_not_exceptions(bad):
    p = profile()
    p["personnel"]["clearance_certificate"] = bad
    result = audit_personnel(p, NOW)
    assert result["status"] == "BLOCKED"
    assert not result["evidence"]["certificate_structure_verified"]
    assert "PERSONNEL_CLEARANCE_CERTIFICATE_INVALID" in result["reasons"]


def test_certificate_diagnostics_do_not_alias_caller_inputs():
    p = certificate_candidate_profile()
    before = copy.deepcopy(p)
    result = audit_personnel(p, NOW)
    result["evidence"]["certificate_issues"][0]["field"] = "changed"
    assert p == before
