"""Official-source fallbacks with explicit current versus historical coverage."""
import re
import hashlib
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from .collect_coaching import collect_evidence
from .collect_team_context import fetch_page
from .source_based_parlays import DOMAINS


def _timestamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.utcoffset() is None:
        raise ValueError('Evidence timestamps must include a timezone')
    return stamp


def _valid_review(record, player, cutoff):
    try:
        reviewed = _timestamp(record['reviewed_at'])
        fields = record['matched_biographical_fields']
        identities = record['provider_identities']
        sources = record['sources']
        if (not player.get('position') or record['position'] != player['position'] or
                reviewed > cutoff or not isinstance(fields, list) or
                any(not isinstance(f, str) or not f.strip() for f in fields) or
                len({f.strip().lower() for f in fields}) < 3 or
                not isinstance(identities, dict) or not identities or
                any(not isinstance(k, str) or not k.strip() or
                    not isinstance(v, str) or not v.strip() for k, v in identities.items()) or
                not isinstance(sources, list) or len(sources) < 2):
            return False
        references, archives, digests = set(), set(), set()
        for source in sources:
            observed = _timestamp(source['observed_at'])
            reference = source['reference']
            path = Path(source['archive_file']).resolve(strict=True)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if (observed > reviewed or observed > cutoff or
                    not isinstance(reference, str) or not reference.strip() or
                    digest != source['sha256']):
                return False
            references.add(reference.split('#', 1)[0].rstrip('/'))
            archives.add(path)
            digests.add(digest)
        return min(len(references), len(archives), len(digests)) >= 2
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        return False


def attach_reviewed_identities(players, team, records, as_of):
    records = records if isinstance(records, list) else []
    try:
        cutoff = _timestamp(as_of)
    except (TypeError, ValueError, AttributeError):
        cutoff = None
    for player in players:
        # A previous attachment must not survive a failed revalidation.
        if 'identity_review' in player:
            player.pop('identity_review', None)
            player.pop('provider_identities', None)
        matches = [r for r in records if isinstance(r, dict) and r.get('team_id') == team and
                   player.get('player_reference') and
                   r.get('player_reference') == player['player_reference']]
        if cutoff is None or len(matches) != 1 or not _valid_review(matches[0], player, cutoff):
            continue
        record = matches[0]
        player['provider_identities'] = deepcopy(record['provider_identities'])
        player['identity_review'] = deepcopy(record)
        # Identity verification never changes game-day eligibility.
    return players


def injury_metadata(raw):
    soup = BeautifulSoup(raw, 'html.parser')
    selected = soup.select('select option[selected][value*="/injury-report/week/"]')
    references = {option['value'] for option in selected}
    if len(references) != 1:
        return {}
    reference = references.pop()
    match = re.search(r'/injury-report/week/(REG|PRE|POST)-(\d+)/?$', urlparse(reference).path)
    if not match or not 1 <= int(match[2]) <= {'REG': 18, 'PRE': 4, 'POST': 5}[match[1]]:
        return {}
    meta = {'phase': match[1], 'week': int(match[2]), 'selected_reference': reference}
    seasons = soup.select('select option[selected][value]')
    years = {int(o['value']) for o in seasons if re.fullmatch(r'20\d{2}', o['value'])}
    if len(years) > 1:
        return {}
    if len(years) == 1:
        meta['season'] = years.pop()
    return meta


def own_injury_rows(raw, url):
    """Keep only club-linked players or rows in a table with a verified club logo."""
    return _injury_rows(raw, url)[0]


