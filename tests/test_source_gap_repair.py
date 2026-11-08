from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from research.source_gap_repair import injury_metadata, own_injury_rows, repair_injury, league_transaction_window, attach_reviewed_identities


def table(link):
    return f'<table><thead><tr><th>Player</th><th>Injury</th></tr></thead><tbody><tr><td>{link}</td><td>Knee</td></tr></tbody></table>'


def test_injury_parser_separates_opponent_and_preserves_own_report():
    raw = (table('<a href="/team/players-roster/one/">One</a>') +
           table('Opponent') + table('<a href="https://other.test/team/players-roster/two/">Two</a>')).encode()
    rows = own_injury_rows(raw, 'https://club.test/team/injury-report/')
    assert len(rows) == 1 and rows[0]['columns']['Player'] == 'One'


def test_empty_selected_week_keeps_history_separate_from_current(tmp_path, monkeypatch):
    import research.source_gap_repair as repair
    marker = '<select><option selected value="/team/injury-report/week/REG-9">Week 9</option></select>'
    current = tmp_path/'current.html'; current.write_text(marker)
    prior = tmp_path/'prior.html'; prior.write_text(marker.replace('REG-9','REG-8') + table('<a href="/team/players-roster/one/">One</a>'))
    monkeypatch.setattr(repair, 'fetch_page', lambda task,archive: dict(html_file=str(prior),rows=[],reference=task[2]))
    page = repair_injury(dict(team='HOU',reference='https://club.test/team/injury-report/',html_file=str(current),rows=[],status='UNPARSED'),tmp_path)
    assert page['rows'] == [] and page['eligibility'] == 'UNKNOWN'
    assert page['status'] == 'CURRENT_REPORT_NOT_AVAILABLE'
    assert len(page['historical_reports'][0]['rows']) == 1
    assert page['historical_reports'][0]['current_eligibility_usable'] is False


def test_legends_alone_do_not_prove_empty_report_or_healthy_roster():
    assert injury_metadata(b'<p>Legend: DNP, LP, FP</p>') == {}


def test_duplicate_headshot_and_name_links_are_one_player_identity():
    from research.source_gap_repair import own_injury_rows
    raw = ('<table><thead><tr><th>Player</th><th>Injury</th></tr></thead><tbody><tr><td>'
           '<a href="/team/players-roster/fixture/"><img alt="Fixture headshot"></a>'
           '<a href="/team/players-roster/fixture/">Fixture Player</a></td><td>Knee</td></tr></tbody></table>')
    rows = own_injury_rows(raw.encode(), 'https://www.jaguars.com/team/injury-report/')
    assert len(rows) == 1
    assert rows[0]['columns']['Player'] == 'Fixture Player'


def test_conflicting_headshot_and_name_links_remain_ambiguous():
    from research.source_gap_repair import own_injury_rows
    raw = ('<table><thead><tr><th>Player</th><th>Injury</th></tr></thead><tbody><tr><td>'
           '<a href="/team/players-roster/other/"><img alt="Other"></a>'
           '<a href="/team/players-roster/fixture/">Fixture Player</a></td><td>Knee</td></tr></tbody></table>')
    assert own_injury_rows(raw.encode(), 'https://www.jaguars.com/team/injury-report/') == []


def test_league_fallback_follows_next_page_and_all_categories(tmp_path):
    calls=[]
    def fetched(url, archive):
        calls.append(url)
        raw='<p>No Transactions Available</p>'
        if '/signings/' in url:
            row='<table><tbody><tr><td>--</td><td><a href="/teams/dallas-cowboys/">Cowboys</a></td><td>11/02</td><td><a href="/players/stable-id/">Player</a></td><td>QB</td><td>Practice Squad</td></tr></tbody></table>'
            raw=row.replace('stable-id/', 'second-id/') if '?after=' in url else row+'<a href="?after=cursor">Next Page</a>'
        path=tmp_path/f'{len(calls)}.html';path.write_text(raw)
        return dict(reference=url,archive_file=str(path))
    result=league_transaction_window(2026,11,tmp_path,fetch=fetched)
    assert len(calls)==7 and result['all_six_categories_complete']
    assert len(result['rows'])==2
    assert result['rows'][0]['player_reference']=='https://www.nfl.com/players/stable-id/'


