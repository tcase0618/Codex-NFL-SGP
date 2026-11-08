import hashlib
import json

import pytest

from research.recover_league_injuries import parse_week, recover
from research.recover_league_injuries import verified_historical_reports, attach_historical_reports

URL = "https://www.nfl.com/injuries/league/2026/reg8"


def html():
    teams = "".join(f'<div class="nfl-c-matchup-strip__team"><span class="nfl-c-matchup-strip__team-abbreviation">{code}</span>'
                    f'<img src="https://static.www.nfl.com/clubs/logos/{logo}">'
                    f'<a class="nfl-c-matchup-strip__team-fullname" href="/teams/{slug}/">{name}</a></div>'
                    for code, logo, slug, name in [("LAR","LA","los-angeles-rams","Rams"),("CIN","CIN","cincinnati-bengals","Bengals")])
    tables = "".join(f'<div class="nfl-t-stats__title">{name}</div><table><thead><tr>'
                     '<th>Player</th><th>Position</th><th>Injuries</th><th>Practice Status</th><th>Game Status</th>'
                     f'</tr></thead><tbody><tr><td><a href="/players/{slug}/">Fixture</a></td><td>QB</td><td></td><td>Full Participation</td><td></td></tr></tbody></table>'
                     for name, slug in [("Rams","rams-player"),("Bengals","bengals-player")])
    return (f'<select><option selected value="/injuries/league/2026/reg8">WEEK 8</option></select>'
            f'<section class="nfl-o-injury-report__unit">{teams}{tables}</section>').encode()


def test_official_logo_alias_requires_matching_rams_team_route():
    result = parse_week(html(),URL,2026,8)
    assert result["status"] == "RECOVERED_REPORT_CONTENTS"
    assert [r["team_id"] for r in result["reports"]] == ["LAR","CIN"]
    assert not result["game_day_active_proof"]
    assert all(not r["historical_pregame_version_verified"] for r in result["reports"])
    assert result["reports"][0]["rows"][0]["columns"]["Injuries"] == ""


@pytest.mark.parametrize("url,year,week", [
    (URL,2026,9),(URL.replace("www.nfl.com","evil.test"),2026,8),
    (URL.replace("https:","http:"),2026,8),(URL,2025,8),
])
def test_wrong_response_route_cannot_certify_requested_week(url,year,week):
    with pytest.raises(ValueError):
        parse_week(html(),url,year,week)


def test_selected_scope_contradiction_is_rejected():
    with pytest.raises(ValueError,match="selected scope"):
        parse_week(html().replace(b'reg8',b'reg9'),URL,2026,8)


@pytest.mark.parametrize("old,new", [
    (b'/teams/los-angeles-rams/',b'/teams/other-team/'),
    (b'logos/CIN',b'logos/TEN'),(b'>Rams</div>',b'>Bengals</div>'),
    (b'<th>Injuries</th>',b'<th>Other</th>'),
    (b'/players/rams-player/',b'https://evil.test/players/rams-player/'),
])
def test_ownership_and_schema_errors_remain_partial(old,new):
    assert parse_week(html().replace(old,new),URL,2026,8)["status"] == "PARTIAL_REPORT_CONTENTS"


def test_retrieval_hash_mismatch_is_unavailable(tmp_path):
    archive=tmp_path/'sources';archive.mkdir()
    path=archive/'fixture.html';path.write_bytes(html())
    result=recover(tmp_path/'output',archive,2026,[8],fetch=lambda u,a:dict(
        reference=URL,archive_file=str(path),sha256=hashlib.sha256(b'wrong').hexdigest()))
    assert result["report_count"] == 0
    assert result["weeks"][0]["status"] == "UNAVAILABLE"


def bundle(tmp_path):
    archive=tmp_path/'sources';archive.mkdir()
    path=archive/'fixture.html';path.write_bytes(html())
    source=dict(reference=URL,archive_file=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),observed_at='2026-11-04T00:00:00Z')
    recover(tmp_path/'bundle',archive,2026,[8],fetch=lambda u,a:source)
    artifact=tmp_path/'bundle/recovered-injuries.json'
    # These are synthetic source fixtures, not authenticated historical inputs.
    return artifact,source


def test_raw_report_replay_and_exact_game_attachment(tmp_path):
    path,_=bundle(tmp_path)
    reports=verified_historical_reports(path,hashlib.sha256(path.read_bytes()).hexdigest(),'2099-01-01T00:00:00Z')
    p=dict(team_id='CIN',as_of='2099-01-01T00:00:00Z',personnel={'players':[{'eligibility':'UNKNOWN'}]},
           game_observations=[dict(season=2026,week=8,opponent='LAR',game_id='fixture-game')])
    updated=attach_historical_reports(p,reports)
    attached=updated['personnel']['historical_official_injury_reports']
    assert len(attached)==1 and attached[0]['game_id']=='fixture-game'
    assert attached[0]['historical_pregame_version_verified'] is False
    assert updated['personnel']['players']==p['personnel']['players']
    assert 'historical_official_injury_reports' not in p['personnel']


@pytest.mark.parametrize('fault',['bundle_hash','raw_hash','cutoff','duplicate_scope','count'])
def test_bad_historical_evidence_is_rejected(tmp_path,fault):
    path,source=bundle(tmp_path)
    if fault=='raw_hash':
        from pathlib import Path
        Path(source['archive_file']).write_bytes(b'changed')
    data=json.loads(path.read_text())
    if fault=='duplicate_scope':data['weeks'].append(data['weeks'][0])
    if fault=='count':data['report_count']=3
    path.write_text(json.dumps(data))
    sha='wrong' if fault=='bundle_hash' else hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        verified_historical_reports(path,sha,'2020-01-01T00:00:00Z' if fault=='cutoff' else '2099-01-01T00:00:00Z')


def test_historical_report_cannot_attach_to_different_opponent_or_future_profile():
    report=dict(team_id='CIN',season=2026,week=8,opponent_id='LAR',source={'observed_at':'2026-11-04T00:00:00Z'})
    p=dict(team_id='CIN',as_of='2026-11-03T00:00:00Z',game_observations=[dict(season=2026,week=8,opponent='CHI',game_id='fixture')])
    with pytest.raises(ValueError,match='game identity'):
        attach_historical_reports(p,[report])
    p['game_observations'][0]['opponent']='LAR'
    with pytest.raises(ValueError,match='after profile cutoff'):
        attach_historical_reports(p,[report])


def test_nfl_alias_is_bound_to_exact_official_team_route():
    raw=html().replace(b'LAR',b'AZ').replace(b'logos/LA',b'logos/AZ').replace(b'los-angeles-rams',b'arizona-cardinals')
    result=parse_week(raw,URL,2026,8)
    assert result['reports'][0]['team_id']=='ARI'
    assert result['reports'][1]['opponent_id']=='ARI'
    assert parse_week(raw.replace(b'arizona-cardinals',b'wrong-club'),URL,2026,8)['errors']
