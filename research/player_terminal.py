"""Loopback-only NFL player research terminal, no wager or execution endpoints."""
import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from football_engine.player_metrics import CATALOG, VIEWS
from football_engine.player_profile_store import PlayerProfileStore

STATIC = Path(__file__).resolve().parents[1] / "terminal"


def handler(store,team_store=None,schedule_provider=None,weekly_provider=None,ticket_ledger=None,odds_provider=None,weather_provider=None):
    if schedule_provider is None:
        from research.team_schedule import ScheduleProvider
        schedule_provider=ScheduleProvider()
    if weekly_provider is None:
        from research.weekly_matchups import WeeklyProvider
        weekly_provider=WeeklyProvider()
    if odds_provider is None:
        from research.polymarket_odds import PolymarketOdds
        archive=getattr(weekly_provider,'archive_dir',None)
        odds_provider=PolymarketOdds(archive / 'polymarket' if archive else None)
    if weather_provider is None:
        from research.weather_alerts import WeatherProvider
        archive=getattr(weekly_provider,'archive_dir',None)
        weather_provider=WeatherProvider(archive / 'weather' if archive else None)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def send(self, data, content_type="application/json; charset=utf-8", status=200):
            raw = data if isinstance(data, bytes) else json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(len(raw)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers();self.wfile.write(raw)

        def do_GET(self):
            request=urlparse(self.path);params=parse_qs(request.query)
            get=lambda key,default="":params.get(key,[default])[0]
            try:
                if request.path=='/api/polymarket/odds':
                    calendar=weekly_provider.week(get('season') or None,get('season_type') or None,get('week') or None)
                    game=next((g for g in calendar['games'] if g['game_id']==get('game_id')),None)
                    if game is None:self.send({'error':'Game is not in the selected week'},status=404);return
                    self.send(odds_provider.game_odds(game,get('market_type','all'),int(get('page','0'))))
                elif request.path in ('/api/week-matchups','/api/week-matchup','/api/week-weather'):
                    try:
                        calendar=weekly_provider.week(get('season') or None,get('season_type') or None,get('week') or None)
                    except ValueError:
                        raise
                    except Exception:
                        self.send({'error':'The weekly calendar is unavailable. Try again later.'},status=503);return
                    if request.path=='/api/week-matchups':self.send(calendar)
                    elif request.path=='/api/week-weather':
                        self.send({'season':calendar['season'],'season_type':calendar['season_type'],'week':calendar['week'],
                                   'games':{g['game_id']:weather_provider.game(g) for g in calendar['games']},
                                   'scope':'Read-only screening signals; no calibrated stat or betting adjustments.'})
                    else:
                        game=next((g for g in calendar['games'] if g['game_id']==get('game_id')),None)
                        if game is None:self.send({'error':'Game is not in the selected week'},status=404);return
                        from research.weekly_matchups import game_tiers
                        matchup=None;matchup_error=None
                        if team_store is not None:
                            try:matchup=team_store.matchup(game['away'],game['home'],game['season'],'season')
                            except Exception:matchup_error='Team comparison is unavailable for this season.'
                        else:matchup_error='Team comparison store is not configured.'
                        self.send({'game':game,'matchup':matchup,'matchup_error':matchup_error,
                                   'parlays':game_tiers(game,ticket_ledger),'schedule_source':calendar['source']})
                elif request.path=='/api/next-matchup':
                    self.send(schedule_provider.schedule(get('team')))
                elif request.path.startswith('/api/team'):
                    if team_store is None:self.send({'error':'Team play store not configured'},status=503);return
                    team=get('team','CHI');season=int(get('season','2026'));view=get('view','season');cutoff=get('as_of') or None
                    if team not in team_store.overview()['teams'] or view not in dict(team_store.overview()['views']):raise ValueError('unknown team or view')
                    if request.path in ('/api/team/metric','/api/team/evidence') and get('metric') not in {m['id'] for m in team_store.overview()['metrics']}:raise ValueError('unknown team metric')
                    if request.path=='/api/teams':self.send(team_store.overview())
                    elif request.path=='/api/team/profile':self.send(team_store.profile(team,season,view,cutoff))
                    elif request.path=='/api/team/metric':
                        metric=get('metric');clean=lambda v:{k:x for k,x in v.items() if not k.startswith('_')}
                        self.send({'views':{key:clean(team_store.value(team,season,metric,key,cutoff)) for key,_ in team_store.overview()['views']},'confidence':team_store.confidence(team,season,metric,'season' if view=='confidence' else view,cutoff)})
                    elif request.path=='/api/team/matchup':
                        opponent=get('opponent','SEA')
                        if opponent not in team_store.overview()['teams']:raise ValueError('unknown opponent')
                        self.send(team_store.matchup(team,opponent,season,view,cutoff))
                    elif request.path=='/api/team/player-events':self.send(team_store.player_trace(get('player'),season,cutoff))
                    elif request.path=='/api/team/contributors':
                        plays=team_store.plays(team,season,'offense',cutoff);players={}
                        for play in plays:
                            for role in ('passer','receiver','rusher'):
                                pid=play.get(role+'_player_id')
                                if not pid:continue
                                item=players.setdefault(pid,{'player_id':pid,'name':play.get(role+'_player_name',pid),'roles':{},'games':set(),'source_ids':set()})
                                item['roles'][role]=item['roles'].get(role,0)+1;item['games'].add(play['game_id']);item['source_ids'].add(play['source_id'])
                        self.send({'players':[{**p,'games':len(p['games']),'source_ids':sorted(p['source_ids'])} for p in sorted(players.values(),key=lambda p:sum(p['roles'].values()),reverse=True)],'scope':'Recorded offensive event roles, not all-11 participation or causal credit.'})
                    elif request.path=='/api/team/evidence':
                        self.send(team_store.evidence(team,season,get('metric'),view,cutoff))
                    else:self.send({'error':'Unknown team route'},status=404)
                elif request.path=="/api/overview":
                    with store.connect() as db:
                        summary=json.loads(db.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()[0])
                        positions=[dict(row) for row in db.execute("SELECT position,COUNT(*) as count FROM players WHERE is_current=1 GROUP BY position ORDER BY position")]
                        seasons=[row[0] for row in db.execute("SELECT DISTINCT season FROM observations ORDER BY season DESC")]
                        teams=[row[0] for row in db.execute("SELECT DISTINCT team FROM players WHERE is_current=1 ORDER BY team")]
                    self.send({"summary":summary,"positions":positions,"catalog":CATALOG,"views":VIEWS,"seasons":seasons,"teams":teams})
                elif request.path=="/api/players":
                    self.send(store.players(get("q")[:100],get("position"),get("team"),int(get("limit","80"))))
                elif request.path in ("/api/profile","/api/metric"):
                    pid=get("player");season=int(get("season","2026"));view=get("view","season")
                    role=get("role") or None
                    if view not in dict(VIEWS): raise ValueError("unknown analytical view")
                    if not 2006 <= season <= 2100: raise ValueError("season out of range")
                    if request.path=="/api/profile":
                        profile=store.profile(pid,season,view,role,get("as_of") or None)
                        profile['supplemental_context']=store.supplemental_context(pid,season,get('as_of') or None)
                        if team_store is not None:
                            profile['shared_play_context']=team_store.player_summary(pid,season,get('as_of') or None)
                        self.send(profile)
                    else:
                        with store.connect() as db:
                            player=db.execute("SELECT position FROM players WHERE id=?",(pid,)).fetchone()
                        if not player: raise KeyError(pid)
                        metric=next((metric for metric in CATALOG.get(role or player[0],[]) if metric["id"]==get("metric")),None)
                        if not metric: raise ValueError("metric does not belong to selected role")
                        self.send({"metric":metric,"views":{key:store.metric(pid,metric,season,key,get("as_of") or None) for key,_ in VIEWS}})
                else:
                    path={"/":"index.html","/index.html":"index.html","/app.js":"app.js","/styles.css":"styles.css",'/teams':'teams.html','/teams.js':'teams.js','/teams.css':'teams.css','/team-night.css':'team-night.css','/team-visuals.js':'team-visuals.js','/ui-clean.css':'ui-clean.css','/next-matchup.js':'next-matchup.js','/weekly-matchups.js':'weekly-matchups.js','/polymarket-odds.js':'polymarket-odds.js','/matchups':'matchups.html','/matchups-page.js':'matchups-page.js'}.get(request.path)
                    if not path:
                        self.send({"error":"Not found"},status=404);return
                    file=STATIC/path
                    self.send(file.read_bytes(),mimetypes.guess_type(file)[0]+"; charset=utf-8")
            except (ValueError,KeyError) as exc:
                self.send({"error":str(exc)},status=400)
            except Exception:
                self.send({"error":"Profile could not be loaded. Inspect local source coverage."},status=500)
    return Handler


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--database",type=Path,required=True)
    parser.add_argument("--port",type=int,default=8766)
    parser.add_argument('--team-database',type=Path)
    parser.add_argument('--team-context-database',type=Path)
    parser.add_argument('--charting-database',type=Path)
    parser.add_argument('--schedule-archive',type=Path)
    parser.add_argument('--ticket-ledger',type=Path)
    args=parser.parse_args()
    if not args.database.is_file():raise SystemExit("Player database not found; build a snapshot first.")
    from football_engine.team_quant import TeamQuantStore
    team_store=TeamQuantStore(args.team_database,context_path=args.team_context_database,charting_path=args.charting_database) if args.team_database else None
    from research.team_schedule import ScheduleProvider
    from research.weekly_matchups import WeeklyProvider
    server=ThreadingHTTPServer(("127.0.0.1",args.port),handler(PlayerProfileStore(args.database,charting_path=args.charting_database),team_store,ScheduleProvider(args.schedule_archive),WeeklyProvider(args.schedule_archive),args.ticket_ledger))
    print(f"Case Capital NFL Quant: http://127.0.0.1:{args.port}",flush=True)
    server.serve_forever()
