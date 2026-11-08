from datetime import datetime, timezone

from research.pregame_validation import validate_evidence, audit_game


def evidence():
    return {"purpose": "roster", "reference": "fixture:team-active-list", "content_sha256": "a" * 64,
            "published_at": "2026-09-10T16:00:00Z", "version_available_at": "2026-09-10T16:00:00Z",
            "archive_reference": "fixture:immutable-version"}


def test_postgame_participation_and_future_version_are_rejected():
    cutoff = datetime(2026, 9, 10, 17, tzinfo=timezone.utc)
    source = evidence()
    source["version_available_at"] = "2026-09-11T16:00:00Z"
    source["didNotPlay"] = False
    reasons = validate_evidence(source, cutoff, "roster")
    assert any("postgame" in reason for reason in reasons)
    assert any("after decision" in reason for reason in reasons)


def test_original_publication_date_without_archived_version_is_insufficient():
    source = evidence()
    del source["archive_reference"]
    assert validate_evidence(source, datetime(2026, 9, 10, 17, tzinfo=timezone.utc), "roster")


def test_missing_inputs_are_not_labeled_backtest_losses_or_clean_results():
    result = audit_game({"id": "1", "name": "test", "week": 1, "kickoff": "2026-09-10T18:00Z"}, None)
    assert result["status"] == "UNTESTABLE_MISSING_EVIDENCE"
    assert len(result["reasons"]) == 2