def test_league_fallback_does_not_certify_unrecognized_empty_response(tmp_path):
    path=tmp_path/'blank.html';path.write_text('<html>Try again</html>')
    result=league_transaction_window(2026,11,tmp_path,fetch=lambda url,a:dict(reference=url,archive_file=str(path)))
    assert not result['all_six_categories_complete'] and len(result['errors'])==6


def test_repeated_pagination_is_incomplete_not_zero_verified_transactions(tmp_path):
    path=tmp_path/'cycle.html'
    path.write_text('<table><tbody><tr><td>--</td><td><a href="/teams/dallas-cowboys/">Cowboys</a></td><td>11/02</td><td><a href="/players/stable-id/">Player</a></td><td>QB</td><td>Practice Squad</td></tr></tbody></table><a href="?after=repeated">Next Page</a>')
    result=league_transaction_window(2026,11,tmp_path,fetch=lambda url,a:dict(reference=url,archive_file=str(path)))
    assert not result['all_six_categories_complete']
    assert len(result['errors']) == 6
    assert all(e=='transaction pagination repeated content' for e in result['errors'])


def test_identity_review_requires_exact_reference_biography_and_preserves_eligibility(tmp_path):
    p=dict(player_reference='https://club.test/team/players-roster/one/',position='QB',eligibility='REQUIRES_GAME_DAY_CONFIRMATION')
    r=dict(team_id='BAL',player_reference=p['player_reference'],position='QB',reviewed_at='2026-11-03T00:00:00+00:00',matched_biographical_fields=['college','age','height','position'],provider_identities={'gsis_id':'stable'},sources=[])
    for name in ['bio','crosswalk']:
        path=tmp_path/name;path.write_bytes(name.encode())
        r['sources'].append(dict(reference='fixture:'+name,archive_file=str(path),sha256=hashlib.sha256(name.encode()).hexdigest(),observed_at=r['reviewed_at']))
    attach_reviewed_identities([p],'BAL',[r],'2026-11-03T01:00:00+00:00')
    assert p['eligibility']=='REQUIRES_GAME_DAY_CONFIRMATION' and p['provider_identities']['gsis_id']=='stable'
    other=dict(player_reference='https://club.test/team/players-roster/another/',position='QB')
    attach_reviewed_identities([other],'BAL',[r],'2026-11-03T01:00:00+00:00')
    assert 'provider_identities' not in other
    stale=dict(p);stale.pop('provider_identities');stale.pop('identity_review')
    Path(r['sources'][0]['archive_file']).write_text('changed source')
    attach_reviewed_identities([stale],'BAL',[r],'2026-11-03T01:00:00+00:00')
    assert 'provider_identities' not in stale


def scope(week='REG-9', season=None):
    marker = f'<select><option selected value="/team/injury-report/week/{week}">Week</option></select>'
    if season:
        marker += f'<select><option selected value="{season}">Season</option></select>'
    return marker


def club_table(team='HOU', injury='Knee', logo_attribute='data-src'):
    return (f'<div class="nfl-o-injury-report__container"><div class="nfl-o-injury-report__club-logo">'
            f'<img {logo_attribute}="https://static.test/clubs/logos/{team}.svg"></div>'
            f'<table><thead><tr><th>Player</th><th>Injury</th><th>Wed</th></tr></thead>'
            f'<tbody><tr><td>Fixture Player</td><td>{injury}</td><td></td></tr></tbody></table></div>')


def injury_page(tmp_path, raw, **kwargs):
    path = tmp_path / 'injury.html'
    path.write_text(raw)
    return dict(team='HOU', reference='https://www.houstontexans.com/team/injury-report/',
                html_file=str(path), rows=[{'unvalidated': True}], status='parsed', **kwargs)


@pytest.mark.parametrize('attribute', ['data-src', 'data-srcset', 'src', 'srcset'])
def test_official_logo_proves_unlinked_own_team_not_opponent(tmp_path, attribute):
    page = injury_page(tmp_path, scope() + club_table(logo_attribute=attribute) + club_table('DAL'))
    result = repair_injury(page, tmp_path)
    assert result['status'] == 'parsed'
    assert len(result['rows']) == 1
    assert result['rows'][0]['ownership'] == 'official club table logo'
    assert result['rows'][0]['columns']['Wed'] == ''  # Future practice columns can be blank.