def _injury_rows(raw, url):
    soup = BeautifulSoup(raw, 'html.parser')
    own, errors = [], []
    host = urlparse(url).hostname
    expected = next((team for team, domain in DOMAINS.items()
                     if host in {domain + '.com', 'www.' + domain + '.com'}), None)
    for index, table in enumerate(soup.find_all('table')):
        container = table.find_parent(class_='nfl-o-injury-report__container')
        reported = set()
        if container:
            for node in container.select('.nfl-o-injury-report__club-logo'):
                for image in [node, *node.select('[data-src], [data-srcset], [src], [srcset]')]:
                    for attribute in ('data-src', 'data-srcset', 'src', 'srcset'):
                        reported.update(re.findall(r'/clubs/logos/([A-Z]+)(?=[/.,?\s]|$)',
                                                   image.get(attribute, '')))
        owned = expected is not None and reported == {expected}
        if expected and reported and not owned:
            if expected in reported:
                errors.append('injury table has ambiguous club ownership')
            continue
        header_row = table.find('tr')
        headers = [x.get_text(' ', strip=True) for x in table.select('thead th')]
        if not headers and header_row:
            headers = [x.get_text(' ', strip=True) for x in header_row.find_all('th')]
        normalized = [h.lower() for h in headers]
        if not any('injury' in h for h in normalized):
            continue
        heading = table.find_previous(['h2', 'h3', 'h4'])
        section = heading.get_text(' ', strip=True) if heading else 'unknown'
        if 'opponent' in section.lower():
            continue
        if (normalized.count('player') != 1 or normalized.count('injury') != 1 or
                len(set(normalized)) != len(headers)):
            errors.append('unrecognized injury table headers')
            continue
        for tr in table.find_all('tr'):
            cells = tr.find_all('td', recursive=False)
            if not cells:
                continue
            if len(cells) != len(headers):
                errors.append('injury row schema changed')
                continue
            cell = cells[normalized.index('player')]
            # Club templates link both the headshot and text to the same player.
            # Count distinct resolved identities, not decorative duplicate anchors.
            links = list({urljoin(url, a['href']): a for a in cell.find_all('a', href=True)}.values())
            values = [c.get_text(' ', strip=True) for c in cells]
            if not values[normalized.index('player')] or not values[normalized.index('injury')]:
                errors.append('injury row contains missing fields')
                continue
            if not links:
                if not owned:
                    errors.append('injury row ownership unverified')
                    continue
                own.append({'section': section, 'columns': dict(zip(headers, values)), 'table_index': index,
                            'ownership': 'official club table logo', 'player_reference': None})
                continue
            if len(links) != 1:
                errors.append('injury row player reference ambiguous')
                continue
            link = urlparse(urljoin(url, links[0]['href']))
            if link.hostname != urlparse(url).hostname:
                if owned:
                    errors.append('injury row contradicts club ownership')
                continue
            if (link.scheme not in ('http', 'https') or link.username or link.password or
                    link.netloc != urlparse(url).netloc or
                    not re.fullmatch(r'/team/players-roster/[^/]+/?', link.path)):
                errors.append('injury row ownership unverified')
                continue
            own.append({'section': section, 'columns': dict(zip(headers, values)), 'table_index': index})
    return own, errors


