"""Offline adversarial fixtures; no source facts or runtime mutations."""
import hashlib
import json
from pathlib import Path

import pytest

from research.recover_current_injury_context import collect, parse_archive, parse_news, recover

AS_OF = '2026-11-04T16:00:00+00:00'
URL = 'https://www.baltimoreravens.com/team/injury-report/'


def source(tmp_path, raw, url=URL, **changes):
    raw = raw.encode()
    digest = hashlib.sha256(raw).hexdigest()
    archive = tmp_path / (digest + '.html')
    archive.write_bytes(raw)
    return dict(team_id='BAL', reference=url, resolved_reference=url, http_status=200,
                archive_file=str(archive), sha256=digest,
                observed_at='2026-11-04T15:00:00+00:00', **changes)


def table(logo='BAL', injury='', duplicate_anchor=True, player_href='/team/players-roster/fixture-player/', player='Fixture Player'):
    link = f'<a href="{player_href}">{player}</a>'
    headshot = f'<a href="{player_href}"><img alt="headshot"></a>' if duplicate_anchor else ''
    return f'''<div class="nfl-o-injury-report__container"><img src="https://static.www.nfl.com/clubs/logos/{logo}.svg">
    <table><thead><tr><th>Player</th><th>Position</th><th>Injury</th><th>Mon</th><th>Tue</th><th>Wed</th><th>Game Status</th></tr></thead>
    <tbody><tr><td>{headshot}{link}</td><td>QB</td><td>{injury}</td><td>LP</td><td>FP</td><td></td><td>UNSPECIFIED</td></tr></tbody></table></div>'''


def club(body=None, week=9, season=2026):
    year = f'<option selected value="{season}">{season}</option>' if season else ''
    return f'<select>{year}<option selected value="/team/injury-report/week/REG-{week}">Week {week}</option></select>' + (table() if body is None else body)


def parse(s):
    return parse_archive(s, 'BAL', 2026, 9, AS_OF)


def test_missing_injury_and_unspecified_are_retained(tmp_path):
    result = parse(source(tmp_path, club()))
    assert len(result['rows']) == 1
    row = result['rows'][0]
    assert row['columns']['Injury'] == ''
    assert row['columns']['Tue'] == 'FP'
    assert row['columns']['Game Status'] == 'UNSPECIFIED'
    assert row['missing_fields'] == ['Injury', 'Wed', 'Game Status']
    assert row['eligibility'] == 'UNKNOWN'
    assert result['report_scope']['practice_days'] == ['Mon', 'Tue', 'Wed']
    assert result['published_at'] is None
    assert not result['current_eligibility_usable']


def test_deduplicates_only_identical_headshot_and_text_targets(tmp_path):
    result = parse(source(tmp_path, club(table(injury='Ankle'))))
    assert result['rows'][0]['player_reference'].endswith('/fixture-player')
    assert not result['errors']


def test_opponent_only_is_not_own_report(tmp_path):
    result = parse(source(tmp_path, club(table(logo='JAX'))))
    assert result['rows'] == []
    assert result['status'] == 'CURRENT_REPORT_NOT_AVAILABLE'


@pytest.mark.parametrize('logo', ['BAL.svg"><img src="/clubs/logos/JAX', 'UNKNOWN'])
def test_ambiguous_or_unknown_logo_excluded(tmp_path, logo):
    assert not parse(source(tmp_path, club(table(logo=logo))))['rows']


@pytest.mark.parametrize('href', ['https://www.jaguars.com/team/players-roster/fixture-player/',
                                  'https://www.baltimoreravens.com.evil.test/team/players-roster/fixture-player/',
                                  '/team/players-roster/fixture-player/?x=1', '/news/fixture-player/'])
def test_foreign_or_non_player_identity_does_not_get_reference(tmp_path, href):
    result = parse(source(tmp_path, club(table(player_href=href))))
    assert result['rows'][0]['player_reference'] is None
    assert not result['completeness']['player_references_complete']