def test_missing_injury_in_owned_logo_table_remains_partial(tmp_path):
    page = injury_page(tmp_path, scope() + club_table() + club_table(injury=''))
    result = repair_injury(page, tmp_path)
    assert result['status'] == 'PARTIAL_REPORT'
    assert result['eligibility'] == 'UNKNOWN'
    assert len(result['rows']) == 1
    assert result['parse_errors'] == ['injury row contains missing fields']


def test_injury_header_variant_is_rejected_without_crash(tmp_path):
    page = injury_page(tmp_path, scope('PRE-2') + club_table().replace('<th>Injury</th>', '<th>Injury Report</th>'))
    result = repair_injury(page, tmp_path)
    assert result['rows'] == []
    assert result['parse_errors'] == ['unrecognized injury table headers']


def test_opponent_logo_overrides_same_origin_player_link():
    raw = club_table('DAL').replace('Fixture Player', '<a href="/team/players-roster/one/">One</a>')
    assert own_injury_rows(raw.encode(), 'https://www.houstontexans.com/team/injury-report/') == []


def test_ambiguous_club_logos_cannot_certify_own_table(tmp_path):
    raw = club_table().replace('</div><table>', '<img src="/clubs/logos/DAL.svg"></div><table>')
    result = repair_injury(injury_page(tmp_path, scope('PRE-2') + raw), tmp_path)
    assert not result['rows']
    assert 'injury table has ambiguous club ownership' in result['parse_errors']


@pytest.mark.parametrize('resolved', [
    'https://www.houstontexans.com/team/injury-report/week/REG-9',
    'https://www.houstontexans.com/team/injury-report/week/REG-9/',
])
def test_current_url_redirected_to_week_is_historical_only(tmp_path, resolved):
    result = repair_injury(injury_page(tmp_path, scope() + club_table(), resolved_reference=resolved), tmp_path)
    assert result['status'] == 'HISTORICAL_ONLY'
    assert result['rows'] == [] and result['eligibility'] == 'UNKNOWN'
    assert result['historical_reports'][0]['current_eligibility_usable'] is False


@pytest.mark.parametrize('marker', ['', scope('REG-8'), scope().replace('/team/', 'https://other.test/team/')])
def test_unverified_scope_never_returns_original_unfiltered_rows(tmp_path, marker):
    page = injury_page(tmp_path, marker + club_table())
    page['reference'] += 'week/REG-9'
    result = repair_injury(page, tmp_path)
    assert result['status'] == 'UNPARSED'
    assert result['rows'] == [] and result['eligibility'] == 'UNKNOWN'


def test_missing_archive_does_not_preserve_unvalidated_rows(tmp_path):
    page = {'reference': 'https://club.test/', 'rows': [{'opponent': True}], 'status': 'parsed'}
    result = repair_injury(page, tmp_path)
    assert result['rows'] == [] and result['eligibility'] == 'UNKNOWN'


def test_injury_hash_mismatch_rejects_changed_archive(tmp_path):
    page = injury_page(tmp_path, scope() + club_table(), content_sha256='0' * 64)
    result = repair_injury(page, tmp_path)
    assert result['status'] == 'UNPARSED' and result['rows'] == []


@pytest.mark.parametrize('older_scope', [scope('PRE-3', '2026'), scope('REG-3', '2025'),
                                      scope('REG-3', '2026').replace('/team/', 'https://other.test/team/')])
def test_historical_fallback_requires_phase_season_and_origin(tmp_path, monkeypatch, older_scope):
    import research.source_gap_repair as repair
    prior = tmp_path / 'prior.html'
    prior.write_text(older_scope + club_table())
    calls = []
    def fetched(task, archive):
        calls.append(task[2])
        return dict(html_file=str(prior), reference=task[2])
    monkeypatch.setattr(repair, 'fetch_page', fetched)
    result = repair_injury(injury_page(tmp_path, scope('REG-4', '2026')), tmp_path)
    assert result['historical_reports'] == [] and len(calls) == 2
    assert result['eligibility'] == 'UNKNOWN'


