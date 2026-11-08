import hashlib
from pathlib import Path

import pytest

from research.dallas_transaction_recovery import (
    CATEGORIES, CLUB, NFL, TRACKER, archive_fetch, collect_tracker, collect_window, parse_page,
)


def page(category="signings", month=11, rows=None, next_link="", empty=False):
    path = f"/transactions/league/{category}/2026/{month}"
    import calendar
    controls = (f'<select><option selected value="{path}">2026</option></select>'
                f'<select><option selected value="{path}">{calendar.month_name[month]}</option></select>'
                f'<a aria-label="{category} selected" href="{path}">Category</a>')
    body = '<h2>No Transactions Available</h2>' if empty else (
        '<table><thead><tr>' + ''.join(f'<th>{h}</th>' for h in
        ["From", "To", "Date", "Name", "Position", "Transaction"]) + '</tr></thead><tbody>'
        + (row(month=month) if rows is None else rows) + '</tbody></table>')
    return controls + '<div class="nfl-c-transactions-report">' + body + next_link + '</div>'


def row(month=11, team="dallas-cowboys", name="fixture", day=2):
    return (f'<tr><td>--</td><td><a href="/teams/{team}/">Club</a></td>'
            f'<td>{month:02}/{day:02}</td><td><a href="/players/{name}/">{name}</a></td>'
            '<td></td><td>Practice Squad</td></tr>')


def source(tmp_path, url, raw, index=0):
    raw = raw.encode()
    path = tmp_path / f"{index}.html"
    path.write_bytes(raw)
    return dict(reference=url, resolved_reference=url, status_code=200,
                retrieved_at="2026-11-04T00:00:00+00:00", archive_file=str(path),
                sha256=hashlib.sha256(raw).hexdigest())


def fetcher(tmp_path, build):
    calls = []
    def fetch(url, archive):
        calls.append(url)
        category = url.split('/')[5]
        return source(tmp_path, url, build(category, url, len(calls)), len(calls))
    return fetch, calls


def test_published_cursor_recovery_preserves_team_player_and_provenance(tmp_path):
    def build(c, u, n):
        second = '?' in u
        return page(c, rows=row(name="second" if second else "first"),
                    next_link='' if second else '<a href="?after=publisher-token">Next Page</a>')
    fetch, calls = fetcher(tmp_path, build)
    result = collect_window(2026, 11, tmp_path, fetch)
    assert len(calls) == 12 and len(result['rows']) == 12
    assert result['all_six_nonempty_windows_exhausted']
    assert not result['season_history_complete'] and not result['runtime_eligible']
    r = result['rows'][1]
    assert r['player_reference'] == NFL + '/players/second/'
    assert r['team_references'] == [NFL + '/teams/dallas-cowboys']
    assert r['source']['sha256'] == hashlib.sha256(Path(r['source']['archive_file']).read_bytes()).hexdigest()


@pytest.mark.parametrize('changing_markup', [False, True])
def test_repeated_pages_cannot_certify_complete(tmp_path, changing_markup):
    def build(c, u, n):
        return page(c, next_link='<a href="?after=publisher">Next Page</a>') + (str(n) if changing_markup else '')
    fetch, calls = fetcher(tmp_path, build)
    result = collect_window(2026, 11, tmp_path, fetch)
    expected = 'pagination_repeated_rows' if changing_markup else 'pagination_repeated_content'
    assert [c['errors'] for c in result['categories']] == [[expected]] * 6
    assert len(result['rows']) == 6 and len(calls) == 12
    assert not result['all_six_categories_complete']


@pytest.mark.parametrize('empty', [True, False])
def test_empty_marker_or_empty_table_never_certifies_zero(tmp_path, empty):
    fetch, _ = fetcher(tmp_path, lambda c, *_: page(c, empty=empty, rows=''))
    result = collect_window(2026, 11, tmp_path, fetch)
    assert result['rows'] == [] and not result['all_six_nonempty_windows_exhausted']
    assert all(c['errors'] for c in result['categories'])


