"""Bounded, archive-first injury context. No runtime writes or eligibility claims.

Publication, observation, report scope, and player identity are separate evidence.
Missing source cells remain missing; news supplements never patch report cells.
"""
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .source_based_parlays import DOMAINS

DAYS = {'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'}
MISSING = {'', '-', '--', '—', 'UNSPECIFIED'}
SLUGS = dict(zip(DOMAINS, (
    'buffalo-bills miami-dolphins new-england-patriots new-york-jets '
    'baltimore-ravens cincinnati-bengals cleveland-browns pittsburgh-steelers '
    'houston-texans indianapolis-colts jacksonville-jaguars tennessee-titans '
    'denver-broncos kansas-city-chiefs las-vegas-raiders los-angeles-chargers '
    'dallas-cowboys new-york-giants philadelphia-eagles washington-commanders '
    'chicago-bears detroit-lions green-bay-packers minnesota-vikings '
    'atlanta-falcons carolina-panthers new-orleans-saints tampa-bay-buccaneers '
    'arizona-cardinals los-angeles-rams san-francisco-49ers seattle-seahawks'
).split()))


def instant(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.utcoffset() is None:
        raise ValueError('timezone-aware evidence clock required')
    return stamp


def official(url, team):
    p = urlparse(url)
    return (p.scheme == 'https' and not p.username and not p.password
            and p.netloc in {DOMAINS[team] + '.com', 'www.' + DOMAINS[team] + '.com', 'www.nfl.com'}
            and not p.fragment)


def read_source(source, team, as_of):
    """Require a digest, successful response, exact destination, and honest clock."""
    if source.get('error') or source.get('http_status') != 200:
        raise ValueError('source retrieval failed')
    requested = source['reference']
    resolved = source['resolved_reference']
    a, b = urlparse(requested), urlparse(resolved)
    if (not official(requested, team) or not official(resolved, team)
            or a.netloc != b.netloc or a.path.rstrip('/') != b.path.rstrip('/') or a.query != b.query):
        raise ValueError('source destination or club ownership mismatch')
    if instant(source['observed_at']) > instant(as_of):
        raise ValueError('source observed after cutoff')
    raw = Path(source['archive_file']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != source['sha256']:
        raise ValueError('archive hash mismatch')
    return BeautifulSoup(raw, 'html.parser')


def publication(soup, source):
    records = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.string or '')
        except (ValueError, TypeError):
            continue
        records.extend(value if isinstance(value, list) else [value])
    articles = [r for r in records if isinstance(r, dict) and r.get('@type') == 'NewsArticle']
    dates = {r['datePublished'] for r in articles if r.get('datePublished')}
    modified = {r['dateModified'] for r in articles if r.get('dateModified')}
    if len(dates) > 1 or len(modified) > 1:
        raise ValueError('ambiguous publication clock')
    for value in dates | modified:
        if instant(value) > instant(source['observed_at']):
            raise ValueError('publication or modification after observation')
    if dates and modified and instant(next(iter(modified))) < instant(next(iter(dates))):
        raise ValueError('modification before publication')
    return next(iter(dates), None), next(iter(modified), None)


def _logos(container):
    return set(re.findall(r'/clubs/logos/([A-Z]+)(?=[/.,?\s"<]|$)', str(container)))


def _rows(table, source, team, league=False):
    headers = [x.get_text(' ', strip=True) for x in table.select('thead th')]
    normalized = [h.lower() for h in headers]
    injury = 'injuries' if league else 'injury'
    if (not headers or len(set(normalized)) != len(headers)
            or any(normalized.count(h) != 1 for h in ('player', 'position', injury))):
        return [], ['unrecognized injury table schema']
    rows, errors, seen = [], [], set()
    for index, tr in enumerate(table.select('tbody tr')):
        cells = tr.find_all('td', recursive=False)
        if len(cells) != len(headers):
            errors.append('injury row schema mismatch'); continue
        values = [c.get_text(' ', strip=True) for c in cells]
        refs = {urljoin(source['resolved_reference'], a['href']).rstrip('/')
                for a in cells[normalized.index('player')].select('a[href]')}
        ref = next(iter(refs), None) if len(refs) == 1 else None
        pattern = r'/players/[^/]+' if league else r'/team/players-roster/[^/]+'
        if ref and (not official(ref, team) or urlparse(ref).netloc != urlparse(source['resolved_reference']).netloc
                    or not re.fullmatch(pattern, urlparse(ref).path) or urlparse(ref).query):
            ref = None
        columns = dict(zip(headers, values))
        if not ref:
            errors.append('player reference missing or ambiguous')
        if not values[normalized.index('player')]:
            errors.append('player name missing'); continue
        key = ref or values[normalized.index('player')]
        if key in seen:
            errors.append('duplicate player row'); continue
        seen.add(key)
        missing = [h for h, v in columns.items() if v.upper() in MISSING]
        rows.append(dict(player_reference=ref, columns=columns, missing_fields=missing,
                         row_index=index, source_sha256=source['sha256'],
                         source_reference=source['reference'], eligibility='UNKNOWN'))
    return rows, errors


def parse_archive(source, team, season, week, as_of):
    """Recover only exactly owned tables; retain unsupported scope as unknown."""
    result = dict(team_id=team, source=source, requested_scope=dict(season=season, week=week),
                  report_scope=dict(season=None, week=None, phase=None, practice_days=[]),
                  published_at=None, modified_at=None, rows=[], errors=[],
                  current_eligibility_usable=False, game_day_active_proof=False,
                  historical_pregame_version_verified=False)
    try:
        soup = read_source(source, team, as_of)
        result['published_at'], result['modified_at'] = publication(soup, source)
        league = urlparse(source['resolved_reference']).netloc == 'www.nfl.com'
        tables = []
        if league:
            path = f'/injuries/league/{season}/reg{week}'
            selected = {urljoin(source['resolved_reference'], o['value']) for o in soup.select('option[selected][value]')
                        if '/injuries/league/' in o['value']}
            if urlparse(source['resolved_reference']).path != path or selected != {'https://www.nfl.com' + path}:
                raise ValueError('league season/week scope mismatch')
            result['report_scope'].update(season=season, week=week, phase='REG')
            for unit in soup.select('section.nfl-o-injury-report__unit'):
                identities = unit.select('.nfl-c-matchup-strip__team')
                unit_tables = unit.find_all('table')
                for identity, table in zip(identities, unit_tables) if len(identities) == len(unit_tables) == 2 else []:
                    link = identity.select_one('.nfl-c-matchup-strip__team-fullname')
                    abbr = identity.select_one('.nfl-c-matchup-strip__team-abbreviation')
                    expected_code = {'ARI': 'AZ', 'WSH': 'WAS'}.get(team, team)
                    logo = {'LA'} if team == 'LAR' else {expected_code}
                    title = table.find_previous(class_='nfl-t-stats__title')
                    if (link and abbr and abbr.get_text(strip=True) == expected_code and _logos(identity) == logo
                            and urljoin(source['resolved_reference'], link.get('href', '')).rstrip('/') == 'https://www.nfl.com/teams/' + SLUGS[team]
                            and title and title.find_parent('section') is unit
                            and title.get_text(' ', strip=True) == link.get_text(' ', strip=True)):
                        tables.append(table)
        else:
            options = {urljoin(source['resolved_reference'], o['value']) for o in soup.select('option[selected][value]')
                       if '/injury-report/week/' in o['value']}
            expected = f'https://{urlparse(source["resolved_reference"]).netloc}/team/injury-report/week/REG-{week}'
            if {x.rstrip('/') for x in options} != {expected}:
                raise ValueError('club selected week scope mismatch')
            path = urlparse(source['resolved_reference']).path.rstrip('/')
            if path not in {'/team/injury-report', urlparse(expected).path}:
                raise ValueError('unexpected club injury route')
            years = {int(o['value']) for o in soup.select('option[selected][value]') if re.fullmatch(r'20\d{2}', o['value'])}
            if len(years) > 1 or years and years != {season}:
                raise ValueError('club selected season mismatch')
            result['report_scope'].update(season=next(iter(years), None), week=week, phase='REG')
            for table in soup.find_all('table'):
                container = table.find_parent(class_='nfl-o-injury-report__container')
                # A player link on a club host alone cannot disambiguate opponent tables.
                if container and _logos(container) == {team}:
                    tables.append(table)
        if len(tables) > 1:
            raise ValueError('multiple owned report tables')
        for table in tables:
            result['rows'], result['errors'] = _rows(table, source, team, league)
            result['report_scope']['practice_days'] = [h.get_text(strip=True) for h in table.select('thead th')
                                                       if h.get_text(strip=True) in DAYS]
        result['report_scope']['populated_practice_days'] = [day for day in result['report_scope']['practice_days']
            if any(row['columns'].get(day, '') not in MISSING for row in result['rows'])]
        missing = sorted({f for r in result['rows'] for f in r['missing_fields']})
        result['completeness'] = dict(row_count=len(result['rows']), missing_columns=missing,
            player_references_complete=bool(result['rows']) and all(r['player_reference'] for r in result['rows']),
            season_verified=result['report_scope']['season'] == season,
            publication_verified=result['published_at'] is not None,
            report_day_verified=bool(result['report_scope']['populated_practice_days']),
            full_report_verified=False)
        result['completeness']['published_row_fields_recovered'] = bool(result['rows']) and not result['errors'] and all(
            r['player_reference'] and r['columns'].get('Position') not in MISSING
            and r['columns'].get('Injuries' if league else 'Injury') not in MISSING
            and any(r['columns'].get(day, '') not in MISSING for day in DAYS | {'Practice Status'})
            for r in result['rows'])
        result['status'] = 'PARTIAL_REPORT' if result['rows'] else 'CURRENT_REPORT_NOT_AVAILABLE'
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
        result.update(status='REJECTED', rows=[], errors=[str(exc)])
    return result


def parse_news(source, team, season, week, as_of, players=()):
    """Retain quoted context; parenthetical injuries require an owned player link.

    A date or a mention of a future Week 9 fixture does not certify a Week 9
    practice report. These supplemental facts never replace missing table cells.
    """
    result = dict(team_id=team, source=source, status='SUPPLEMENTAL_NEWS_ONLY', facts=[],
                  published_at=None, modified_at=None, reported_week=None,
                  requested_scope=dict(season=season, week=week),
                  current_eligibility_usable=False, full_report_verified=False)
    try:
        soup = read_source(source, team, as_of)
        if urlparse(source['resolved_reference']).netloc == 'www.nfl.com' or '/news/' not in urlparse(source['resolved_reference']).path:
            raise ValueError('news must be published by the exact club')
        result['published_at'], result['modified_at'] = publication(soup, source)
        if not result['published_at']:
            raise ValueError('news publication clock missing')
        title = soup.title.get_text(' ', strip=True) if soup.title else ''
        weeks = set(re.findall(r'\bWeek\s+(\d+)\b', title, re.I))
        result['reported_week'] = int(next(iter(weeks))) if len(weeks) == 1 else None
        result['published_season'] = instant(result['published_at']).year - (instant(result['published_at']).month <= 2)
        if result['published_season'] != season or result['reported_week'] not in {None, week}:
            result['status'] = 'HISTORICAL_NEWS_ONLY'
        paras = [p.get_text(' ', strip=True) for p in soup.select('.nfl-c-body-part p')]
        result['report_publication_notices'] = [p for p in paras if 'injury report' in p.lower() and 'will release' in p.lower()]
        for player in players:
            ref = player.get('player_reference')
            if not ref or not official(ref, team) or urlparse(ref).netloc not in {DOMAINS[team]+'.com', 'www.'+DOMAINS[team]+'.com'}:
                continue
            name = player['columns'].get('Player', '')
            if not name:
                continue
            for index, paragraph in enumerate(paras):
                match = re.search(re.escape(name) + r'\s*\(([^()]{1,80})\)', paragraph)
                if not match:
                    match = re.search(re.escape(name) + r',?\s+is dealing with an? ([a-z /-]{1,50}) injury', paragraph)
                if match:
                    result['facts'].append(dict(player_reference=ref, player=name,
                        injury=match[1], quotation=paragraph, paragraph_index=index,
                        source_sha256=source['sha256'], report_day=None,
                        eligibility='UNKNOWN', evidence_kind='NARRATIVE_NOT_REPORT_CELL'))
            headline = soup.select_one('h1')
            if headline:
                text = headline.get_text(' ', strip=True)
                match = re.search(re.escape(name) + r"['’]s ([A-Za-z -]{1,40}) Injury", text)
                if match:
                    result['facts'].append(dict(player_reference=ref, player=name, injury=match[1],
                        quotation=text, selector='h1', source_sha256=source['sha256'], report_day=None,
                        eligibility='UNKNOWN', evidence_kind='NARRATIVE_NOT_REPORT_CELL'))
        result['facts'] = list({(f['player_reference'], f['quotation']): f for f in result['facts']}.values())
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
        result.update(status='REJECTED', facts=[], errors=[str(exc)])
    return result


def collect(urls, output, *, limit=48, get=requests.get):
    """Fetch only explicit official URLs, once, with no redirects or pagination."""
    if not 1 <= limit <= 64 or len(urls) > limit:
        raise ValueError('retrieval budget exceeded')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for team, url in urls:
        if team not in DOMAINS or not official(url, team):
            raise ValueError('unapproved club source')
    for team, url in dict.fromkeys(tuple(task) for task in urls):
        record = dict(team_id=team, reference=url)
        try:
            with get(url, timeout=20, allow_redirects=False, stream=True) as response:
                raw = bytearray()
                for block in response.iter_content(65536):
                    raw.extend(block)
                    if len(raw) > 3_000_000:
                        raise ValueError('source exceeds byte budget')
                digest = hashlib.sha256(raw).hexdigest()
                archive = output / (digest + '.html')
                if archive.exists() and archive.read_bytes() != raw:
                    raise ValueError('existing archive differs')
                if not archive.exists():
                    archive.write_bytes(raw)
                record.update(resolved_reference=response.url, http_status=response.status_code,
                              sha256=digest, archive_file=str(archive.resolve()))
        except (requests.RequestException, OSError, ValueError) as exc:
            record['error'] = str(exc)
        record['observed_at'] = datetime.now(timezone.utc).isoformat()
        records.append(record)
    return records


def recover(manifest, output, season, week, *, as_of=None):
    """Offline comparison for parent integration; never touches a runtime profile."""
    as_of = as_of or datetime.now(timezone.utc).isoformat()
    instant(as_of)
    if not 2000 <= season <= 2100 or not 1 <= week <= 18 or len(manifest) > 64:
        raise ValueError('invalid bounded recovery scope')
    reports = []
    league = [s for s in manifest if urlparse(s.get('reference', '')).netloc == 'www.nfl.com']
    for team in DOMAINS:
        sources = [s for s in manifest if s.get('team_id') == team and '/team/injury-report' in s.get('reference', '')]
        reports.extend(parse_archive(s, team, season, week, as_of) for s in sources + league)
    news = []
    for s in manifest:
        if '/news/' in s.get('reference', '') and not s['reference'].endswith('/news/'):
            players = [r for report in reports if report['team_id'] == s['team_id'] for r in report['rows']]
            news.append(parse_news(s, s['team_id'], season, week, as_of, players))
    comparison = []
    for team in DOMAINS:
        own = [r for r in reports if r['team_id'] == team]
        comparison.append(dict(team_id=team, recovered_rows=max((len(r['rows']) for r in own), default=0),
            report_complete=False, status='STILL_INCOMPLETE',
            published_row_fields_recovered=any(r.get('completeness', {}).get('published_row_fields_recovered') for r in own),
            missing_external_facts=['complete current official report', 'verified report season/day/publication as applicable',
                                    'game-day eligibility and current roster/transaction reconciliation']))
    result = dict(as_of=as_of, season=season, week=week, reports=reports, news=news,
        comparison=comparison, repaired_complete_clubs=[],
        still_incomplete_clubs=list(DOMAINS), retrieval_count=len(manifest),
        automation_changed=False, runtime_state_changed=False, research_only=True)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'recovered-context.json'
    if path.exists():
        raise FileExistsError('preserve existing recovery output; choose a new output directory')
    path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--season', type=int, required=True)
    parser.add_argument('--week', type=int, required=True)
    args = parser.parse_args()
    result = recover(json.loads(args.manifest.read_text(encoding='utf-8')), args.output, args.season, args.week)
    print(json.dumps(dict(reports=len(result['reports']), still_incomplete=len(result['still_incomplete_clubs']))))