def test_missing_season_not_inferred_from_observation(tmp_path):
    result = parse(source(tmp_path, club(season=None)))
    assert result['report_scope']['season'] is None
    assert not result['completeness']['season_verified']


@pytest.mark.parametrize('week,season', [(8, 2026), (9, 2025)])
def test_previous_week_or_season_rejected(tmp_path, week, season):
    result = parse(source(tmp_path, club(week=week, season=season)))
    assert result['status'] == 'REJECTED'
    assert result['rows'] == []


@pytest.mark.parametrize('field,value', [
    ('sha256', '0'*64), ('observed_at', '2026-11-04T16:00:01+00:00'),
    ('observed_at', '2026-11-04T15:00:00'), ('http_status', 403),
    ('resolved_reference', 'https://www.jaguars.com/team/injury-report/'),
    ('resolved_reference', URL+'week/REG-8'),
])
def test_archive_clock_http_and_destination_gates(tmp_path, field, value):
    s = source(tmp_path, club()); s[field] = value
    assert parse(s)['status'] == 'REJECTED'


def test_hash_is_required_not_defaulted(tmp_path):
    s = source(tmp_path, club()); del s['sha256']
    assert parse(s)['status'] == 'REJECTED'


def test_multiple_owned_tables_not_silently_combined(tmp_path):
    assert parse(source(tmp_path, club(table()+table())))['status'] == 'REJECTED'


def test_schema_mismatch_cannot_appear_complete(tmp_path):
    result = parse(source(tmp_path, club(table().replace('<td>QB</td>', ''))))
    assert not result['rows']
    assert result['errors']


def test_distinct_links_remain_ambiguous(tmp_path):
    raw = club(table()).replace(
        '<a href="/team/players-roster/fixture-player/"><img', '<a href="/team/players-roster/another-player/"><img')
    assert parse(source(tmp_path, raw))['rows'][0]['player_reference'] is None


def test_available_daily_fields_do_not_require_unpublished_future_cells(tmp_path):
    result = parse(source(tmp_path, club(table(injury='Ankle'))))
    assert result['completeness']['published_row_fields_recovered']
    assert not result['completeness']['full_report_verified']
    assert not result['game_day_active_proof']


def article(published='2026-11-03T19:00:00Z', modified=None, title='Practice update'):
    data = dict(**{'@type': 'NewsArticle'}, datePublished=published)
    if modified:
        data['dateModified'] = modified
    return (f'<title>{title}</title><script type="application/ld+json">{json.dumps(data)}</script>'
            '<div class="nfl-c-body-part"><p>Fixture Player (ankle) was limited Tuesday.</p>'
            '<p>Opponent Runner (knee) did not practice.</p></div>')


def test_news_injury_keeps_quote_hash_and_real_owned_reference(tmp_path):
    players = parse(source(tmp_path, club()))['rows']
    s = source(tmp_path, article(), 'https://www.baltimoreravens.com/news/fixture-update')
    result = parse_news(s, 'BAL', 2026, 9, AS_OF, players)
    assert len(result['facts']) == 1
    fact = result['facts'][0]
    assert fact['injury'] == 'ankle'
    assert fact['source_sha256'] == s['sha256']
    assert fact['report_day'] is None
    assert result['reported_week'] is None
    assert result['status'] == 'SUPPLEMENTAL_NEWS_ONLY'
    assert players[0]['columns']['Injury'] == ''


@pytest.mark.parametrize('published,modified', [
    ('2026-11-04T17:00:00Z', None), ('2026-11-03T19:00:00', None),
    ('2026-11-03T19:00:00Z', '2026-11-04T17:00:00Z'),
    ('2026-11-03T19:00:00Z', '2026-11-03T18:00:00Z'),
])
def test_news_publication_and_modification_must_precede_observation(tmp_path, published, modified):
    s = source(tmp_path, article(published, modified), 'https://www.baltimoreravens.com/news/fixture')
    assert parse_news(s, 'BAL', 2026, 9, AS_OF)['status'] == 'REJECTED'


