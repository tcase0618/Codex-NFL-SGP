"""Scheduled all-team data refresh; no odds, wagers or inferred scheme changes."""
import argparse
import copy
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from football_engine.coaching import attach_coaching, coach_identity
from football_engine.team_profiles import TeamProfileStore, add_scheme_note, content_hash, timestamp
from .build_team_profiles import game_observations, write_team_report
from .collect_coaching import collect_team, collect_evidence
from .collect_team_context import fetch_page
from .source_based_parlays import DOMAINS
from .team_candidates import team_candidates
from .source_gap_repair import repair_injury, attach_reviewed_identities
from .dallas_transaction_recovery import collect_window

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
EASTERN = ZoneInfo("America/New_York")


class RefreshBusy(OSError):
    pass


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
    os.replace(temporary, path)


@contextmanager
def single_run(path):
    with path.open("a+b") as handle:
        if not path.stat().st_size:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RefreshBusy("another refresh is already running") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def fetch_json(url, archive):
    response = requests.get(url, timeout=(6, 25), headers={"User-Agent": "CaseCapitalFootballResearch/0.1"})
    response.raise_for_status()
    sha = hashlib.sha256(response.content).hexdigest()
    path = archive / (sha + ".json")
    if not path.exists():
        path.write_bytes(response.content)
    return response.json(), dict(reference=url, sha256=sha, archive_file=str(path),
                                 observed_at=datetime.now(timezone.utc).isoformat(),
                                 kind="FRESH_HTTP_RETRIEVAL", historical_pregame_version_verified=False)


def event_game(event):
    competition = event["competitions"][0]
    status = competition.get("status", event.get("status", {}))["type"]
    phase = event.get("season", {}).get("type") or event.get("seasonType", {}).get("type", 2)
    if not status.get("completed") or int(phase) not in (2, 3):
        return None
    season = event["season"]["year"]
    week = event["week"]["number"]
    return dict(id=event["id"], name=event["name"], season=season, week=week,
                season_type=int(phase), kickoff=event["date"])


def near_game(event, now):
    kickoff=timestamp(event["date"])
    return kickoff-timedelta(hours=2)<=now<=kickoff+timedelta(hours=5)


def upcoming_game_context(event, source, now):
    """Read upcoming identities from the schedule, independently of final boxes."""
    kickoff = timestamp(event["date"])
    competition = event["competitions"][0]
    phase = event.get("season", {}).get("type") or event.get("seasonType", {}).get("type", 2)
    if isinstance(phase, dict):
        phase = phase.get("type")
    if kickoff <= now or competition.get("status", event.get("status", {})).get("type", {}).get("completed") or phase not in (2, 3):
        return []
    teams = [c["team"]["abbreviation"] for c in competition["competitors"]]
    if len(teams) != 2 or len(set(teams)) != 2 or any(t not in DOMAINS for t in teams):
        return []
    return [dict(team_id=team, opponent_id=next(t for t in teams if t != team),
                 game_id=event["id"], kickoff=kickoff.isoformat(), week=event["week"]["number"],
                 season=event["season"]["year"], season_type=phase, schedule_source=source)
            for team in teams]


def game_stats_hash(rows):
    # Changes to videos/news/clock metadata are not statistical corrections.
    normalized=[]
    for row in sorted(rows,key=lambda row:row["team"]):
        item={k:v for k,v in row.items() if k not in ("source","available_at")}
        item["kickoff"]=timestamp(item["kickoff"]).astimezone(timezone.utc).isoformat()
        item.setdefault("season_type",2)
        normalized.append(item)
    return content_hash(normalized)


def known_game_hashes(store, as_of=None):
    as_of=as_of or datetime.now(timezone.utc)
    latest = {}
    for (raw,) in store.connection.execute("SELECT payload FROM team_observations ORDER BY sequence"):
        row = json.loads(raw)
        if timestamp(row["available_at"])>as_of:
            continue
        # Existing stores predate the explicit phase field; regular-season
        # observations are phase 2 unless positively recorded otherwise.
        row.setdefault("season_type", 2)
        latest[(row["game_id"], row["team"])] = row
    games = {}
    for (game, _), row in latest.items():
        games.setdefault(game, []).append(row)
    return {game: game_stats_hash(rows) for game, rows in games.items() if len(rows) == 2}


