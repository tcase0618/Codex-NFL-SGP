"""Pure CURRENT personnel audit for automatic team-profile dictionaries.

This audits supplied metadata and inspects optional embedded certificate candidates.
Fresh retrieval is limited to 24 hours (inclusive); dated depth charts additionally
expire after seven Eastern calendar days, matching automatic_refresh's policy.
These are conservative audit cutoffs, not provider guarantees. Current automatic
profiles cannot PASS projection/ticket use: the input contract has no independent
publisher-authentication or full-source-coverage verifier. Identity evidence and
membership context remain separately inspectable; neither implies eligibility.
"""

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json


SOURCE_MAX_AGE = timedelta(hours=24)
DEPTH_MAX_AGE_DAYS = 7
PROVIDER_KEYS = frozenset(("gsis_id", "espn_id"))
_EASTERN = ZoneInfo("America/New_York")


def _time(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _list(value):
    return value if isinstance(value, list) else []


def _source_issues(source, cutoff, time_key="retrieved_at", hash_key="content_sha256", fresh=True):
    issues = []
    if not _text(source.get("reference")) or not _text(source.get(hash_key)):
        issues.append("SOURCE_PROVENANCE_MISSING")
    observed = _time(source.get(time_key))
    if observed is None:
        issues.append("SOURCE_TIME_INVALID")
    elif cutoff is not None:
        if observed > cutoff:
            issues.append("SOURCE_AFTER_CUTOFF")
        elif fresh and cutoff - observed > SOURCE_MAX_AGE:
            issues.append("SOURCE_STALE")
    return issues


def _identity_issues(player, team, cutoff):
    ids = player.get("provider_identities")
    if not isinstance(ids, dict) or not any(key in PROVIDER_KEYS and _text(value) for key, value in ids.items()):
        return ["STABLE_PROVIDER_ID_MISSING"]
    if any(key not in PROVIDER_KEYS or not _text(value) for key, value in ids.items()):
        return ["STABLE_PROVIDER_ID_INVALID"]
    review = player.get("identity_review")
    if not isinstance(review, dict):
        return ["EXACT_IDENTITY_REVIEW_MISSING"]
    issues = []
    if (not _text(team) or not _text(player.get("player_reference")) or not _text(player.get("position"))
            or review.get("player_reference") != player.get("player_reference")
            or review.get("team_id") != team or player.get("team_id") != team
            or review.get("position") != player.get("position")
            or review.get("provider_identities") != ids):
        issues.append("EXACT_IDENTITY_REVIEW_MISMATCH")
    fields = _list(review.get("matched_biographical_fields"))
    if len({field for field in fields if _text(field)}) < 3:
        issues.append("IDENTITY_BIOGRAPHY_INCOMPLETE")
    reviewed = _time(review.get("reviewed_at"))
    if reviewed is None or cutoff is None or reviewed > cutoff:
        issues.append("IDENTITY_REVIEW_TIME_INVALID_OR_FUTURE")
    sources = _list(review.get("sources"))
    if len({s.get("reference") for s in sources if isinstance(s, dict) and _text(s.get("reference"))}) < 2:
        issues.append("IDENTITY_SOURCES_INCOMPLETE")
    for source in sources:
        if not isinstance(source, dict):
            issues.append("IDENTITY_SOURCE_INVALID")
            continue
        issues.extend(_source_issues(source, cutoff, "observed_at", "sha256", fresh=False))
        if source.get("error") or ("status" in source and source["status"] not in ("parsed", "VERIFIED")):
            issues.append("IDENTITY_SOURCE_UNAVAILABLE")
        observed = _time(source.get("observed_at"))
        if observed is not None and reviewed is not None and observed > reviewed:
            issues.append("IDENTITY_SOURCE_AFTER_REVIEW")
    return sorted(set(issues))


def _depth_roles_valid(roles):
    return isinstance(roles, list) and bool(roles) and all(
        isinstance(role, dict) and _text(role.get("position"))
        and type(role.get("depth")) is int and role["depth"] > 0
        and _text(role.get("alignment_row")) for role in roles)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _archive_json(raw):
    return json.loads(raw, object_pairs_hook=_unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _raw_digest(raw):
    try:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    except UnicodeError:
        return None


def _inspect_certificate(profile, personnel, cutoff):
    """Inspect candidate structure and content identity, never grant clearance.

    Hashes establish content identity, not publisher authenticity. See
    docs/personnel-clearance.md describes the missing authority boundary.
    True means structurally consistent only; all content is caller-controlled.
    """
    errors = []
    root = "personnel.clearance_certificate"

    def require(ok, path, message):
        if not ok:
            errors.append({"field": path, "issue": message})

    cert = personnel.get("clearance_certificate")
    if not isinstance(cert, dict):
        require(False, root, "required object: PERSONNEL_CLEARANCE_V1")
        for field in ("schema", "scope", "reviewed_at", "reviewer_reference", "snapshot_sha256", "archives"):
            require(False, root + "." + field, "required certificate field")
        for field in ("game_id", "kickoff", "snapshot_id"):
            require(_text(profile.get(field)), field, "required target-game snapshot binding")
        return False, errors
    require(cert.get("schema") == "PERSONNEL_CLEARANCE_V1", root + ".schema", "expected PERSONNEL_CLEARANCE_V1")
    scope = {key: profile.get(key) for key in ("team_id", "game_id", "kickoff", "snapshot_id", "as_of")}
    for key, value in scope.items():
        require(_text(value), key, "required nonempty string")
    require(cert.get("scope") == scope, root + ".scope", "must exactly match profile team/game/kickoff/snapshot/as_of")
    stamp, kickoff = _time(profile.get("as_of")), _time(profile.get("kickoff"))
    reviewed = _time(cert.get("reviewed_at"))
    require(stamp is not None and kickoff is not None and cutoff is not None
            and stamp <= cutoff < kickoff, "kickoff", "requires aware profile.as_of <= audit cutoff < kickoff")
    require(reviewed is not None and stamp is not None and reviewed == stamp,
            root + ".reviewed_at", "must equal aware snapshot as_of")
    require(_text(cert.get("reviewer_reference")), root + ".reviewer_reference", "required accountable review reference")
    snapshot = deepcopy(profile)
    snapshot["personnel"].pop("clearance_certificate", None)
    try:
        fingerprint = _digest(snapshot)
    except (ValueError, TypeError, OverflowError, RecursionError):
        fingerprint = None
    require(fingerprint is not None and cert.get("snapshot_sha256") == fingerprint,
            root + ".snapshot_sha256", "must hash canonical JSON of entire profile excluding certificate")
    archives = cert.get("archives")
    kinds = ("roster", "depth", "injury", "active", "transactions")
    require(isinstance(archives, dict) and set(archives) == set(kinds), root + ".archives", "requires exactly roster/depth/injury/active/transactions")
    archives = archives if isinstance(archives, dict) else {}
    payloads = {}
    references = []
    for kind in kinds:
        path = root + ".archives." + kind
        archive = archives.get(kind)
        if not isinstance(archive, dict):
            require(False, path, "required archive object")
            continue
        require(_text(archive.get("reference")), path + ".reference", "required source reference")
        if _text(archive.get("reference")):
            references.append(archive["reference"])
        raw = archive.get("archive_content")
        require(isinstance(raw, str), path + ".archive_content", "required exact archived UTF-8 JSON text")
        if not isinstance(raw, str):
            continue
        require(_text(archive.get("content_sha256")) and archive.get("content_sha256") == _raw_digest(raw),
                path + ".content_sha256", "must equal SHA256 of archive_content UTF-8 bytes")
        try:
            payload = _archive_json(raw)
        except (ValueError, TypeError, RecursionError):
            require(False, path + ".archive_content", "requires unambiguous finite JSON object")
            continue
        if not isinstance(payload, dict):
            require(False, path + ".archive_content", "requires JSON object")
            continue
        payloads[kind] = payload
        require(payload.get("scope") == scope, path + ".scope", "must match exact certificate scope")
        require(payload.get("kind") == kind, path + ".kind", "archive kind mismatch")
        observed = _time(payload.get("observed_at"))
        require(observed is not None and stamp is not None and cutoff is not None
                and observed <= stamp and timedelta(0) <= cutoff - observed <= SOURCE_MAX_AGE,
                path + ".observed_at", "requires fresh aware observation no later than snapshot")
        rows = payload.get("rows")
        require(isinstance(rows, list), path + ".rows", "required explicit list, including certified zero rows")
        require(payload.get("complete") is True, path + ".complete", "requires explicit full source coverage")
        require(type(payload.get("row_count")) is int and isinstance(rows, list)
                and payload["row_count"] == len(rows), path + ".row_count", "requires exact integer row count")
        require(payload.get("pagination_complete") is True, path + ".pagination_complete", "all source pages required")
        if kind in ("roster", "depth", "injury"):
            sources = [s for s in _list(personnel.get("source_records")) if isinstance(s, dict) and s.get("kind") == kind]
            require(len(sources) == 1 and all(s.get("reference") == archive.get("reference")
                    and s.get("content_sha256") == archive.get("content_sha256")
                    and _time(s.get("retrieved_at")) == observed for s in sources),
                    path, "archive must identify the exact supplied source record, hash and observation")
    require(len(references) == len(set(references)), root + ".archives", "distinct source references required")

    players = _list(personnel.get("players"))
    identities = {}
    for i, player in enumerate(players):
        if isinstance(player, dict) and _text(player.get("player_reference")):
            identities[player["player_reference"]] = player.get("provider_identities")
        if not isinstance(player, dict):
            continue
        review = player.get("identity_review")
        review = review if isinstance(review, dict) else {}
        for j, source in enumerate(_list(review.get("sources"))):
            path = f"personnel.players[{i}].identity_review.sources[{j}]"
            source = source if isinstance(source, dict) else {}
            raw = source.get("archive_content")
            if not isinstance(raw, str):
                require(False, path + ".archive_content", "required archived identity JSON for certified clearance")
                continue
            require(_text(source.get("sha256")) and source.get("sha256") == _raw_digest(raw),
                    path + ".sha256", "must hash exact archived identity UTF-8 bytes")
            try:
                payload = _archive_json(raw)
            except (ValueError, TypeError, RecursionError):
                payload = None
            expected = {key: review.get(key) for key in ("team_id", "player_reference", "position",
                                                        "provider_identities", "matched_biographical_fields")}
            expected.update(kind="identity", observed_at=source.get("observed_at"))
            require(payload == expected, path + ".archive_content", "must bind exact reviewed IDs, team, position, biography fields and source observation")

    def keyed(rows, path):
        mapped = {}
        if not isinstance(rows, list):
            require(False, path, "required ID-bound rows")
            return mapped
        for i, row in enumerate(rows):
            ref = row.get("player_reference") if isinstance(row, dict) else None
            ids = row.get("provider_identities") if isinstance(row, dict) else None
            valid = (_text(ref) and isinstance(ids, dict) and bool(ids)
                     and all(k in PROVIDER_KEYS and _text(v) for k, v in ids.items()))
            require(valid, f"{path}[{i}]", "stable provider IDs and player_reference required; names are insufficient")
            if not valid:
                continue
            require(ref not in mapped, f"{path}[{i}].player_reference", "duplicate identity")
            if ref in identities:
                require(ids == identities[ref], f"{path}[{i}].provider_identities", "exact profile IDs required")
            mapped[ref] = row
        seen = Counter((k, v) for row in mapped.values() for k, v in row["provider_identities"].items())
        require(not any(n > 1 for n in seen.values()), path, "provider identity collision")
        return mapped

    roster = keyed(payloads.get("roster", {}).get("rows"), root + ".archives.roster.rows")
    depth = keyed(payloads.get("depth", {}).get("rows"), root + ".archives.depth.rows")
    active = keyed(payloads.get("active", {}).get("rows"), root + ".archives.active.rows")
    injury = keyed(payloads.get("injury", {}).get("rows"), root + ".archives.injury.rows")
    sections = payloads.get("roster", {}).get("covered_sections")
    require(payloads.get("active", {}).get("evidence_type") == "OFFICIAL_GAME_DAY_ACTIVE_LIST",
            root + ".archives.active.evidence_type", "requires affirmative official game-day activity, never roster inference or absence from inactives")
    require(sections == ["Active", "Reserve", "Practice Squad", "Other"],
            root + ".archives.roster.covered_sections", "must explicitly cover all four roster sections, including empty sections")
    for kind, rows in (("roster", roster), ("depth", depth), ("active", active)):
        require(bool(identities) and set(rows) == set(identities), root + ".archives." + kind + ".rows", "must exactly cover snapshot player identities")
    for player in players:
        if not isinstance(player, dict) or not _text(player.get("player_reference")):
            continue
        ref = player["player_reference"]
        require(roster.get(ref, {}).get("roster_sections") == player.get("roster_sections"),
                root + ".archives.roster.rows." + ref, "roster membership must match profile")
        require(depth.get(ref, {}).get("depth_roles") == player.get("depth_roles"),
                root + ".archives.depth.rows." + ref, "depth roles must match profile")
        require(active.get(ref, {}).get("status") == "ACTIVE",
                root + ".archives.active.rows." + ref + ".status", "explicit target-game ACTIVE proof required")
    require(set(injury) <= set(identities), root + ".archives.injury.rows", "unresolved injury identity")
    require(payloads.get("injury", {}).get("rows") == personnel.get("injury_report_rows"),
            root + ".archives.injury.rows", "must equal supplied current injury rows including IDs and columns")
    tx = payloads.get("transactions", {})
    baseline = keyed(tx.get("baseline_rows"), root + ".archives.transactions.baseline_rows")
    start, end = _time(tx.get("window_start")), _time(tx.get("window_end"))
    require(start is not None and stamp is not None and start <= stamp and end == stamp,
            root + ".archives.transactions.window_start/window_end", "requires baseline-through-snapshot complete interval")
    require(end is not None and _time(tx.get("observed_at")) == end,
            root + ".archives.transactions.observed_at", "complete through-snapshot transaction source must be observed at interval end")
    previous = start
    for i, event in enumerate(_list(tx.get("rows"))):
        path = root + f".archives.transactions.rows[{i}]"
        if not isinstance(event, dict):
            require(False, path, "required transaction object")
            continue
        row = event.get("member")
        member = keyed([row], path + ".member")
        when = _time(event.get("effective_at"))
        require(when is not None and previous is not None and stamp is not None and previous <= when <= stamp,
                path + ".effective_at", "requires ordered events within baseline/snapshot interval")
        previous = when
        require(_text(event.get("transaction_id")), path + ".transaction_id", "required transaction ID")
        action = event.get("action")
        require(action in ("ADD", "REMOVE"), path + ".action", "expected ADD or REMOVE; changes require REMOVE then ADD")
        for ref, member_row in member.items():
            if action == "ADD":
                require(ref not in baseline, path, "ADD must introduce absent member")
                baseline[ref] = member_row
            elif action == "REMOVE":
                require(baseline.get(ref) == member_row, path, "REMOVE must exactly identify existing baseline member")
                baseline.pop(ref, None)
    event_ids = [e.get("transaction_id") for e in _list(tx.get("rows")) if isinstance(e, dict)]
    require(all(_text(v) for v in event_ids) and len(set(v for v in event_ids if _text(v))) == len(event_ids),
            root + ".archives.transactions.rows", "unique transaction IDs required")
    require(baseline == roster, root + ".archives.transactions", "replayed baseline and all events must exactly produce archived roster")
    return not errors, errors


def audit_personnel(profile, as_of) -> dict:
    """Return deterministic blockers and evidence; never mutate the input.

    ``as_of`` accepts an aware datetime or ISO timestamp. Invalid or naive inputs
    produce BLOCKED rather than consulting wall time. Reasons are stable codes;
    detailed source/player evidence explains their scope. PASS would mean only
    this audit has no blockers, never approval of a forecast or wager. The current
    optional PERSONNEL_CLEARANCE_V1 candidate is inspected for exact deficiencies.
    It cannot establish publisher authority or completeness from caller-controlled
    fields; personnel gates remain blocked, including for consistent candidates.
    """
    cutoff = _time(as_of)
    reasons = {"PERSONNEL_COMPLETENESS_UNVERIFIED", "GAME_SPECIFIC_ACTIVE_PROOF_UNAVAILABLE",
               "TRANSACTION_RECONCILIATION_UNVERIFIED"}
    if cutoff is None:
        reasons.add("AS_OF_INVALID")
    if not isinstance(profile, dict):
        reasons.add("PROFILE_INVALID")
        profile = {}
    team = profile.get("team_id")
    if not _text(team):
        reasons.add("TEAM_ID_MISSING")
    profile_time = _time(profile.get("as_of"))
    if profile_time is None:
        reasons.add("PROFILE_TIME_INVALID")
    elif cutoff is not None and profile_time > cutoff:
        reasons.add("PROFILE_AFTER_CUTOFF")
    evidence_cutoff = min(cutoff, profile_time) if cutoff is not None and profile_time is not None else cutoff
    personnel = profile.get("personnel")
    if not isinstance(personnel, dict):
        reasons.add("PERSONNEL_MISSING_OR_INVALID")
        personnel = {}
    structure_verified, certificate_issues = _inspect_certificate(profile, personnel, cutoff)
    if "clearance_certificate" in personnel:
        reasons.add("PERSONNEL_CLEARANCE_AUTHORITY_UNVERIFIED")
        if not structure_verified:
            reasons.add("PERSONNEL_CLEARANCE_CERTIFICATE_INVALID")
    for kind in ("roster", "depth", "injury", "active", "transactions"):
        certificate_issues.append({
            "field": "personnel.clearance_certificate.archives." + kind + ".publisher_provenance",
            "code": "SOURCE_AUTHORITY_UNVERIFIED",
            "issue": "Independent trusted publisher archive acquisition and full-inventory extraction verification unavailable; caller hashes, source URLs, completeness flags and review labels cannot establish authority."})
    certificate_issues.append({
        "field": "personnel.players[*].identity_review.sources[*].publisher_provenance",
        "code": "SOURCE_AUTHORITY_UNVERIFIED",
        "issue": "Independent publisher-backed stable-ID and biographical mapping verification unavailable; matching caller-supplied identity archives are insufficient."})
    players = _list(personnel.get("players"))
    if not players:
        reasons.add("PERSONNEL_PLAYERS_MISSING")
    records = personnel.get("source_records")
    if not isinstance(records, list):
        reasons.add("PERSONNEL_SOURCES_INVALID")
    elif any(not isinstance(record, dict) or record.get("kind") not in ("roster", "depth", "injury")
             for record in records):
        reasons.add("PERSONNEL_SOURCE_RECORD_INVALID")
    source_evidence = []
    for kind in ("roster", "depth", "injury"):
        sources = [s for s in _list(records) if isinstance(s, dict) and s.get("kind") == kind]
        if len(sources) != 1:
            reasons.add(kind.upper() + "_SOURCE_MISSING_OR_AMBIGUOUS")
        for source in sources:
            issues = _source_issues(source, cutoff)
            observed = _time(source.get("retrieved_at"))
            if observed is not None and profile_time is not None and observed > profile_time:
                issues.append("SOURCE_AFTER_PROFILE")
            if source.get("status") not in ("parsed", "VISUALLY_TRANSCRIBED") or source.get("error"):
                issues.append("SOURCE_UNAVAILABLE")
            label = None
            if kind == "depth":
                transcription = source.get("transcription")
                if transcription is not None and not isinstance(transcription, dict):
                    issues.append("DEPTH_TRANSCRIPTION_INVALID")
                if isinstance(transcription, dict):
                    label = transcription.get("chart_label_date")
                    reviewed = _time(transcription.get("reviewed_at"))
                    if reviewed is None or evidence_cutoff is None or reviewed > evidence_cutoff:
                        issues.append("DEPTH_REVIEW_TIME_INVALID_OR_FUTURE")
                    if not _text(transcription.get("content_sha256")):
                        issues.append("DEPTH_TRANSCRIPTION_PROVENANCE_MISSING")
                if source.get("status") == "VISUALLY_TRANSCRIBED" and not label:
                    issues.append("DEPTH_CHART_DATE_MISSING")
                if label is not None:
                    try:
                        chart_date = date.fromisoformat(label)
                    except (TypeError, ValueError):
                        issues.append("DEPTH_CHART_DATE_INVALID")
                    else:
                        if cutoff is not None:
                            age = (cutoff.astimezone(_EASTERN).date() - chart_date).days
                            if age < 0:
                                issues.append("DEPTH_CHART_AFTER_CUTOFF")
                            elif age > DEPTH_MAX_AGE_DAYS:
                                issues.append("DEPTH_CHART_STALE")
                        if profile_time is not None and chart_date > profile_time.astimezone(_EASTERN).date():
                            issues.append("DEPTH_CHART_AFTER_PROFILE")
            reasons.update(kind.upper() + "_" + issue for issue in issues)
            source_evidence.append({"kind": kind, "reference": source.get("reference"),
                                    "status": source.get("status"), "retrieved_at": source.get("retrieved_at"),
                                    "chart_label_date": label, "issues": sorted(set(issues))})
    coverage = personnel.get("injury_coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    injury_rows = personnel.get("injury_report_rows")
    if not isinstance(injury_rows, list) or not injury_rows:
        reasons.add("CURRENT_INJURY_COVERAGE_UNAVAILABLE")
    elif any(not isinstance(row, dict) or not isinstance(row.get("columns"), dict)
             or not row["columns"] for row in injury_rows):
        reasons.add("CURRENT_INJURY_ROWS_INVALID")
    if coverage.get("status") not in ("parsed", "VISUALLY_TRANSCRIBED"):
        reasons.add("CURRENT_INJURY_COVERAGE_UNVERIFIED")
    # Parsed rows and selected-week metadata do not certify target-game scope.
    reasons.add("INJURY_TARGET_GAME_SCOPE_UNVERIFIED")
    player_evidence = []
    identities = Counter()
    references = Counter()
    for index, player in enumerate(players):
        if not isinstance(player, dict):
            reasons.add("PERSONNEL_PLAYER_INVALID")
            player_evidence.append({"index": index, "issues": ["PERSONNEL_PLAYER_INVALID"]})
            continue
        identity_issues = _identity_issues(player, team, evidence_cutoff)
        issues = list(identity_issues)
        if player.get("team_id") != team:
            issues.append("PLAYER_TEAM_MISMATCH")
        if not _text(player.get("player")) or not _text(player.get("position")):
            issues.append("PLAYER_DESCRIPTION_INCOMPLETE")
        sections = player.get("roster_sections")
        membership = "ROSTER_ACTIVE_CONTEXT_ONLY" if sections == ["Active"] else "REQUIRES_RECONCILIATION"
        if sections != ["Active"]:
            issues.append("ROSTER_MEMBERSHIP_REQUIRES_RECONCILIATION")
        if player.get("review_flags"):
            issues.append("PLAYER_REVIEW_FLAGS_PRESENT")
        if not isinstance(player.get("review_flags"), list):
            issues.append("PLAYER_REVIEW_FLAGS_INVALID")
        if not _list(player.get("depth_roles")):
            issues.append("PLAYER_DEPTH_UNAVAILABLE")
        elif not _depth_roles_valid(player["depth_roles"]):
            issues.append("PLAYER_DEPTH_INVALID")
        ids = player.get("provider_identities")
        ids = ids if isinstance(ids, dict) else {}
        for key, value in ids.items():
            if key in PROVIDER_KEYS and _text(value):
                identities[(key, value)] += 1
        reference = player.get("player_reference")
        if _text(reference):
            references[reference] += 1
        reasons.update(issues)
        player_evidence.append({"index": index, "player": player.get("player"),
                                "player_reference": reference, "provider_identities": dict(ids),
                                "membership": membership,
                                "identity_metadata_verified": not identity_issues,
                                "game_specific_active_verified": False, "issues": sorted(set(issues))})
    duplicate_ids = [{"provider": key, "id": value, "count": count}
                     for (key, value), count in sorted(identities.items()) if count > 1]
    if duplicate_ids:
        reasons.add("DUPLICATE_STABLE_PROVIDER_ID")
    if any(count > 1 for count in references.values()):
        reasons.add("DUPLICATE_PLAYER_REFERENCE")
    for player in player_evidence:
        conflicts = []
        if any(identities[(key, value)] > 1 for key, value in player.get("provider_identities", {}).items()
               if key in PROVIDER_KEYS and _text(value)):
            conflicts.append("DUPLICATE_STABLE_PROVIDER_ID")
        reference = player.get("player_reference")
        if _text(reference) and references[reference] > 1:
            conflicts.append("DUPLICATE_PLAYER_REFERENCE")
        if conflicts:
            player["identity_metadata_verified"] = False
            player["game_specific_active_verified"] = False
            player["issues"] = sorted(set(player["issues"] + conflicts))
    return deepcopy({"status": "BLOCKED" if reasons else "PASS", "reasons": sorted(reasons),
            "evidence": {"team_id": team, "as_of": cutoff.isoformat() if cutoff else None,
                         "scope": "CURRENT_PERSONNEL_PROJECTION_AND_TICKET_GATES",
                         "policy": {"source_max_age_hours": 24, "depth_max_age_days": DEPTH_MAX_AGE_DAYS,
                                    "depth_calendar_timezone": "America/New_York"},
                         "player_count": len(players), "sources": source_evidence, "players": player_evidence,
                         "duplicate_provider_ids": duplicate_ids,
                         "injury_status": coverage.get("status"),
                         "injury_report_row_count": len(injury_rows) if isinstance(injury_rows, list) else None,
                         "historical_injury_report_count": len(_list(coverage.get("historical_reports"))),
                         "completeness_verified": False,
                         "game_specific_active_verified": False,
                         "transaction_reconciliation_verified": False,
                         "archive_content_reverified": structure_verified,
                         "certificate_structure_verified": structure_verified,
                         "source_authority_verified": False,
                         "clearance_supported": False,
                         "certificate_issues": certificate_issues,
                         "limitation": "Diagnostics only: embedded normalized hashes establish caller-content identity, not publisher authenticity or source completeness. Trusted acquisition and full-inventory extraction verification are unavailable in this contract; no certificate candidate can clear personnel gates. Roster membership never establishes game activity."}})