@pytest.mark.parametrize('fault', ['month', 'category', 'team', 'player', 'date', 'headers', 'blank_team'])
def test_schema_scope_and_identity_fail_closed(tmp_path, fault):
    def build(c, *_):
        raw = page(c)
        if fault == 'month': return raw.replace('>November<', '>October<')
        if fault == 'category': return raw.replace(' selected"', ' wrong"')
        if fault == 'team': return raw.replace('/teams/dallas-cowboys/', 'https://other.test/teams/dallas-cowboys/')
        if fault == 'player': return raw.replace('/players/fixture/', 'https://other.test/players/fixture/')
        if fault == 'date': return raw.replace('11/02', '10/02')
        if fault == 'headers': return raw.replace('<th>Name</th>', '<th>Unknown</th>')
        return raw.replace('<td>--</td>', '<td>Unknown</td>')
    fetch, _ = fetcher(tmp_path, build)
    result = collect_window(2026, 11, tmp_path, fetch)
    assert result['rows'] == [] and all(c['errors'] for c in result['categories'])


@pytest.mark.parametrize('href', ['https://other.test/x', 'http://www.nfl.com/x',
    'https://www.nfl.com:444/x', 'https://user@www.nfl.com/x', '?page=2', '?after=',
    '?after=a&after=b', '/transactions/league/signings/2026/10?after=x', '?after=x#fragment'])
def test_unsafe_pagination_not_fetched(tmp_path, href):
    fetch, calls = fetcher(tmp_path, lambda c, *_: page(c, next_link=f'<a href="{href}">Next Page</a>'))
    result = collect_window(2026, 11, tmp_path, fetch)
    assert len(calls) == 6 and all(c['errors'] for c in result['categories'])


@pytest.mark.parametrize('fault', ['hash', 'redirect', 'ignored_query', 'missing_archive', 'status', 'timestamp'])
def test_provenance_revalidated(tmp_path, fault):
    def fetch(url, archive):
        c = url.split('/')[5]
        s = source(tmp_path, url, page(c, next_link='<a href="?after=x">Next Page</a>'))
        if fault == 'hash': s['sha256'] = '0' * 64
        if fault == 'redirect': s['resolved_reference'] = 'https://other.test/'
        if fault == 'ignored_query' and '?' in url: s['resolved_reference'] = url.split('?')[0]
        if fault == 'missing_archive': s['archive_file'] = str(tmp_path/'missing')
        if fault == 'status': s['status_code'] = 404
        if fault == 'timestamp': s['retrieved_at'] = '2026-11-04T00:00:00'
        return s
    result = collect_window(2026, 11, tmp_path, fetch)
    assert all(c['errors'] for c in result['categories'])
    assert not result['all_six_nonempty_windows_exhausted']


def test_page_budget_no_fabricated_next_parameters(tmp_path):
    fetch, calls = fetcher(tmp_path, lambda c, u, n: page(c, rows=row(name=f'p{n}'),
                       next_link=f'<a href="?after=published{n}">Next Page</a>'))
    result = collect_window(2026, 11, tmp_path, fetch, max_pages=2)
    assert len(calls) == 12
    assert all(c['errors'] == ['page_limit_reached'] for c in result['categories'])


def test_non_dallas_rows_do_not_turn_into_dallas_events(tmp_path):
    fetch, _ = fetcher(tmp_path, lambda c, *_: page(c, rows=row(team='buffalo-bills')))
    result = collect_window(2026, 11, tmp_path, fetch)
    assert result['rows'] == []
    assert not result['all_six_categories_complete']


def test_dallas_departure_matches_from_link():
    raw = page(rows=row().replace('<td>--</td>', '<td><a href="/teams/dallas-cowboys/">DAL</a></td>')
               .replace('/teams/dallas-cowboys/">Club', '/teams/buffalo-bills/">Club'))
    result = parse_page(raw, NFL+'/transactions/league/signings/2026/11', 2026, 11, 'signings')
    assert len(result['rows']) == 1


def tracker():
    return ('<h2>News - Regular Season | 2026</h2><div class="nfl-c-article__dates">Oct 29, 2026 at 04:20 PM</div>'
            '<div class="nfl-c-body-part"><table><tr><td>Transactions</td></tr>'
            '<tr><td>Oct. 20 | Signed DB A and DB B to the practice squad.</td></tr>'
            '<tr><td>Sept. 12 | Moved RB C to injured reserve</td></tr></table></div>')


