"""Read an independent, source-versioned public charting research overlay."""
import json
import hashlib
import math
import re
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from .team_metrics import TEAM_CATALOG


def instant(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def numeric(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def boolean(value):
    if value in (True, 'TRUE', 'true', '1', 1):
        return 1
    if value in (False, 'FALSE', 'false', '0', 0):
        return 0
    return None


def personnel(value):
    """Parse explicit position counts; FB counts toward running-back personnel."""
    if not value:
        return None
    result = {}
    for part in value.split(','):
        match = re.fullmatch(r'\s*(\d+)\s+([A-Z]+)\s*', part)
        if not match or match[2] in result:
            return None
        result[match[2]] = int(match[1])
    return result if sum(result.values()) == 11 else None


# metric_id -> (side, eligible population, explicit field, operation, parameter)
RULES = {}


def register(key, side, population, field, operation='mean', parameter=None):
    RULES[key] = (side, population, field, operation, parameter)


for key, field in [('scheme.play_action_frequency', 'is_play_action'),
                   ('coaching.play_action_tendency', 'is_play_action'),
                   ('scheme.rpo_frequency', 'is_rpo'),
                   ('coaching.motion_tendency', 'is_motion')]:
    register(key, 'offense', 'dropback' if 'play_action' in key else 'scrimmage', field, 'flag')
for key, code in [('11', (1, 1, 3)), ('12', (1, 2, 2)), ('21', (2, 1, 2)), ('13', (1, 3, 1))]:
    register(f'scheme.{key}_personnel_usage', 'offense', 'scrimmage', 'offense_personnel', 'personnel', code)
    if key != '13':
        register(f'scheme.{key}_personnel_epa', 'offense', 'scrimmage', 'offense_personnel', 'epa_personnel', code)
for key, field in [('play_action', 'is_play_action'), ('rpo', 'is_rpo'), ('motion_play', 'is_motion')]:
    register(f'scheme.{key}_epa', 'offense', 'dropback' if key == 'play_action' else 'scrimmage', field, 'epa_flag')
for key, val in [('shotgun', 'S'), ('under_center', 'U'), ('pistol', 'P')]:
    register(f'scheme.{key}_formation', 'offense', 'scrimmage', 'qb_location', 'category', val)
register('scheme.empty_backfield', 'offense', 'scrimmage', 'n_offense_backfield', 'equals', 0)
register('scheme.empty_formation_epa', 'offense', 'scrimmage', 'n_offense_backfield', 'epa_equals', 0)
for key, val in [('man', 'MAN_COVERAGE'), ('zone', 'ZONE_COVERAGE')]:
    register(f'coverage.{key}_coverage_frequency', 'defense', 'dropback', 'defense_man_zone_type', 'category', val)
    register(f'coverage.{key}_coverage_epa_allowed', 'defense', 'dropback', 'defense_man_zone_type', 'epa_category', val)
for n in (0, 1, 2, 3, 4, 6):
    register(f'coverage.cover_{n}_usage', 'defense', 'dropback', 'defense_coverage_type', 'category', f'COVER_{n}')
    if n in (1, 2, 3, 4):
        register(f'coverage.cover_{n}_epa_allowed', 'defense', 'dropback', 'defense_coverage_type', 'epa_category', f'COVER_{n}')
for key in ['coverage.blitz_frequency', 'coaching.blitz_tendency']:
    register(key, 'defense', 'dropback', 'n_blitzers', 'positive')
for key, n in [('nickel', 5), ('dime', 6)]:
    register(f'coverage.{key}_personnel_usage', 'defense', 'scrimmage', 'defense_personnel', 'db_personnel', n)
register('rushing.average_box_defenders', 'offense', 'rush', 'n_defense_box')
register('pass_rush.opponent_throwaway_rate', 'defense', 'dropback', 'is_throw_away', 'flag')
register('pass_rush.pressure_rate', 'defense', 'dropback', 'was_pressure', 'flag')
register('passing.pressure_rate_allowed', 'offense', 'dropback', 'was_pressure', 'flag')
register('line.pressures_allowed_dropback', 'offense', 'dropback', 'was_pressure', 'flag')
register('pass_rush.four_man_pressure_rate', 'defense', 'dropback', 'was_pressure', 'four_man_pressure')
register('pass_rush.blitz_pressure_rate', 'defense', 'dropback', 'was_pressure', 'blitz_pressure')
register('passing.average_time_to_throw', 'offense', 'throw', 'time_to_throw')
# Only existing catalog IDs are exposed. Motion-at-snap and pre-snap-only remain
# unsupported: FTN's is_motion does not distinguish those timings.
CATALOG = {m['id']: m for m in TEAM_CATALOG}
RULES = {k: v for k, v in RULES.items() if k in CATALOG}


def eligible(pbp, population):
    if pbp.get('play_type') not in ('pass', 'run') or boolean(pbp.get('no_play')) == 1:
        return False
    if any(boolean(pbp.get(k)) == 1 for k in ('qb_kneel', 'qb_spike', 'two_point_attempt')):
        return False
    if population == 'dropback':
        return boolean(pbp.get('qb_dropback')) == 1
    if population == 'throw':
        return boolean(pbp.get('pass_attempt')) == 1 and boolean(pbp.get('sack')) != 1
    if population == 'rush':
        return pbp.get('play_type') == 'run' and boolean(pbp.get('qb_scramble')) != 1
    return True


def field_value(fields, name):
    item = fields.get(name)
    return item['value'] if item else None


def measure(fields, pbp, rule):
    _, _, field, op, parameter = rule
    raw = field_value(fields, field)
    if raw is None or raw == '':
        return None, None
    ids = set(fields[field]['source_ids'])
    if op in ('personnel', 'epa_personnel', 'db_personnel'):
        counts = personnel(raw)
        if counts is None:
            return None, None
        if op == 'db_personnel':
            yes = sum(counts.get(k, 0) for k in ('DB', 'CB', 'S', 'SS', 'FS')) == parameter
        else:
            yes = (counts.get('RB', 0) + counts.get('FB', 0), counts.get('TE', 0), counts.get('WR', 0)) == parameter
    elif op in ('category', 'epa_category'):
        allowed = {'qb_location': {'S', 'U', 'P'}, 'defense_man_zone_type': {'MAN_COVERAGE', 'ZONE_COVERAGE'},
                   'defense_coverage_type': {'COVER_0', 'COVER_1', 'COVER_2', '2_MAN', 'COVER_3', 'COVER_4', 'COVER_6', 'COVER_9', 'COMBO', 'BLOWN'}}
        if raw not in allowed[field]:
            return None, None
        yes = raw == parameter
    elif op in ('flag', 'epa_flag', 'four_man_pressure', 'blitz_pressure'):
        yes = boolean(raw)
        if yes is None:
            return None, None
        if op in ('four_man_pressure', 'blitz_pressure'):
            other = 'n_pass_rushers' if op == 'four_man_pressure' else 'n_blitzers'
            n = numeric(field_value(fields, other))
            if n is None or not 0 <= n <= 11 or n != int(n):
                return None, None
            ids.update(fields[other]['source_ids'])
            if not (n == 4 if op == 'four_man_pressure' else n > 0):
                return 'inapplicable', ids
    else:
        n = numeric(raw)
        maximum = {'n_blitzers': 11, 'n_defense_box': 11, 'n_offense_backfield': 5, 'time_to_throw': 30}.get(field, 11)
        minimum = 1 if field in ('n_defense_box', 'time_to_throw') else 0
        if n is None or not minimum <= n <= maximum:
            return None, None
        if op == 'mean':
            return n, ids
        yes = n > 0 if op == 'positive' else n == parameter
    if op.startswith('epa_'):
        if not yes:
            return 'inapplicable', ids
        return numeric(pbp.get('epa')), ids
    return float(yes) * 100, ids


class ChartingMetrics:
    def __init__(self, path):
        self.path = Path(path).resolve()

    def connect(self):
        db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        return db

    def metadata(self):
        with self.connect() as db:
            return {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM metadata')}

    def verify_base(self, path):
        h = hashlib.sha256()
        with Path(path).open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                h.update(block)
        if h.hexdigest() != self.metadata().get('base_input_sha256'):
            raise ValueError('Charting overlay base input SHA256 does not match this database')
        return True

    @lru_cache(maxsize=1)
    def sources(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM sources ORDER BY id')]

    def source(self, source_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
            return dict(row) if row else None

    @lru_cache(maxsize=96)
    def records(self, team, season):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM plays WHERE season=? AND (posteam=? OR defteam=?) ORDER BY game_id,play_id', (season, team, team))]

    @lru_cache(maxsize=96)
    def prepared(self, team, season):
        return [(row, json.loads(row['pbp']), json.loads(row['game']), json.loads(row['fields']), json.loads(row['base_source_ids'])) for row in self.records(team, season)]

    def evidence(self, team, season, metric_id, view='season', as_of=None, limit=80):
        """Exact aggregate evidence; row-level endpoint evidence is not implemented."""
        value = self.value(team, season, metric_id, view, as_of)
        if value is None:
            return None
        aggregate = {k: v for k, v in value.items() if k != '_game_values'}
        return {'team': team, 'season': season, 'metric_id': metric_id, 'view': view,
                'evidence_kind': 'SOURCED_AGGREGATE',
                'aggregate': aggregate, 'rows': [], 'total': value['sample_plays'],
                'total_eligible_rows': value['eligible_observations'], 'limit': limit,
                'source_ids': value['source_ids'], 'row_evidence_available': False,
                'reason': 'Aggregate uses the exact value selection and denominator; row-level evidence is not implemented. Raw records remain in the independent overlay.',
                'sources': [s for s in self.sources() if s['id'] in value['source_ids']]}

    @lru_cache(maxsize=20000)
    def value(self, team, season, metric_id, view='season', as_of=None, *, week_max=None):
        if metric_id not in RULES:
            return None
        if view not in ('season', 'last3', 'last5', 'last8', 'home', 'away', 'man', 'zone', 'blitz', 'four_man'):
            return None
        cutoff = instant(as_of) if as_of else None
        side, population, field, operation, parameter = RULES[metric_id]
        sources = {s['id']: s for s in self.sources()}
        def visible(ids):
            return all(sid in sources and (cutoff is None or instant(sources[sid]['observed_at']) <= cutoff) for sid in ids)
        rows = []
        for row, pbp, game, all_fields, base_ids in self.prepared(team, season):
            if row['posteam' if side == 'offense' else 'defteam'] != team:
                continue
            if week_max is not None and row['week'] > week_max:
                continue
            if not visible(base_ids):
                continue
            if view in ('home', 'away') and game.get('home' if view == 'home' else 'away') != team:
                continue
            fields = {k: v for k, v in all_fields.items() if visible(v['source_ids'])}
            rows.append((row, pbp, game, fields))
        # Last-N games are selected from all available PBP games, including games
        # with no charting rows; missing charting cannot silently extend the window.
        if view.startswith('last'):
            game_order = sorted({(r['game_date'], r['week'], r['game_id']) for r, _, _, _ in rows})
            ids = {g[2] for g in game_order[-int(view[4:]):]}
            rows = [r for r in rows if r[0]['game_id'] in ids]
        values, used_ids, game_values = [], set(), {}
        missing = inapplicable = filter_missing = eligible_count = 0
        observed_weeks = set()
        for row, pbp, game, fields in rows:
            if not eligible(pbp, population):
                continue
            if view in ('man', 'zone', 'blitz', 'four_man'):
                filter_field = 'defense_man_zone_type' if view in ('man', 'zone') else 'n_blitzers' if view == 'blitz' else 'n_pass_rushers'
                raw = field_value(fields, filter_field)
                if filter_field == 'defense_man_zone_type':
                    if raw not in ('MAN_COVERAGE', 'ZONE_COVERAGE'):
                        filter_missing += 1
                        continue
                    matches = raw == ('MAN_COVERAGE' if view == 'man' else 'ZONE_COVERAGE')
                else:
                    n = numeric(raw)
                    if n is None or not 0 <= n <= 11 or n != int(n):
                        filter_missing += 1
                        continue
                    matches = n > 0 if view == 'blitz' else n == 4
                if not matches:
                    continue
                used_ids.update(fields[filter_field]['source_ids'])
            eligible_count += 1
            val, ids = measure(fields, pbp, RULES[metric_id])
            if val == 'inapplicable':
                inapplicable += 1
                continue
            if val is None:
                missing += 1
                continue
            values.append(val)
            used_ids.update(ids)
            used_ids.update(json.loads(row['base_source_ids']))
            game_values.setdefault(row['game_id'], []).append(val)
            observed_weeks.add(row['week'])
        complete = not missing and not filter_missing
        qualified = complete and len(values) >= 100 and len(game_values) >= 3
        is_epa = operation.startswith('epa_')
        result = {**CATALOG[metric_id], 'value': round(sum(values)/len(values), 5) if values else None,
                  'status': 'UNAVAILABLE' if not values else 'CALCULATED' if qualified else 'INCOMPLETE_FIELDS' if not complete else 'SMALL_SAMPLE',
                  'season': season, 'view': view, 'sample_plays': len(values), 'sample_opportunities': len(values),
                  'sample_games': len(game_values), 'source_ids': sorted(used_ids) if values else [],
                  'eligible_observations': eligible_count, 'missing_opportunities': missing,
                  'inapplicable_opportunities': inapplicable, 'filter_missing_opportunities': filter_missing,
                  'qualified': qualified, 'complete_fields': complete, 'percentile': None, 'confidence_interval': None,
                  'unit': 'EPA' if is_epa else 's' if field == 'time_to_throw' else 'count' if operation == 'mean' else '%',
                  'numerator': sum(values) / (100 if operation != 'mean' and not is_epa else 1),
                  'observed_weeks': sorted(observed_weeks), '_game_values': game_values,
                  'formula': f'pooled mean of {field}; {operation}; parameter={parameter}; {population}; view={view}',
                  'definition': f'Explicit source field {field}; {operation} {parameter}; denominator: valid charted {population} plays; kneels, spikes, no-plays and two-point tries excluded.',
                  'reason': 'No eligible source observations at this season/cutoff.' if not values else 'Some eligible plays lack valid charting; rate is conditional on observed fields.' if not complete else None,
                  'historically_pregame_certified': False,
                  'provider_model_dependency': 'nflverse EPA; model training vintage not independently verified' if is_epa else None,
                  'feature_signature': f'public_charting|{side}|{population}|{field}|{operation}|{parameter}|{view}'}
        if field == 'is_motion':
            result['definition'] += ' FTN motion includes before OR at the snap; no separate timing is available.'
        if field == 'n_blitzers':
            result['definition'] += ' Blitz means explicit FTN n_blitzers > 0, not a five-rusher proxy.'
        if not values and rows and missing == 0 and eligible_count > 0 and inapplicable == eligible_count:
            result.update(status='INAPPLICABLE', reason='No plays satisfy the explicit subgroup; conditional EPA has no denominator.')
        return result


def team_charting_metrics(db_path, team, season, week_max=None, *, as_of=None, view='season'):
    reader = ChartingMetrics(db_path)
    return {key: reader.value(team, season, key, view, as_of, week_max=week_max) for key in RULES}


def team_charting_sources(db_path):
    return ChartingMetrics(db_path).sources()


def team_charting_metadata(db_path):
    return ChartingMetrics(db_path).metadata()