def test_metadata_duplicate_responsive_selects_and_conflicting_seasons():
    assert injury_metadata((scope() * 2).encode())['week'] == 9
    assert injury_metadata((scope(season='2026') + scope(season='2025')).encode()) == {}


@pytest.mark.parametrize('year', ['2025', '2027'])
def test_out_of_season_report_is_not_current_eligibility(tmp_path, year):
    page = injury_page(tmp_path, scope(season=year) + club_table(), retrieved_at='2026-11-03T00:00:00Z')
    result = repair_injury(page, tmp_path)
    assert result['rows'] == [] and result['status'] == 'HISTORICAL_ONLY'
    assert result['eligibility'] == 'UNKNOWN'


def test_january_report_preserves_prior_calendar_year_season(tmp_path):
    page = injury_page(tmp_path, scope('REG-18', '2026') + club_table(), retrieved_at='2027-01-03T00:00:00Z')
    assert repair_injury(page, tmp_path)['status'] == 'parsed'


def test_duplicate_identity_records_are_ambiguous_not_first_match(tmp_path):
    player, review = reviewed_identity(tmp_path)
    attach_reviewed_identities([player], 'BAL', [review, deepcopy(review)], '2026-11-03T01:00:00Z')
    assert 'provider_identities' not in player


def test_revalidated_identity_revokes_attachment_when_archive_changes(tmp_path):
    player, review = reviewed_identity(tmp_path)
    attach_reviewed_identities([player], 'BAL', [review], '2026-11-03T01:00:00Z')
    Path(review['sources'][0]['archive_file']).write_text('changed fixture evidence')
    attach_reviewed_identities([player], 'BAL', [review], '2026-11-03T01:00:00Z')
    assert 'provider_identities' not in player and 'identity_review' not in player


def reviewed_identity(tmp_path):
    player = dict(player_reference='https://club.test/team/players-roster/fixture/', position='QB', eligibility='UNKNOWN')
    review = dict(team_id='BAL', player_reference=player['player_reference'], position='QB',
                  reviewed_at='2026-11-03T00:00:00Z', matched_biographical_fields=['college', 'age', 'height'],
                  provider_identities={'gsis_id': 'fixture-id'}, sources=[])
    for name in ('bio', 'crosswalk'):
        path = tmp_path / name
        path.write_bytes(name.encode())
        review['sources'].append(dict(reference='fixture:' + name, archive_file=str(path),
                                      sha256=hashlib.sha256(name.encode()).hexdigest(),
                                      observed_at='2026-11-02T23:00:00Z'))
    return player, review


@pytest.mark.parametrize('fault', ['duplicate_fields', 'duplicate_sources', 'duplicate_reference', 'missing_archive',
                                 'naive_review', 'bad_observation', 'observed_after_review', 'empty_ids', 'missing_position'])
def test_invalid_review_evidence_cannot_attach_or_survive_revalidation(tmp_path, fault):
    player, review = reviewed_identity(tmp_path)
    cutoff = '2026-11-03T01:00:00Z'
    attach_reviewed_identities([player], 'BAL', [review], cutoff)
    assert player['provider_identities']['gsis_id'] == 'fixture-id'
    if fault == 'duplicate_fields':
        review['matched_biographical_fields'] = ['age', 'Age', ' age ']
    elif fault == 'duplicate_sources':
        review['sources'][1] = deepcopy(review['sources'][0])
    elif fault == 'duplicate_reference':
        review['sources'][1]['reference'] = review['sources'][0]['reference'] + '#duplicate'
    elif fault == 'missing_archive':
        review['sources'][0]['archive_file'] = str(tmp_path / 'missing')
    elif fault == 'naive_review':
        review['reviewed_at'] = '2026-11-03T00:00:00'
    elif fault == 'bad_observation':
        review['sources'][0]['observed_at'] = None
    elif fault == 'observed_after_review':
        review['sources'][0]['observed_at'] = '2026-11-03T00:30:00Z'
    elif fault == 'empty_ids':
        review['provider_identities'] = {}
    else:
        player.pop('position')
    attach_reviewed_identities([player], 'BAL', [review], cutoff)
    assert 'provider_identities' not in player and 'identity_review' not in player
    assert player['eligibility'] == 'UNKNOWN'