def repair_injury(page, archive):
    if not page.get('html_file'):
        return {**page, 'rows': [], 'eligibility': 'UNKNOWN',
                'status': 'FAILED' if page.get('status') == 'FAILED' else 'UNPARSED'}
    try:
        raw = Path(page['html_file']).read_bytes()
    except (OSError, TypeError, ValueError):
        return {**page, 'rows': [], 'status': 'UNPARSED', 'eligibility': 'UNKNOWN',
                'reason': 'Archived injury report is unavailable.'}
    digest = hashlib.sha256(raw).hexdigest()
    if page.get('content_sha256', digest) != digest:
        return {**page, 'rows': [], 'status': 'UNPARSED', 'eligibility': 'UNKNOWN',
                'reason': 'Archived injury report hash mismatch.'}
    meta = injury_metadata(raw)
    url = page.get('resolved_reference', page['reference'])
    origin = urlparse(page['reference'])
    resolved_page = urlparse(url)
    if (origin.hostname != resolved_page.hostname or resolved_page.scheme not in ('http', 'https') or
            resolved_page.username or resolved_page.password):
        return {**page, 'rows': [], 'status': 'UNPARSED', 'eligibility': 'UNKNOWN',
                'reason': 'Injury report redirected outside the reporting club.'}
    own, errors = _injury_rows(raw, url)
    selected = urlparse(urljoin(url, meta.get('selected_reference', '')))
    requested = urlparse(page['reference'])
    explicit_week = '/injury-report/week/' in requested.path or '/injury-report/week/' in resolved_page.path
    scope_valid = bool(meta) and selected.netloc == resolved_page.netloc and selected.scheme == resolved_page.scheme
    scope_path = requested.path if '/injury-report/week/' in requested.path else resolved_page.path
    if explicit_week and selected.path.rstrip('/') != scope_path.rstrip('/'):
        scope_valid = False
    historical_season = False
    if meta.get('season') and page.get('retrieved_at'):
        try:
            retrieved = _timestamp(page['retrieved_at'])
            season = retrieved.year - (retrieved.month <= 2)
            historical_season = meta['season'] != season
        except (ValueError, TypeError, AttributeError):
            scope_valid = False
    if own and scope_valid:
        if explicit_week or historical_season:
            return {**page, 'rows': [], 'status': 'HISTORICAL_ONLY', 'report_scope': meta,
                    'eligibility': 'UNKNOWN', 'historical_reports': [
                        {**page, 'rows': own, 'report_scope': meta, 'status': 'HISTORICAL_ONLY',
                         'parse_errors': errors, 'current_eligibility_usable': False}]}
        return {**page, 'rows': own, 'status': 'PARTIAL_REPORT' if errors else 'parsed',
                'report_scope': meta, 'parse_errors': errors,
                'eligibility': 'UNKNOWN' if errors else 'REQUIRES_GAME_DAY_CONFIRMATION'}
    if not scope_valid:
        return {**page, 'rows': [], 'status': 'UNPARSED', 'report_scope': meta,
                'eligibility': 'UNKNOWN', 'reason': 'Injury report scope is unverified.'}
    # An empty selected week is observable; it does not prove healthy players,
    # no injuries, or a definitive reason the publisher has not supplied rows.
    result = {**page, 'rows': [], 'status': 'CURRENT_REPORT_NOT_AVAILABLE',
              'report_scope': meta, 'eligibility': 'UNKNOWN',
              'reason': 'Selected official week has no report rows; publication or source completeness is unconfirmed.',
              'historical_reports': [], 'parse_errors': errors}
    if meta['phase'] != 'REG':
        return result
    for week in range(meta['week'] - 1, max(0, meta['week'] - 3), -1):
        url = urljoin(page['reference'], f'/team/injury-report/week/REG-{week}')
        older = fetch_page((page['team'], 'injury', url), archive)
        if older.get('html_file') and not older.get('error'):
            try:
                history = Path(older['html_file']).read_bytes()
            except (OSError, TypeError, ValueError):
                continue
            history_digest = hashlib.sha256(history).hexdigest()
            if older.get('content_sha256', history_digest) != history_digest:
                continue
            resolved = older.get('resolved_reference', url)
            if (urlparse(resolved).netloc != urlparse(url).netloc or
                    urlparse(resolved).path.rstrip('/') != urlparse(url).path.rstrip('/')):
                continue
            older['rows'], older['parse_errors'] = _injury_rows(history, resolved)
            older['report_scope'] = injury_metadata(history)
            scope = older['report_scope']
            if (older['rows'] and scope.get('week') == week and scope.get('phase') == meta['phase'] and
                    scope.get('season') == meta.get('season') and
                    urlparse(urljoin(url, scope.get('selected_reference', ''))).netloc == urlparse(url).netloc and
                    urlparse(urljoin(url, scope.get('selected_reference', ''))).path.rstrip('/') == urlparse(url).path.rstrip('/')):
                result['historical_reports'].append({**older, 'status': 'HISTORICAL_ONLY',
                    'current_eligibility_usable': False})
                break
    return result


def _pagination_key(url):
    parsed = urlparse(url)
    return parsed._replace(query=urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True))),
                           fragment='').geturl()


def _transaction_url_valid(url, path):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (parsed.scheme == 'https' and parsed.netloc == 'www.nfl.com' and
            parsed.path == path and not parsed.fragment)


