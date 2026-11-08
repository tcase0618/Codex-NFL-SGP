"""Public, read-only NFL discovery and order-book quotes; no trading endpoints."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
from threading import Lock
from urllib.parse import urlencode
from uuid import uuid4

import requests

GAMMA = 'https://gamma-api.polymarket.com'
CLOB = 'https://clob.polymarket.com'
ALIASES = {'LAR': 'LA', 'WSH': 'WAS'}


def instant(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def decimal(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a price')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Invalid numeric value') from exc
    if not result.is_finite():
        raise ValueError('Nonfinite numeric value')
    return result


def american_odds(price):
    """Gross $1-payout share price to nearest whole American odds; .5 = +100."""
    p = decimal(price)
    if not 0 < p < 1:
        raise ValueError('Price must be strictly between zero and one')
    value = 100 * (1 - p) / p if p <= Decimal('.5') else -100 * p / (1 - p)
    rounded = int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return f'{rounded:+d}'


def array(value):
    result = json.loads(value) if isinstance(value, str) else value
    if not isinstance(result, list):
        raise ValueError('Expected outcome array')
    return result


def matching_event(event, game):
    """Require exact provider NFL team identities, orientation, and kickoff."""
    teams = event.get('teams', [])
    identities = {t.get('ordering'): str(t.get('abbreviation', '')).upper()
                  for t in teams if str(t.get('league', '')).lower() == 'nfl'}
    expected = {side: ALIASES.get(game[side], game[side]) for side in ('home', 'away')}
    return (len(teams) == 2 and identities == expected
            and abs((instant(event['startTime']) - instant(game['kickoff'])).total_seconds()) <= 60)


def book_quote(book, token_id, condition_id, now):
    if str(book.get('asset_id')) != token_id or book.get('market') != condition_id:
        raise ValueError('Order-book identity mismatch')
    stamp = datetime.fromtimestamp(float(decimal(book['timestamp']) / 1000), timezone.utc)
    age = (now - stamp).total_seconds()
    if age < -30:
        raise ValueError('Order-book timestamp is in the future')
    def levels(side):
        result = []
        for row in book.get(side, []):
            price, size = decimal(row['price']), decimal(row['size'])
            if not 0 < price < 1 or size < 0:
                raise ValueError('Invalid order-book level')
            if size > 0:
                result.append((price, size))
        return result
    asks, bids = levels('asks'), levels('bids')
    ask = min((p for p, _ in asks), default=None)
    bid = max((p for p, _ in bids), default=None)
    if ask is not None and bid is not None and bid >= ask:
        raise ValueError('Crossed or locked order book')
    return {'status': 'NO_ASK' if ask is None else 'STALE' if age > 120 else 'AVAILABLE',
            'american': american_odds(ask) if ask is not None else None,
            'share_price': str(ask) if ask is not None else None,
            'best_bid': str(bid) if bid is not None else None,
            'best_bid_american': american_odds(bid) if bid is not None else None,
            'ask_size_shares': str(sum(s for p, s in asks if p == ask)) if ask is not None else None,
            'book_timestamp': stamp.isoformat(), 'book_age_seconds': max(0, round(age, 3)),
            'basis': 'BEST_ASK_GROSS_BEFORE_FEES', 'fees_included': False}


class PolymarketOdds:
    def __init__(self, archive_dir=None, *, get=requests.get, clock=None):
        self.archive_dir = Path(archive_dir) if archive_dir else None
        self.get = get
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache = {}
        self.lock = Lock()

    def fetch(self, base, path, params, ttl):
        reference = base + path + '?' + urlencode(params)
        now = self.clock()
        with self.lock:
            cached = self.cache.get(reference)
            if cached and 0 <= (now - cached[0]).total_seconds() < ttl:
                return cached[1], cached[2]
        response = self.get(reference, timeout=(4, 8), allow_redirects=False)
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('Public market endpoint did not return 200')
        raw = response.content
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError('Public response exceeded size limit')
        data = json.loads(raw)
        observed = self.clock()
        digest = hashlib.sha256(raw).hexdigest()
        source = {'reference': reference, 'observed_at': observed.isoformat(), 'sha256': digest}
        if self.archive_dir:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            target = self.archive_dir / (digest + '.json')
            try:
                with target.open('xb') as stream:
                    stream.write(raw)
            except FileExistsError:
                if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise ValueError('Archived quote hash mismatch')
            source['archive_file'] = str(target.resolve())
        with self.lock:
            self.cache[reference] = (observed, data, source)
        return data, source

    def game_odds(self, game, market_type='all', page=0):
        if not isinstance(page, int) or not 0 <= page <= 200:
            raise ValueError('Invalid market page')
        if not re.fullmatch(r'[a-z0-9_]{1,70}', market_type):
            raise ValueError('Invalid market type')
        report = {'provider': 'Polymarket', 'game_id': game['game_id'], 'status': 'UNAVAILABLE',
                  'format': 'AMERICAN', 'markets': [], 'market_types': [], 'sources': [],
                  'page': page, 'page_size': 8, 'total_markets': 0, 'has_more': False,
                  'observed_at': self.clock().isoformat(), 'fee_basis': 'Gross odds before fees; best ask for buying an outcome.',
                  'sportsbook_equivalent': False, 'parlay_quote': None, 'betting_ready': False}
        try:
            if not game.get('kickoff_confirmed', True):
                raise ValueError('Game kickoff is unconfirmed')
            date = instant(game['kickoff']).date().isoformat()
            away, home = (ALIASES.get(game[k], game[k]).lower() for k in ('away', 'home'))
            slug = f'nfl-{away}-{home}-{date}'
            events, source = self.fetch(GAMMA, '/events', {'slug': slug}, 120)
            report['sources'].append(source)
            candidates = [e for e in events if e.get('slug') == slug and matching_event(e, game)]
            if len(candidates) != 1:
                raise ValueError('No unique NFL event matches both teams and kickoff')
            event = candidates[0]
            report.update(event_id=str(event['id']), event_slug=slug, event_url='https://polymarket.com/event/' + slug)
            if event.get('closed') is not False or event.get('active') is not True or event.get('archived') is True:
                raise ValueError('Event is closed or inactive; live odds unavailable')
            markets = [m for m in event.get('markets', []) if m.get('active') is True
                       and m.get('closed') is False and m.get('acceptingOrders') is True
                       and m.get('enableOrderBook') is True and not m.get('archived')]
            report['market_types'] = sorted({m.get('sportsMarketType') for m in markets if m.get('sportsMarketType')})
            if market_type != 'all':
                markets = [m for m in markets if m.get('sportsMarketType') == market_type]
            priority = {'moneyline': 0, 'spreads': 1, 'totals': 2}
            markets.sort(key=lambda m: (priority.get(m.get('sportsMarketType'), 3), -float(decimal(m.get('liquidity', 0))), str(m['id'])))
            report.update(total_markets=len(markets), has_more=(page + 1) * 8 < len(markets))
            jobs = []
            for market in markets[page * 8:(page + 1) * 8]:
                try:
                    outcomes, tokens = array(market['outcomes']), array(market['clobTokenIds'])
                    if not 1 <= len(outcomes) == len(tokens) <= 4 or len(set(map(str, tokens))) != len(tokens):
                        raise ValueError('Ambiguous outcome/token mapping')
                    if not all(isinstance(o,str) and o.strip() for o in outcomes) or len(set(outcomes)) != len(outcomes):
                        raise ValueError('Ambiguous outcome labels')
                    if not all(re.fullmatch(r'\d{1,100}', str(t)) for t in tokens):
                        raise ValueError('Invalid token identity')
                    item = {'market_id': str(market['id']), 'condition_id': market['conditionId'],
                            'question': market['question'], 'market_type': market.get('sportsMarketType'),
                            'line': market.get('line'), 'resolution_rules': market.get('description'),
                            'fees_enabled': market.get('feesEnabled'), 'fee_schedule': market.get('feeSchedule'),
                            'outcomes': [], 'source': source}
                    report['markets'].append(item)
                    for label, token in zip(outcomes, tokens):
                        jobs.append((item, str(label), str(token)))
                except (KeyError, TypeError, ValueError) as exc:
                    report.setdefault('market_gaps', []).append({'market_id': str(market.get('id')), 'reason': str(exc)})
            def quote(job):
                item, label, token = job
                result = {'label': label, 'token_id': token, 'american': None, 'status': 'UNAVAILABLE'}
                try:
                    book, book_source = self.fetch(CLOB, '/book', {'token_id': token}, 20)
                    result.update(book_quote(book, token, item['condition_id'], self.clock()), source=book_source)
                except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError) as exc:
                    result['reason'] = str(exc)[:240]
                return item, result
            with ThreadPoolExecutor(max_workers=6) as pool:
                for item, result in pool.map(quote, jobs):
                    item['outcomes'].append(result)
            fresh = sum(o['status'] == 'AVAILABLE' for m in report['markets'] for o in m['outcomes'])
            count = sum(len(m['outcomes']) for m in report['markets'])
            report.update(status='AVAILABLE' if count and fresh == count else 'PARTIAL' if fresh else 'UNAVAILABLE',
                          available_outcomes=fresh, total_outcomes=count,
                          reason=None if fresh else 'No fresh buy-side quotes in this selection.')
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            report['reason'] = str(exc)[:240]
        report['observed_at'] = self.clock().isoformat()
        report['refresh_after_seconds'] = 30
        if self.archive_dir:
            path = self.archive_dir / ('quote-report-' + uuid4().hex + '.json')
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as stream:
                json.dump(report, stream, allow_nan=False)
        return report