def test_identity_review_does_not_alias_mutable_evidence_and_rejects_invalid_cutoff(tmp_path):
    player, review = reviewed_identity(tmp_path)
    attach_reviewed_identities([player], 'BAL', [review], '2026-11-03T01:00:00Z')
    review['provider_identities']['gsis_id'] = 'changed'
    assert player['provider_identities']['gsis_id'] == 'fixture-id'
    attach_reviewed_identities([player], 'BAL', [review], '2026-11-03')
    assert 'identity_review' not in player and 'provider_identities' not in player


def transaction_row():
    return ('<table><tbody><tr><td>--</td><td><a href="/teams/dallas-cowboys/">Cowboys</a></td>'
            '<td>11/02</td><td><a href="/players/fixture-id/">Fixture Player</a></td>'
            '<td></td><td>Practice Squad</td></tr></tbody></table>')


def window_from_html(tmp_path, raw):
    path = tmp_path / 'transactions.html'
    path.write_text(raw)
    return league_transaction_window(2026, 11, tmp_path,
        fetch=lambda url, archive: dict(reference=url, archive_file=str(path)))


@pytest.mark.parametrize('destination', ['http://www.nfl.com', 'https://www.nfl.com:444',
                                      'https://other.test', 'https://user@www.nfl.com'])
def test_pagination_rejects_unsafe_origin_before_fetch(tmp_path, destination):
    raw = transaction_row() + f'<a href="{destination}/transactions/league/signings/2026/11?after=x">Next Page</a>'
    result = window_from_html(tmp_path, raw)
    assert not result['all_six_categories_complete']
    assert len(result['sources']) == 6
    assert set(result['errors']) == {'unexpected pagination destination'}


@pytest.mark.parametrize('raw', ['<table><tbody></tbody></table>', '<table><tr><td>Unexpected</td></tr></table>',
                              transaction_row().replace('/teams/dallas-cowboys/', 'https://other.test/teams/dallas-cowboys/'),
                              transaction_row().replace('/players/fixture-id/', 'https://other.test/players/fixture-id/')])
def test_malformed_transaction_responses_cannot_certify_complete(tmp_path, raw):
    result = window_from_html(tmp_path, raw)
    assert result['status'] == 'PARTIAL_OFFICIAL_LEAGUE_WINDOW'
    assert not result['all_six_categories_complete'] and result['rows'] == []


def test_optional_transaction_position_and_disabled_next_are_valid(tmp_path):
    result = window_from_html(tmp_path, transaction_row() + '<a href="?after=x" aria-disabled="true">Next Page</a>')
    assert result['all_six_categories_complete'] and len(result['rows']) == 6


def test_pagination_repeated_rows_with_changing_markup_stay_partial(tmp_path):
    calls = []
    def fetched(url, archive):
        calls.append(url)
        raw = transaction_row() + f'<p>request {len(calls)}</p>'
        if '?' not in url:
            raw += '<a href="?after=cursor">Next Page</a>'
        path = tmp_path / f'{len(calls)}.html'
        path.write_text(raw)
        return dict(reference=url, archive_file=str(path))
    result = league_transaction_window(2026, 11, tmp_path, fetch=fetched)
    assert not result['all_six_categories_complete'] and len(result['rows']) == 6
    assert result['errors'] == ['transaction pagination repeated row'] * 6


@pytest.mark.parametrize('fault', ['redirect', 'ignored_cursor', 'hash', 'missing_archive', 'non_dictionary'])
def test_pagination_response_provenance_is_revalidated(tmp_path, fault):
    path = tmp_path / 'response.html'
    path.write_text(transaction_row() + '<a href="?after=cursor">Next Page</a>')
    def fetched(url, archive):
        source = dict(reference=url, archive_file=str(path))
        if fault == 'redirect':
            source['resolved_reference'] = 'https://other.test/transactions/'
        elif fault == 'ignored_cursor':
            source['reference'] = url.split('?')[0]
        elif fault == 'hash':
            source['sha256'] = '0' * 64
        elif fault == 'missing_archive':
            source.pop('archive_file')
        else:
            return None
        return source
    result = league_transaction_window(2026, 11, tmp_path, fetch=fetched)
    assert not result['all_six_categories_complete'] and len(result['errors']) == 6