def league_transaction_window(season, month, archive, fetch=collect_evidence):
    """Complete paginated official categories for a bounded calendar window."""
    sources, rows, errors = [], [], []
    categories = ('trades', 'signings', 'reserve-list', 'waivers', 'terminations', 'other')
    for category in categories:
        url = f'https://www.nfl.com/transactions/league/{category}/{season}/{month}'
        seen, contents, row_signatures = set(), set(), set()
        expected_path = urlparse(url).path
        for _ in range(30):
            key = _pagination_key(url)
            if key in seen:
                errors.append('transaction pagination cycle'); break
            seen.add(key)
            try:
                source = fetch(url, archive)
            except (OSError, ValueError) as exc:
                errors.append(f'transaction fetch failed: {exc}'); break
            sources.append(source)
            if not isinstance(source, dict):
                errors.append('invalid transaction source'); break
            if source.get('error'):
                errors.append(source['error']); break
            resolved = source.get('resolved_reference', source.get('reference', ''))
            if (not _transaction_url_valid(resolved, expected_path) or
                    _pagination_key(resolved) != key):
                errors.append('unexpected transaction response destination'); break
            try:
                raw = Path(source['archive_file']).read_bytes()
            except (KeyError, TypeError, ValueError, OSError):
                errors.append('transaction archive unavailable'); break
            digest = hashlib.sha256(raw).hexdigest()
            if source.get('sha256', digest) != digest:
                errors.append('transaction archive hash mismatch'); break
            if digest in contents:
                errors.append('transaction pagination repeated content'); break
            contents.add(digest)
            soup = BeautifulSoup(raw, 'html.parser')
            tables = soup.find_all('table')
            empty = any(text.strip() == 'No Transactions Available' for text in soup.stripped_strings)
            data_rows = [tr for table in tables for tr in table.find_all('tr') if tr.find('td')]
            if not data_rows and not empty:
                errors.append('unrecognized transaction response'); break
            if data_rows and empty:
                errors.append('conflicting transaction empty marker')
            for tr in data_rows:
                cells = tr.find_all('td', recursive=False)
                if len(cells) != 6:
                    errors.append('transaction schema changed'); continue
                team_links = [urlparse(urljoin(url, a['href'])) for cell in cells[:2]
                              for a in cell.find_all('a', href=True)]
                player_links = cells[3].find_all('a', href=True)
                player_url = urljoin(url, player_links[0]['href']) if len(player_links) == 1 else ''
                player = urlparse(player_url)
                if (not team_links or any(t.scheme != 'https' or t.netloc != 'www.nfl.com' or
                                          not re.fullmatch(r'/teams/[^/]+/?', t.path) for t in team_links) or
                        player.scheme != 'https' or player.netloc != 'www.nfl.com' or
                        not re.fullmatch(r'/players/[^/]+/?', player.path) or
                        not all(cells[i].get_text(' ', strip=True) for i in (0, 1, 2, 3, 5))):
                    errors.append('transaction row identity or fields unverified'); continue
                teams = {t.path.rstrip('/') for t in team_links}
                signature = tuple(c.get_text(' ', strip=True) for c in cells) + (player_url,)
                if signature in row_signatures:
                    errors.append('transaction pagination repeated row'); continue
                row_signatures.add(signature)
                if '/teams/dallas-cowboys' in teams:
                    rows.append({'category': category,
                        'columns': [c.get_text(' ', strip=True) for c in cells],
                        'player_reference': player_url,
                        'source': source})
            next_urls = {urljoin(url, a['href']) for a in soup.find_all('a', href=True)
                         if (a.get_text(' ', strip=True) == 'Next Page' or 'next' in a.get('rel', [])) and
                         a.get('aria-disabled') != 'true' and 'disabled' not in a.get('class', [])}
            if not next_urls:
                break
            if len(next_urls) != 1:
                errors.append('ambiguous transaction pagination'); break
            next_url = next_urls.pop()
            if not _transaction_url_valid(next_url, expected_path):
                errors.append('unexpected pagination destination'); break
            url = next_url
        else:
            errors.append('transaction pagination limit reached')
    return {'status': 'PARSED_OFFICIAL_LEAGUE_WINDOW' if not errors else 'PARTIAL_OFFICIAL_LEAGUE_WINDOW',
            'season': season, 'month': month, 'rows': rows, 'sources': sources,
            'errors': errors, 'all_six_categories_complete': not errors,
            'coverage': 'Calendar month only; not full-season transaction history or game-day eligibility'}