def test_old_news_is_historical_never_current(tmp_path):
    s = source(tmp_path, article(title='Week 8 Injury Report'), 'https://www.baltimoreravens.com/news/fixture')
    assert parse_news(s, 'BAL', 2026, 9, AS_OF)['status'] == 'HISTORICAL_NEWS_ONLY'


def test_ambiguous_publication_clock_rejected(tmp_path):
    raw = article() + '<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-11-02T19:00:00Z"}</script>'
    s = source(tmp_path, raw, 'https://www.baltimoreravens.com/news/fixture')
    assert parse_news(s, 'BAL', 2026, 9, AS_OF)['status'] == 'REJECTED'


def test_explicit_narrative_injury_is_separate_from_report(tmp_path):
    raw = article().replace('Fixture Player (ankle) was limited Tuesday.', 'Fixture Player, is dealing with an ankle injury.')
    s = source(tmp_path, raw, 'https://www.baltimoreravens.com/news/fixture')
    players = parse(source(tmp_path, club()))['rows']
    result = parse_news(s, 'BAL', 2026, 9, AS_OF, players)
    assert result['facts'][0]['injury'] == 'ankle'
    assert players[0]['columns']['Injury'] == ''


def test_empty_league_marker_is_not_health_evidence(tmp_path):
    url = 'https://www.nfl.com/injuries/league/2026/reg9'
    raw = '<select><option selected value="'+url+'">WEEK 9</option></select><p>No Injuries Reported</p>'
    result = parse(source(tmp_path, raw, url))
    assert result['status'] == 'CURRENT_REPORT_NOT_AVAILABLE'
    assert result['report_scope']['season'] == 2026
    assert not result['game_day_active_proof']


def test_league_selected_scope_cannot_cross_year(tmp_path):
    url = 'https://www.nfl.com/injuries/league/2026/reg9'
    raw = '<option selected value="/injuries/league/2025/reg9">WEEK 9</option>'
    assert parse(source(tmp_path, raw, url))['status'] == 'REJECTED'


def test_network_collector_no_redirects_and_observes_after_response(tmp_path):
    class Response:
        url = URL
        status_code = 302
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def iter_content(self, size): yield b'actual response bytes'
    calls = []
    def fake_get(url, **kwargs):
        calls.append(kwargs)
        return Response()
    records = collect([('BAL', URL), ('BAL', URL)], tmp_path, get=fake_get)
    assert len(calls) == 1
    assert calls[0]['allow_redirects'] is False
    assert records[0]['http_status'] == 302
    assert Path(records[0]['archive_file']).read_bytes() == b'actual response bytes'
    assert parse(records[0])['status'] == 'REJECTED'


def test_network_collector_byte_limit(tmp_path):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def iter_content(self, size): yield b'x'*3_000_001
    result = collect([('BAL', URL)], tmp_path, get=lambda *a, **k: Response())
    assert 'byte budget' in result[0]['error']
    assert not list(tmp_path.glob('*.html'))


def test_collection_budget_and_host_are_checked_before_network(tmp_path):
    def forbidden(*a, **kw):
        pytest.fail('network must not be called')
    with pytest.raises(ValueError, match='budget'):
        collect([('BAL', URL)]*3, tmp_path, limit=2, get=forbidden)
    with pytest.raises(ValueError, match='unapproved'):
        collect([('BAL', 'https://www.baltimoreravens.com.evil.test/')], tmp_path, get=forbidden)


def test_recovery_does_not_overwrite_existing_artifact(tmp_path):
    recover([], tmp_path, 2026, 9, as_of=AS_OF)
    before = (tmp_path/'recovered-context.json').read_bytes()
    with pytest.raises(FileExistsError):
        recover([], tmp_path, 2026, 9, as_of=AS_OF)
    assert (tmp_path/'recovered-context.json').read_bytes() == before
