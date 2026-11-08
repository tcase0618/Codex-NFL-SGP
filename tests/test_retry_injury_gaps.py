import copy
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from football_engine.team_profiles import content_hash
from research.retry_injury_gaps import retry_injury_gaps

NOW = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)


def target():
    return dict(team_id="CIN", opponent_id="ATL", game_id="fixture-game", week=9,
                season=2026, season_type=2, kickoff=(NOW+timedelta(days=4)).isoformat())


def fixture(tmp_path, week=9, team="CIN", damaged=False):
    raw = (f'<select><option selected value="/team/injury-report/week/REG-{week}">Week</option></select>'
           '<select><option selected value="2026">Season</option></select>'
           f'<div class="nfl-o-injury-report__container"><div class="nfl-o-injury-report__club-logo">'
           f'<img src="https://static.test/clubs/logos/{team}"></div>'
           '<table><thead><tr><th>Player</th><th>Injury</th><th>Wed</th></tr></thead>'
           '<tbody><tr><td>Fixture Player</td><td>Knee</td><td>DNP</td></tr></tbody></table></div>').encode()
    path = tmp_path / "injury.html"
    path.write_bytes(raw)
    page = dict(team="CIN", kind="injury", reference="https://www.bengals.com/team/injury-report/",
                retrieved_at=NOW.isoformat(), html_file=str(path),
                content_sha256="wrong" if damaged else hashlib.sha256(raw).hexdigest())
    profile = dict(team_id="CIN", season_year=2026, as_of=NOW.isoformat(), last_game={"week":8},
                   personnel={"source_records":[{"kind":"injury", "status":"CURRENT_REPORT_NOT_AVAILABLE"}],
                              "players":[{"eligibility":"REQUIRES_GAME_DAY_CONFIRMATION"}]},
                   blocking_review_reasons=["current injury report unavailable or incomplete; confirm eligibility before selection",
                                            "unrelated gate"])
    profile["version"] = content_hash(profile)
    gaps = [dict(team="CIN", kind="injury"), dict(team="DAL", kind="transactions")]
    return {"CIN":profile}, gaps, page


def test_new_report_repairs_only_source_context_and_preserves_eligibility(tmp_path):
    profiles, gaps, page = fixture(tmp_path)
    original = copy.deepcopy(profiles)
    result, pending, attempts, changes, sources = retry_injury_gaps(
        profiles,gaps,{},tmp_path,now=NOW,fetch=lambda task,path:page,expected_games={"CIN":target()})
    assert changes == ["CIN"] and pending == [gaps[1]]
    assert result["CIN"]["personnel"]["players"] == original["CIN"]["personnel"]["players"]
    assert result["CIN"]["blocking_review_reasons"] == ["unrelated gate"]
    assert profiles == original and attempts["CIN"] == NOW.isoformat()
    assert result["CIN"]["version"] != profiles["CIN"]["version"]
    assert sources[0]["eligibility"] == "REQUIRES_GAME_DAY_CONFIRMATION"


@pytest.mark.parametrize("change", ["previous_week", "opponent_only", "damaged", "missing", "future", "old_season"])
def test_inadequate_source_cannot_clear_gap(tmp_path, change, monkeypatch):
    profiles, gaps, page = fixture(tmp_path, week=8 if change=="previous_week" else 9,
                                   team="TEN" if change=="opponent_only" else "CIN", damaged=change=="damaged")
    if change == "missing":
        page.pop("html_file")
    if change == "future":
        page["retrieved_at"] = (NOW+timedelta(hours=1)).isoformat()
    if change == "old_season":
        profiles["CIN"]["season_year"] = 2025
    monkeypatch.setattr("research.source_gap_repair.fetch_page", lambda *args: {})
    result, pending, _, changes, _ = retry_injury_gaps(profiles,gaps,{},tmp_path,now=NOW,fetch=lambda task,path:page,expected_games={"CIN":target()})
    assert pending == gaps and not changes and result == profiles


def test_quiet_poll_does_not_refetch_within_hour(tmp_path):
    profiles,gaps,_ = fixture(tmp_path)
    def forbidden(*args):
        raise AssertionError("unchanged hourly poll fetched too early")
    result,pending,_,changes,sources = retry_injury_gaps(profiles,gaps,{"CIN":NOW.isoformat()},tmp_path,
                                                      now=NOW+timedelta(minutes=59),fetch=forbidden)
    assert result==profiles and pending==gaps and changes==sources==[]


@pytest.mark.parametrize("case", ["missing", "other_week", "already_started", "other_team"])
def test_upcoming_schedule_scope_required(tmp_path, case):
    profiles,gaps,page = fixture(tmp_path)
    game = target()
    if case == "other_week": game["week"] = 10
    if case == "already_started": game["kickoff"] = NOW.isoformat()
    if case == "other_team": game["team_id"] = "ATL"
    expected = {} if case == "missing" else {"CIN":game}
    _,pending,_,changes,_=retry_injury_gaps(profiles,gaps,{},tmp_path,now=NOW,
        fetch=lambda task,path:page,expected_games=expected)
    assert pending==gaps and changes==[]


def test_one_unavailable_club_does_not_discard_other_recovery(tmp_path):
    profiles,gaps,page=fixture(tmp_path)
    profiles["HOU"] = copy.deepcopy(profiles["CIN"])
    profiles["HOU"]["team_id"] = "HOU"
    gaps.append(dict(team="HOU",kind="injury"))
    def fetch(task,path):
        if task[0]=="HOU": raise OSError("club unavailable")
        return page
    _,pending,_,changes,sources=retry_injury_gaps(profiles,gaps,{},tmp_path,now=NOW,
        fetch=fetch,expected_games={"CIN":target()})
    assert changes==["CIN"]
    assert {g["team"] for g in pending}=={"DAL","HOU"}
    assert sources[1]["status"]=="FAILED"