def safe_parallel(items, function):
    def one(item):
        try:
            return item, function(item), None
        except Exception as exc:
            return item, None, str(exc)
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(one, items))


def reviewed_depth(source, reviewed):
    if source.get("rows"):
        return source
    for image in source.get("image_sources", []):
        for record in reviewed:
            if (record["team"] == source["team"] and record["content_sha256"] == image["content_sha256"]
                    and hashlib.sha256(Path(image["file"]).read_bytes()).hexdigest() == record["content_sha256"]):
                return {**source, "rows": record["rows"], "status": "VISUALLY_TRANSCRIBED",
                        "transcription": {k: record[k] for k in ("content_sha256", "reviewed_at", "chart_label_date")}}
    return source


def staff_matches(current, reviewed):
    expected = {role: (coach_identity(record["name"]), (record["title"] or "").casefold())
                for role, record in reviewed["staff"].items() if record["name"]}
    actual = {c["role"]: (coach_identity(c["name"]), c["title"].casefold()) for c in current["staff"]}
    return expected == actual


def collect_news(team, archive):
    source = collect_evidence(f"https://www.{DOMAINS[team]}.com/news/", archive)
    if "error" in source:
        return dict(source=source, items=[])
    soup = BeautifulSoup(Path(source["archive_file"]).read_bytes(), "html.parser")
    found = {}
    for a in soup.find_all("a", href=True):
        url = urljoin(source["reference"], a["href"])
        title = a.get_text(" ", strip=True)
        if (urlparse(url).hostname == f"www.{DOMAINS[team]}.com" and "/news/" in url
                and len(title) > 12 and re.search(r"coordinat|head.coach|play.call|scheme|injur|inactive|trade|transaction|sign|waiv|activat|roster|recap|reaction", url, re.I)):
            found.setdefault(url, dict(team_id=team, reference=url, headline=title[:400], status="PENDING_SOURCE_REVIEW"))
    return dict(source=source, items=list(found.values())[:12])


