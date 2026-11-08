"""Archive-only exact-game projected personnel; never grants active clearance.

Provider rosters and club rows deliberately remain separate without an explicit
cross-link. A displayed name is never a join key. No network or runtime writes.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from football_engine.team_profiles import content_hash

from .collect_team_context import parse_tables
from .recover_current_injury_context import instant, parse_archive
from .source_based_parlays import DOMAINS


def _reference(value):
    return value.rstrip('/') if isinstance(value, str) else None


def _read(source, as_of):
    """Digest checks protect replay integrity, not publisher authentication."""
    if source.get('http_status') != 200 or source.get('error'):
        raise ValueError('unsuccessful source acquisition')
    if instant(source['observed_at']) > instant(as_of):
        raise ValueError('source observation after cutoff')
    url = urlparse(source['reference'])
    if url.scheme != 'https' or url.username or url.password or url.port not in (None, 443):
        raise ValueError('invalid source URL')
    if source.get('resolved_reference', source['reference']) != source['reference']:
        raise ValueError('unverified source redirect')
    raw = Path(source['archive_file']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != source['sha256']:
        raise ValueError('archive hash mismatch')
    return raw


def _schedule(state, game_id, as_of):
    games = [g for g in state['upcoming_games'].values() if str(g['game_id']) == str(game_id)]
    if len(games) != 2 or len({g['team_id'] for g in games}) != 2:
        raise ValueError('exact reciprocal two-team upcoming game required')
    teams = {g['team_id'] for g in games}
    for g in games:
        if g['opponent_id'] != next(t for t in teams if t != g['team_id']):
            raise ValueError('opponent mismatch')
        if any(g[k] != games[0][k] for k in ('kickoff', 'week', 'season', 'season_type')):
            raise ValueError('inconsistent game scope')
        if instant(as_of) >= instant(g['kickoff']):
            raise ValueError('projection cutoff must precede kickoff')
        src = g['schedule_source']
        if urlparse(src['reference']).hostname != 'site.api.espn.com':
            raise ValueError('unsupported schedule publisher')
        # Existing schedule envelopes predate explicit HTTP status capture.
        raw = _read(dict(src, http_status=src.get('http_status', 200)), as_of)
        events = [e for e in json.loads(raw)['events'] if str(e['id']) == str(game_id)]
        if len(events) != 1:
            raise ValueError('source event missing or ambiguous')
        e = events[0]
        comps = e['competitions']
        if len(comps) != 1:
            raise ValueError('ambiguous competition')
        c = comps[0]
        if ({x['team']['abbreviation'] for x in c['competitors']} != teams
                or instant(e['date']) != instant(g['kickoff'])
                or e['season']['year'] != g['season']
                or e['week']['number'] != g['week']
                or e['season']['type'] != g['season_type']
                or c['status']['type']['state'] != 'pre'):
            raise ValueError('source game scope mismatch')
    return sorted(games, key=lambda g: g['team_id'])


def _provider_roster(source, raw, team, season):
    url = urlparse(source['reference'])
    if (url.hostname != 'site.api.espn.com'
            or url.path != f'/apis/site/v2/sports/football/nfl/teams/{team.lower()}/roster'):
        raise ValueError('provider roster endpoint mismatch')
    data = json.loads(raw)
    if data['team']['abbreviation'] != team or data['season']['year'] != season:
        raise ValueError('provider team/season mismatch')
    rows=[]
    for gi, group in enumerate(data['athletes']):
        for ai, athlete in enumerate(group['items']):
            player_id=str(athlete['id'])
            links=[link['href'] for link in athlete.get('links', [])
                   if urlparse(link['href']).hostname == 'www.espn.com'
                   and re.fullmatch(r'/nfl/player/_/id/'+re.escape(player_id)+r'/[^/]+', urlparse(link['href']).path)]
            if (not player_id.isdigit() or athlete.get('uid') != 's:20~l:28~a:'+player_id
                    or not links):
                raise ValueError('provider ID lacks explicit matching UID/playercard cross-link')
            rows.append(dict(espn_id=player_id, player=athlete['displayName'],
                position=athlete.get('position', {}).get('abbreviation'), player_reference=sorted(set(links))[0],
                source_json_pointer=f'/athletes/{gi}/items/{ai}', source_sha256=source['sha256'],
                source_reference=source['reference'], observed_at=source['observed_at'],
                provider_status=athlete.get('status'), injury_context=athlete.get('injuries', []),
                provider_team_references=deepcopy(athlete.get('teams', [])),
                injury_scope='PROVIDER_ROSTER_CONTEXT_NOT_GAME_REPORT',
                club_player_reference=None, game_day_eligibility='UNKNOWN',
                diagnostics=['CLUB_IDENTITY_LINK_UNRESOLVED','GAME_ACTIVE_UNVERIFIED',
                             'TRANSACTION_RECONCILIATION_UNVERIFIED']))
    if len({r['espn_id'] for r in rows}) != len(rows):
        raise ValueError('duplicate provider identity')
    return rows


def build_game_personnel_view(state, profiles, sources, *, game_id, as_of):
    """Build a fresh projection without modifying any inputs.

    ``sources`` are raw archive envelopes: kind, team_id, reference,
    resolved_reference, http_status, observed_at, archive_file and sha256.
    Accepted kinds: roster/depth/injury (club), espn_roster (provider).
    Hashes and source clocks are checked on every invocation. Missing sources
    produce explicit gaps; invalid exact schedule scope raises ValueError.
    """
    games = _schedule(state, game_id, as_of)
    profiles = profiles.get('profiles', profiles)
    result=dict(schema='EXACT_GAME_PROJECTED_PERSONNEL_V1', game_id=str(game_id),
        as_of=as_of, kickoff=games[0]['kickoff'], teams={}, source_diagnostics=[],
        status='PROJECTED_CONTEXT_ONLY', game_day_active_proof=False,
        clearance_supported=False, trusted_acquisition_supported=False,
        historical_pregame_version_verified=False, betting_ready=False)
    for game in games:
        team=game['team_id']; profile=profiles[team]
        if profile['team_id'] != team or instant(profile['as_of']) > instant(as_of):
            raise ValueError('profile team or cutoff mismatch')
        if profile.get('game_id') not in (None, str(game_id)):
            raise ValueError('profile already bound to another game')
        if profile.get('version') != content_hash({k:v for k,v in profile.items() if k != 'version'}):
            raise ValueError('profile content hash/version mismatch')
        club={}; depth={}; provider=[]; injury=[]; used=[]
        for source in sources:
            if source.get('team_id') != team:
                continue
            try:
                raw=_read(source, as_of); kind=source['kind']
                if kind == 'identity_profile':
                    url=urlparse(source['reference'])
                    if (url.hostname != 'www.'+DOMAINS[team]+'.com'
                            or not re.fullmatch(r'/team/players-roster/[^/]+/?',url.path)):
                        raise ValueError('club identity profile ownership mismatch')
                elif kind == 'espn_roster':
                    if provider:
                        raise ValueError('multiple provider roster snapshots')
                    provider=_provider_roster(source, raw, team, game['season'])
                else:
                    url=urlparse(source['reference'])
                    if url.hostname != 'www.'+DOMAINS[team]+'.com':
                        raise ValueError('club ownership mismatch')
                    expected={'roster':'/team/players-roster', 'depth':'/team/depth-chart', 'injury':'/team/injury-report'}
                    if kind not in expected or url.path.rstrip('/') != expected[kind]:
                        raise ValueError('club source kind/path mismatch')
                    if kind == 'injury':
                        injury.append(parse_archive(source, team, game['season'], game['week'], as_of))
                    else:
                        target=club if kind == 'roster' else depth
                        if target:
                            raise ValueError('multiple club snapshots for same kind')
                        for row in parse_tables(raw, source['reference'], kind):
                            ref=_reference(row.get('player_reference'))
                            if ref and urlparse(ref).hostname == url.hostname:
                                target.setdefault(ref, []).append(dict(row, source_sha256=source['sha256']))
                used.append({k:source[k] for k in ('kind','reference','sha256','observed_at','archive_file')})
            except (ValueError, KeyError, TypeError, OSError) as exc:
                result['source_diagnostics'].append(dict(team_id=team, reference=source.get('reference'), issue=str(exc)))
        players=[]
        for original in profile['personnel']['players']:
            p=deepcopy(original); ref=_reference(p.get('player_reference'))
            p.update(game_id=str(game_id), kickoff=game['kickoff'], provider_identities={},
                roster_evidence=club.get(ref, []), depth_evidence=depth.get(ref, []),
                game_day_eligibility='UNKNOWN', game_day_active_proof=False,
                injury_evidence=[row for report in injury for row in report.get('rows', [])
                                 if ref and _reference(row['player_reference']) == ref],
                identity_mapping_evidence=[],
                diagnostics=['STABLE_PROVIDER_ID_UNRESOLVED','GAME_ACTIVE_UNVERIFIED',
                             'TRANSACTION_RECONCILIATION_UNVERIFIED'])
            # An explicit club URL in the provider record, or an ESPN playercard
            # anchor on the exact club profile, is required. Never compare names.
            candidates=[]
            for source in sources:
                if source.get('team_id') != team or _reference(source.get('reference')) != ref:
                    continue
                try:
                    if urlparse(source['reference']).hostname != 'www.'+DOMAINS[team]+'.com':
                        continue
                    soup=BeautifulSoup(_read(source, as_of), 'html.parser')
                    links={a['href'] for a in soup.select('a[href]')}
                    candidates.extend((row,source) for row in provider if row['player_reference'] in links)
                except (ValueError, KeyError, OSError):
                    continue
            if len({row['espn_id'] for row,source in candidates}) == 1:
                chosen=candidates[0][0]; p['provider_identities']={'espn_id':chosen['espn_id']}
                p['diagnostics'].remove('STABLE_PROVIDER_ID_UNRESOLVED')
                chosen['club_player_reference']=ref
                p['identity_mapping_evidence']=[dict(
                    method='EXPLICIT_CLUB_PROFILE_ESPN_PLAYERCARD_ANCHOR',
                    club_reference=source['reference'],club_archive_file=source['archive_file'],
                    club_sha256=source['sha256'],club_observed_at=source['observed_at'],
                    anchor_href=row['player_reference'],anchor_selector='a[href]',
                    espn_id=row['espn_id'],provider_reference=row['source_reference'],
                    provider_sha256=row['source_sha256'],provider_json_pointer=row['source_json_pointer'])
                    for row,source in candidates]
            if not p['roster_evidence']: p['diagnostics'].append('ROSTER_SOURCE_ROW_UNVERIFIED')
            if not p['depth_evidence']: p['diagnostics'].append('DEPTH_SOURCE_ROW_UNVERIFIED')
            if not p['injury_evidence']: p['diagnostics'].append('GAME_INJURY_STATUS_UNVERIFIED')
            players.append(p)
        # One provider ID cannot silently become two different club identities.
        assignments={}
        for p in players:
            if p['provider_identities']:
                assignments.setdefault(p['provider_identities']['espn_id'], []).append(p)
        for espn_id, matches in assignments.items():
            if len(matches) > 1:
                for p in matches:
                    p['provider_identities']={}
                    p['diagnostics'].extend(['STABLE_PROVIDER_ID_UNRESOLVED','CLUB_PROVIDER_ID_COLLISION'])
                for row in provider:
                    if row['espn_id']==espn_id:
                        row['club_player_reference']=None
        result['teams'][team]=dict(team_id=team, game_id=str(game_id), kickoff=game['kickoff'],
            opponent_id=game['opponent_id'], profile_version=profile.get('version'),
            profile_as_of=profile['as_of'], schedule_source=deepcopy(game['schedule_source']),
            club_players=players, provider_players=provider, injury_reports=injury, sources=used,
            missingness=dict(club_players=len(players), provider_identities=len(provider),
                club_provider_mappings=sum(bool(p['provider_identities']) for p in players),
                club_missing_provider_id=sum(not p['provider_identities'] for p in players),
                club_with_roster_evidence=sum(bool(p['roster_evidence']) for p in players),
                club_with_depth_evidence=sum(bool(p['depth_evidence']) for p in players),
                club_with_injury_rows=sum(bool(p['injury_evidence']) for p in players)),
            market_dependencies={
                'player_yardage':['selected player stable identity','selected player projected role',
                                  'selected player injury/transaction reconciliation','sourced price',
                                  'calibrated opportunity model'],
                'anytime_td':['selected player stable identity','selected player projected scoring role',
                              'selected player injury/transaction reconciliation','sourced price',
                              'calibrated scoring model'],
                'all_11_interactions':['both projected all-11 lineups','sourced charting'],
                'all_reserves_active_required':False})
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('state','profiles','sources','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--game-id',required=True)
    parser.add_argument('--as-of',required=True)
    args=parser.parse_args()
    read=lambda p: json.loads(p.read_text(encoding='utf-8-sig'))
    view=build_game_personnel_view(read(args.state),read(args.profiles),read(args.sources),
                                  game_id=args.game_id,as_of=args.as_of)
    with args.output.open('x',encoding='utf8') as handle:
        json.dump(view,handle,indent=2,allow_nan=False)


if __name__ == '__main__':
    main()