def test_tracker_preserves_literal_multi_player_statement_and_month(tmp_path):
    s = source(tmp_path, TRACKER, tracker())
    result = collect_tracker(2026, 10, tmp_path, fetch=lambda *_: s)
    assert len(result['rows']) == 1 and not result['errors']
    assert result['rows'][0]['statement'] == 'Signed DB A and DB B to the practice squad.'
    assert result['rows'][0]['player_identity'] == 'UNRESOLVED'
    assert not result['season_history_complete']
    november = collect_tracker(2026, 11, tmp_path, fetch=lambda *_: s)
    assert november['rows'] == [] and november['status'] == 'PARTIAL_CLUB_NEWS'


@pytest.mark.parametrize('fault', ['year', 'publication', 'malformed'])
def test_tracker_cannot_invent_year_or_parse_free_prose(tmp_path, fault):
    raw = tracker()
    if fault == 'year': raw = raw.replace('Season | 2026', 'Season | 2025')
    if fault == 'publication': raw = raw.replace('Oct 29, 2026', 'Oct 29, 2025')
    if fault == 'malformed': raw = raw.replace('Oct. 20 |', 'Yesterday |')
    s = source(tmp_path, TRACKER, raw)
    result = collect_tracker(2026, 10, tmp_path, fetch=lambda *_: s)
    assert result['rows'] == [] and result['errors']


def test_http_failure_body_archived_without_redirect_or_retry(tmp_path, monkeypatch):
    calls = []
    class Response:
        url = CLUB + '/team/transactions/2026'
        status_code = 404
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield b'not found'
    def get(url, **kwargs):
        calls.append(kwargs)
        return Response()
    monkeypatch.setattr('research.dallas_transaction_recovery.requests.get', get)
    result = archive_fetch(Response.url, tmp_path)
    assert result['error'] == 'http_404'
    assert Path(result['archive_file']).read_bytes() == b'not found'
    assert len(calls) == 1 and calls[0]['allow_redirects'] is False
    assert len(list(tmp_path.glob('*.json'))) == 1


@pytest.mark.parametrize('pages', [0, 11])
def test_invalid_budget_makes_no_requests(tmp_path, pages):
    with pytest.raises(ValueError):
        collect_window(2026, 11, tmp_path, fetch=lambda *_: pytest.fail('Unexpected fetch'), max_pages=pages)


def test_tracker_rejects_cross_year_or_future_date_inference(tmp_path):
    raw = tracker().replace('Oct. 20 |', 'Dec. 20 |')
    result = collect_tracker(2026, 12, tmp_path, fetch=lambda *_: source(tmp_path, TRACKER, raw))
    assert result['rows'] == []
    assert result['errors'] == ['club_statement_after_publication_date']


def test_conflicting_empty_marker_rejects_rows(tmp_path):
    fetch, _ = fetcher(tmp_path, lambda c, *_: page(c).replace('</table>', '</table><h2>No Transactions Available</h2>'))
    result = collect_window(2026, 11, tmp_path, fetch)
    assert result['rows'] == [] and all(c['errors'] for c in result['categories'])


def test_ambiguous_pagination_rejected_before_fetch(tmp_path):
    fetch, calls = fetcher(tmp_path, lambda c, *_: page(c, next_link=(
        '<a href="?after=a">Next Page</a><a href="?after=b">Next Page</a>')))
    result = collect_window(2026, 11, tmp_path, fetch)
    assert len(calls) == 6
    assert all(c['errors'] == ['ambiguous_next_page'] for c in result['categories'])


def test_exhaustion_never_upgrades_typed_partial_coverage(tmp_path):
    fetch, _ = fetcher(tmp_path, lambda c, *_: page(c))
    result = collect_window(2026, 11, tmp_path, fetch)
    assert result['coverage_status'] == 'PARTIAL'
    assert result['all_six_nonempty_windows_exhausted']
    assert not result['all_six_categories_complete']
    assert result['missing_external_facts']
    assert all(c['recovery_action'] and c['missing_external_facts'] for c in result['categories'])


def test_tracker_typed_partial_discloses_inferred_year(tmp_path):
    result = collect_tracker(2026, 10, tmp_path, fetch=lambda *_: source(tmp_path, TRACKER, tracker()))
    assert result['coverage_status'] == 'PARTIAL'
    assert result['coverage_type'] == 'LATEST_CLUB_NEWS_STATEMENTS'
    assert result['rows'][0]['date_year_basis'] == 'Matching article year; row specifies month/day only'
    assert result['missing_external_facts'] and result['recovery_action']