def run(config, force_context=False):
    root, output = Path(config["runtime"]), Path(config["output"])
    root.mkdir(parents=True, exist_ok=True); output.mkdir(parents=True, exist_ok=True)
    archive = root / "sources"; archive.mkdir(exist_ok=True)
    with single_run(root / "refresh.lock"):
        state_path = root / "state.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        now = datetime.now(timezone.utc); local = now.astimezone(EASTERN)
        season = local.year - (local.month <= 7)
        context_due = force_context or state.get("context_date") != local.date().isoformat()
        errors, gaps, sources, events = [], copy.deepcopy(state.get("context_gaps",[])), [], {}
        upcoming = {team: item for team, item in state.get("upcoming_games", {}).items()
                    if timestamp(item["kickoff"]) > now}
        urls = [f"{BASE}/scoreboard?dates={(local.date()-timedelta(days=offset)):%Y%m%d}&limit=100" for offset in range(3)]
        if context_due:
            phases = (2, 3) if local.month <= 7 else (2,)
            urls += [f"{BASE}/teams/{team.lower()}/schedule?season={season}&seasontype={phase}" for team in DOMAINS for phase in phases]
        game_window=False
        for url, result, error in safe_parallel(urls, lambda url: fetch_json(url, archive)):
            if error:
                errors.append(dict(stage="schedule", reference=url, error=error)); continue
            data, source = result; sources.append(source)
            for event in data.get("events", []):
                try:
                    for item in upcoming_game_context(event, source, now):
                        old = upcoming.get(item["team_id"])
                        if not old or timestamp(item["kickoff"]) <= timestamp(old["kickoff"]):
                            upcoming[item["team_id"]] = item
                    game_window=game_window or near_game(event,now)
                    game = event_game(event)
                    if game and timestamp(game["kickoff"]) < now:
                        events[game["id"]] = game
                except (KeyError, ValueError, TypeError) as exc:
                    errors.append(dict(stage="schedule_event", event_id=event.get("id"), error=str(exc)))
        context_due=context_due or game_window
        state["upcoming_games"] = upcoming
        store = TeamProfileStore(Path(config["database"]))
        known = known_game_hashes(store)
        to_fetch = [game for game in events.values() if game["id"] not in known or timestamp(game["kickoff"]) >= now-timedelta(days=3)]
        new_games, corrections = [], []
        for game, result, error in safe_parallel(to_fetch, lambda g: fetch_json(f"{BASE}/summary?event={g['id']}", archive)):
            if error:
                errors.append(dict(stage="final_box", game_id=game["id"], error=error)); continue
            raw, source = result; sources.append(source)
            try:
                rows = game_observations(game, raw, source, timestamp(source["observed_at"]))
                for row in rows:
                    row["season_type"] = game["season_type"]
                digest = game_stats_hash(rows)
                if digest != known.get(game["id"]):
                    store.add_game(rows)
                    (corrections if game["id"] in known else new_games).append(game["id"])
                    known[game["id"]] = digest
            except (KeyError, ValueError, TypeError) as exc:
                errors.append(dict(stage="final_validation", game_id=game["id"], error=str(exc)))
        publish_due = context_due or new_games or corrections or not state.get("latest_profiles")
        review_queue = []
        previous_profiles = json.loads(Path(state.get("latest_profiles", config["seed_profiles"])).read_text())["profiles"]
        profiles = previous_profiles
        if publish_due:
            gaps=[]
            tasks = [(team, kind, f"https://www.{domain}.com/team/{suffix}/") for team, domain in DOMAINS.items()
                     for kind, suffix in (("roster", "players-roster"), ("depth", "depth-chart"), ("injury", "injury-report"))]
            pages = []
            reviewed = json.loads(Path(config["reviewed_depth"]).read_text())
            for task, page, error in safe_parallel(tasks, lambda t: fetch_page(t, archive)):
                if error:
                    page = dict(team=task[0], kind=task[1], reference=task[2], status="FAILED", rows=[], error=error,
                                retrieved_at=datetime.now(timezone.utc).isoformat())
                if page['kind'] == 'injury':
                    page = repair_injury(page, archive)
                pages.append(reviewed_depth(page, reviewed) if page["kind"] == "depth" else page)
            directories = dict((team, value if value else dict(team_id=team,staff=[],errors=[error]))
                               for team, value, error in safe_parallel(list(DOMAINS), lambda t: collect_team((t,DOMAINS[t]),archive)))
            tx = dict((team, value if value else dict(error=error)) for team,value,error in
                      safe_parallel(list(DOMAINS), lambda t: collect_evidence(f"https://www.{DOMAINS[t]}.com/team/transactions/2026".replace("/2026",f"/{season}"),archive)))
            news = dict((team,value if value else dict(source=dict(error=error),items=[])) for team,value,error in
                        safe_parallel(list(DOMAINS), lambda t: collect_news(t,archive)))
            coaching = json.loads(Path(config["coaching"]).read_text())["teams"]
            identities = json.loads(Path(config['reviewed_personnel']).read_text())['records'] if config.get('reviewed_personnel') else []
            notes = json.loads(Path(config["scheme_notes"]).read_text())["notes"]
            if config.get("reviewed_news") and Path(config["reviewed_news"]).exists():
                notes += json.loads(Path(config["reviewed_news"]).read_text())["notes"]
            profiles = {}; now = datetime.now(timezone.utc)
            historical_injuries = []
            if config.get("historical_injury_reports"):
                from .recover_league_injuries import verified_historical_reports
                entry = config["historical_injury_reports"]
                historical_injuries = verified_historical_reports(entry["path"], entry["sha256"], now.isoformat())
            for team in DOMAINS:
                profile = store.profile(team, now)
                team_pages = [p for p in pages if p["team"] == team]
                blocking = []
                for p in team_pages:
                    if p["status"] not in ("parsed", "VISUALLY_TRANSCRIBED"):
                        gaps.append(dict(team=team, kind=p["kind"], status=p["status"], reference=p["reference"]))
                        if p["status"] == "FAILED" or p["kind"] in ("roster", "depth"):
                            blocking.append(f"{p['kind']} source requires review")
                    label=p.get("transcription",{}).get("chart_label_date")
                    if label and (local.date()-datetime.strptime(label,"%Y-%m-%d").date()).days>7:
                        gaps.append(dict(team=team,kind="depth",status="DATED_CHART_REQUIRES_FRESHNESS_REVIEW",chart_label_date=label))
                        p['candidate_eligible'] = False
                        blocking.append('dated depth chart excluded from current candidate roles')
                profile["personnel"] = dict(status="CURRENT_SOURCE_CONTEXT_NOT_GAME_DAY_ELIGIBILITY",
                    players=team_candidates([p for p in team_pages if p.get('candidate_eligible', True)],team),
                    source_records=[{k:p.get(k) for k in ("kind","reference","retrieved_at","content_sha256","status","transcription","image_sources","error","report_scope","parse_errors","reason","eligibility")} for p in team_pages],
                    injury_report_rows=next(p["rows"] for p in team_pages if p["kind"]=="injury"))
                injury_page = next(p for p in team_pages if p['kind'] == 'injury')
                attach_reviewed_identities(profile['personnel']['players'], team, identities, profile['as_of'])
                profile['personnel']['injury_coverage'] = {k: injury_page.get(k) for k in
                    ('status', 'report_scope', 'reason', 'eligibility', 'historical_reports')}
                if injury_page['status'] not in {'parsed','VISUALLY_TRANSCRIBED'}:
                    blocking.append('current injury report unavailable or incomplete; confirm eligibility before selection')
                transaction = tx[team]
                transaction_rows = []
                if "error" not in transaction:
                    soup=BeautifulSoup(Path(transaction["archive_file"]).read_bytes(),"html.parser")
                    transaction_rows=[[c.get_text(" ",strip=True) for c in tr.find_all(["td","th"])] for table in soup.find_all("table") for tr in table.find_all("tr")]
                fallback = collect_window(season, local.month, archive, max_pages=3) if not transaction_rows and team == 'DAL' else None
                if not transaction_rows and (not fallback or not fallback['all_six_categories_complete']):
                    gaps.append(dict(team=team,kind="transactions",status="UNPARSED_OR_UNAVAILABLE"))
                profile["personnel"]["transactions"]=dict(source=transaction,rows_as_published=transaction_rows,status="REQUIRES_IDENTITY_AND_EFFECTIVE_TIME_RECONCILIATION")
                if fallback:
                    profile['personnel']['transactions']['official_league_fallback'] = fallback
                profile["version"]=content_hash({k:v for k,v in profile.items() if k!="version"})
                for note in notes:
                    if note["team_id"]==team:
                        profile=add_scheme_note(profile,note)
                directory=directories[team]
                if staff_matches(directory,coaching[team]):
                    reviewed_coaching=copy.deepcopy(coaching[team])
                    reviewed_coaching["directory_verified_at"]=directory["directory_source"]["observed_at"]
                    for role in reviewed_coaching["staff"].values():
                        role["sources"][0]=directory["directory_source"]
                    profile=attach_coaching(profile,reviewed_coaching)
                else:
                    blocking.append("coaching staff changed or unavailable; source review required")
                    gaps.append(dict(team=team,kind="coaching",status="REVIEW_REQUIRED",directory=directory))
                    profile["coaching_review"]=dict(status="REVIEW_REQUIRED",directory=directory,prior_context_reference=config["coaching"])
                old_urls=set(state.get("news_urls",{}).get(team,[]))
                review_queue += [item for item in news[team]["items"] if item["reference"] not in old_urls]
                if "error" in news[team]["source"]:
                    gaps.append(dict(team=team,kind="news",status="FETCH_FAILED"))
                profile["news_context"]=news[team]
                profile["blocking_review_reasons"]=blocking
                if historical_injuries:
                    from .recover_league_injuries import attach_historical_reports
                    profile = attach_historical_reports(profile, historical_injuries)
                profile["version"]=content_hash({k:v for k,v in profile.items() if k!="version"})
                profiles[team]=profile
            state["news_urls"]={t:sorted(set(state.get("news_urls",{}).get(t,[]))|{i["reference"] for i in news[t]["items"]}) for t in DOMAINS}
            state["context_date"]=local.date().isoformat()
            state["context_checked_at"]=now.isoformat()
            if any(p["status"]=="FAILED" for p in pages) or any(not d["staff"] for d in directories.values()):
                state.pop("context_date",None)
            state["context_gaps"]=gaps
        injury_updates = []
        if not publish_due and any(g.get("kind") == "injury" for g in gaps):
            from .retry_injury_gaps import retry_injury_gaps
            profiles, gaps, attempts, injury_updates, retried = retry_injury_gaps(
                profiles, gaps, state.get("injury_gap_attempts", {}), archive, now=now, expected_games=upcoming)
            state["injury_gap_attempts"] = attempts
            state["context_gaps"] = gaps
            sources.extend(retried)
            publish_due = bool(injury_updates)
        stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        folder=output/("automatic-football-refresh-"+stamp);folder.mkdir()
        if publish_due:
            target=folder/"profiles.json"
            with target.open("x",encoding="utf-8") as handle:
                json.dump(dict(kind="AUTOMATIC_TEAM_PROFILE_REFRESH",as_of=now.isoformat(),profiles=profiles,source_gaps=gaps),handle,indent=2)
            for team,profile in profiles.items():
                write_team_report(folder/(team+".md"),profile)
            state["latest_profiles"]=str(target)
        summary=dict(as_of=datetime.now(timezone.utc).isoformat(),status="PARTIAL" if errors or gaps else "OK",
                     teams=len(profiles),new_final_games=new_games,statistical_corrections=corrections,
                     profiles_rebuilt=bool(publish_due),latest_profiles=state["latest_profiles"],
                     injury_sources_recovered=injury_updates,
                     errors=errors,source_gaps=gaps,news_review_queue=review_queue,
                     sources=sources,scheduled_execution="managed by Codex automation",wager_execution_enabled=False)
        local_label=timestamp(summary["as_of"]).astimezone(EASTERN).strftime("%B %d, %Y %I:%M %p %Z")
        lines=["# Automatic football refresh","",f"Run: {local_label}; status: {summary['status']}.","",
               f"Teams: {len(profiles)}. New final games: {len(new_games)}. Statistical corrections: {len(corrections)}.","",
               "Profiles preserve final-box metrics, current source context and reviewed coaching descriptions. New headlines require source review; no scheme probabilities are invented.","",
               "| Team | Profile | Review gates |","|---|---|---|"]
        for team,profile in profiles.items():
            path=folder/(team+".md") if publish_due else Path(state["latest_profiles"]).parent/(team+".md")
            lines.append(f"| {team} | [Open]({path.as_posix()}) | {'; '.join(profile.get('blocking_review_reasons',[])) or 'No new blocking gate'} |")
        lines += ["","## Source gaps",""]+[f"- {g['team']}: {g['kind']} — {g['status']}" for g in gaps]
        lines += ["","## Pending article review",""]+[f"- {i['team_id']}: [{i['headline']}]({i['reference']})" for i in review_queue]
        (folder/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
        summary["report"]=str(folder/"report.md")
        with (folder/"run.json").open("x",encoding="utf-8") as handle:
            json.dump(summary,handle,indent=2)
        # Preserve pending reviews across quiet checks; finding a headline is
        # not equivalent to completing a source review.
        pending={i["reference"]:i for i in state.get("pending_news_reviews",[])}
        pending.update({i["reference"]:i for i in review_queue})
        state["pending_news_reviews"]=list(pending.values())
        state["last_attempt"]=summary["as_of"]
        if not errors:
            state["last_successful_poll"]=summary["as_of"]
        atomic_json(state_path,state)
        atomic_json(output/"football-refresh-status.json",summary)
        latest=output/"football-profiles-latest.md"
        temporary=latest.with_name(latest.name+".tmp")
        temporary.write_text("\n".join(lines)+"\n",encoding="utf-8")
        os.replace(temporary,latest)
        print(json.dumps({k:summary[k] for k in ("status","teams","profiles_rebuilt","new_final_games","statistical_corrections","report","latest_profiles")},indent=2))
        return summary


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--config",type=Path,required=True);parser.add_argument("--force-context",action="store_true")
    args=parser.parse_args()
    config=json.loads(args.config.read_text(encoding="utf-8"))
    try:
        run(config,args.force_context)
    except RefreshBusy as exc:
        print(json.dumps(dict(status="SKIPPED_ALREADY_RUNNING",detail=str(exc))))
    except Exception as exc:
        # Do not replace a validated profile with a failed build. Publish a
        # separate failure indicator so a stale success cannot look current.
        output=Path(config["output"]);output.mkdir(parents=True,exist_ok=True)
        atomic_json(output/"football-refresh-status.json",dict(status="FAILED",as_of=datetime.now(timezone.utc).isoformat(),error=str(exc),last_valid_profiles_preserved=True))
        raise
