"""Build all 32 current descriptive team profiles from sourced final boxes."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from football_engine.team_profiles import TeamProfileStore, add_scheme_note, content_hash, matchup_packet, timestamp
from football_engine.coaching import attach_coaching
from .complete_parlays import load_games
from .replay_2026 import BASE
from .source_based_parlays import DOMAINS


def game_observations(game: dict, raw: dict, source: dict, available_at: datetime) -> list[dict]:
    competition = raw["header"]["competitions"][0]
    if (str(raw["header"].get("id")) != str(game["id"])
            or competition["status"]["type"]["name"] != "STATUS_FINAL"):
        raise ValueError("matching final box required")
    competitors = {item["team"]["abbreviation"]: item for item in competition["competitors"]}
    teams = raw["boxscore"]["teams"]
    if len(teams) != 2 or {team["team"]["abbreviation"] for team in teams} != set(competitors):
        raise ValueError("both final team boxes required")
    stats = {}
    for team in teams:
        abbreviation = team["team"]["abbreviation"]
        values = {item["name"]: item["displayValue"] for item in team["statistics"]}

        def number(name):
            try:
                return float(values[name].replace(",", ""))
            except (KeyError, ValueError):
                return None

        def pair(name, separator="-"):
            try:
                return tuple(float(value) for value in values[name].split(separator))
            except (KeyError, ValueError):
                return None, None

        _, attempts = pair("completionAttempts", "/")
        sacks, sack_yards = pair("sacksYardsLost")
        third_made, third_attempts = pair("thirdDownEff")
        red_made, red_attempts = pair("redZoneAttempts")
        net = number("netPassingYards")
        stats[abbreviation] = {
            "points": float(competitors[abbreviation]["score"]), "plays": number("totalOffensivePlays"),
            "drives": number("totalDrives"), "total_yards": number("totalYards"),
            "passing_attempts": attempts, "sacks_taken": sacks,
            "dropbacks_proxy": attempts + sacks if attempts is not None and sacks is not None else None,
            "gross_passing_yards": net + sack_yards if net is not None and sack_yards is not None else None,
            "net_passing_yards": net, "rushing_yards": number("rushingYards"),
            "rushing_attempts": number("rushingAttempts"), "third_down_made": third_made,
            "third_down_attempts": third_attempts, "red_zone_made": red_made,
            "red_zone_attempts": red_attempts, "passing_td": None, "rushing_td": None,
        }
    for team in raw["boxscore"].get("players", []):
        abbreviation = team["team"]["abbreviation"]
        for section in team.get("statistics", []):
            if section.get("name") not in ("passing", "rushing") or "TD" not in section.get("labels", []):
                continue
            index = section["labels"].index("TD")
            rows = section.get("athletes", [])
            try:
                total = sum(float(row["stats"][index]) for row in rows)
                stats[abbreviation][section["name"] + "_td"] = total if rows else None
            except (ValueError, IndexError):
                pass
    return [{"team": team, "opponent": next(other for other in stats if other != team),
             "game_id": game["id"], "week": game["week"], "season": game["season"],
             "kickoff": game["kickoff"], "available_at": available_at.isoformat(), "final": True,
             "offense": own, "opponent_offense": stats[next(other for other in stats if other != team)],
             "source": source} for team, own in stats.items()]


def write_team_report(path: Path, profile: dict):
    lines = [f"# {profile['team_id']} team profile", "", f"As of {profile['as_of']}. Version `{profile['version']}`.", "",
             f"{profile['games']} observed games; {profile['season_games']} in season {profile['season_year']}. Descriptive evidence, not a calibrated matchup forecast.", "",
             "## Offense and defense", "", "| Side | Measure | Season | Recent four | Recent exposure |", "|---|---|---:|---:|---:|"]
    for side in ("offense", "defense_allowed"):
        for name, recent in profile["recent"][side].items():
            season = profile["season"][side][name]
            fmt = lambda value: f"{value:.3f}" if value is not None else "Unavailable"
            lines.append(f"| {side} | {name} | {fmt(season['value'])} | {fmt(recent['value'])} | {recent['denominator']} |")
    last = profile["last_game"]
    if "coaching" in profile:
        coaches = profile["coaching"]
        lines.extend(["", "## Coaching and play-calling", "", "| Role | Coach | Status |", "|---|---|---|"])
        for role, record in coaches["staff"].items():
            name = record.get('name') or 'No separate coordinator listed'
            lines.append(f"| {role} | [{name}]({record['sources'][0]['reference']}) | {record['status']} |")
        for side, caller in coaches["play_callers"].items():
            name = caller.get('name') or 'Unconfirmed'
            lines.append(f"| {side} play-caller | [{name}]({caller['sources'][0]['reference']}) | {caller['status']} {caller.get('report_date', '')} |")
        lines.extend(["", f"New head coach in 2026: {coaches['staff']['head_coach']['new_head_coach_2026']}.", "",
                      "Management: " + coaches["head_coach_management"]["description"], ""])
        lines.extend(f"[Management source]({s['reference']})" for s in coaches["head_coach_management"]["sources"])
        for side in ("offense", "defense"):
            scheme = coaches[side]
            lines.append(f"**{side.title()} — {scheme['scope']}:** {scheme['description']}")
            lines.extend(f"[Scheme source]({s['reference']})" for s in scheme["sources"])
            if "foundation" in scheme:
                foundation = scheme["foundation"]
                lines.append(f"\n**Tactical foundation — {foundation['scope']} ({foundation['coach']}):** {foundation['description']}")
                lines.extend(f"[Tactical source]({s['reference']})" for s in foundation["sources"])
            era = profile["coaching_era"][side]
            lines.append(f"\nCoaching-era evidence: {era['status']}; post-change games: {era['games']}.\n")
        lines.append("Scheme descriptions do not establish usage percentages, tempo, pressure rates or a numerical betting edge. Unconfirmed callers are not inferred from titles.")
        for rate in coaches.get("reported_rates", []):
            lines.append(f"\n- Dated reported {rate['metric']}: {rate['value']:.1%}; {rate['window']}. {rate['definition']}. [Source]({rate['sources'][0]['reference']})")
        for change in coaches["changes"]:
            lines.append(f"\n- {change['date']} ({change['date_precision']}): {change['description']}")
    lines.extend(["", "## Latest game", "", f"Game {last['game_id']} against {last['opponent']}: {last['offense']['points']:g}–{last['opponent_offense']['points']:g}.",
                  "", "Full raw metrics and sources are preserved in the JSON profile. Points include all scoring. Attempts plus sacks is a dropback proxy; sacks are not pressure. Box red-zone conversion is not player red-zone opportunity.", "",
                  "## Scheme evidence", ""])
    lines.extend(f"- **{note['status']}**: {note['detail']} [Source]({note['source']['reference']})" for note in profile["scheme_notes"])
    if not profile["scheme_notes"]:
        lines.append("No sourced scheme notes loaded. Do not infer a scheme from box-score results alone.")
    lines.extend(["", "Advanced fields still unavailable: " + ", ".join(profile["scheme"]) + ".", "",
                  "## Personnel and depth chart", "", "Current source context only; verify identity, transactions and game-day eligibility before selection.", "",
                  "| Player | Position | Roster section | Depth alignment | Review flags |", "|---|---|---|---|---|"])
    for player in profile["personnel"]["players"]:
        roles = "; ".join(f"{r['position']} row {r['alignment_row']} depth {r['depth']}" for r in player["depth_roles"])
        lines.append(f"| {player['player']} | {player.get('position', 'Unknown')} | {', '.join(player['roster_sections'])} | {roles} | {', '.join(player['review_flags'])} |")
    lines.extend(["", "## Source coverage", ""])
    lines.extend(f"- {source['kind']}: {source['status']}; retrieved {source['retrieved_at']}. [Official page]({source['reference']})" for source in profile["personnel"]["source_records"])
    lines.append("\nInjury rows preserved: " + str(len(profile["personnel"]["injury_report_rows"])) + ". An empty table does not establish no injuries.")
    with path.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run(output: Path, cache: Path, database: Path, personnel: Path, scheme_notes: Path | None = None, coaching: Path | None = None):
    now = datetime.now(timezone.utc)
    store = TeamProfileStore(database)
    games = [game for game in load_games(cache) if game["season"] == 2026]
    for game in games:
        url = f"{BASE}/summary?event={game['id']}"
        path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        raw_bytes = path.read_bytes()
        source = {"reference": url, "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                  "archive_file": str(path.resolve()),
                  "observed_at": now.isoformat(), "kind": "CACHED_FINAL_BOX_READ_CURRENTLY",
                  "historical_pregame_version_verified": False}
        store.add_game(game_observations(game, json.loads(raw_bytes), source, now))
    context = json.loads(personnel.read_text(encoding="utf-8"))
    notes = json.loads(scheme_notes.read_text(encoding="utf-8"))["notes"] if scheme_notes else []
    coaching_data = json.loads(coaching.read_text(encoding="utf-8"))["teams"] if coaching else {}
    if coaching and set(coaching_data) != set(DOMAINS):
        raise ValueError("coaching input must cover all 32 teams")
    profiles = {}
    for team in DOMAINS:
        profile = store.profile(team, now)
        sources = [source for source in context["sources"] if source["team"] == team]
        if any(timestamp(source["retrieved_at"]) > now for source in sources):
            raise ValueError("future personnel context")
        profile["personnel"] = {"status": "CURRENT_SOURCE_CONTEXT_NOT_GAME_DAY_ELIGIBILITY",
                                "players": context["candidate_profiles"][team],
                                "source_records": [{k: s.get(k) for k in ("kind", "reference", "retrieved_at", "content_sha256", "status")}
                                                   for s in sources],
                                "injury_report_rows": next((s["rows"] for s in sources if s["kind"] == "injury"), [])}
        profile["version"] = content_hash({k: v for k, v in profile.items() if k != "version"})
        for note in notes:
            if note["team_id"] == team:
                profile = add_scheme_note(profile, note)
        if team in coaching_data:
            profile = attach_coaching(profile, coaching_data[team])
        profiles[team] = profile
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    artifact = {"kind": "CURRENT_DESCRIPTIVE_TEAM_PROFILES", "as_of": now.isoformat(),
                "historically_available_before_games": False, "profiles": profiles,
                "warning": "Final-box descriptions and current personnel only. Advanced scheme fields remain unavailable; no fitted matchup predictor or sportsbook prices.",
                "example_matchup": matchup_packet(profiles["BAL"], profiles["JAX"], now)}
    target = output / f"team-profiles-{stamp}.json"
    with target.open("x", encoding="utf-8") as handle:
        json.dump(artifact, handle, indent=2, allow_nan=False)
    team_directory = output / (target.stem + "-individual")
    team_directory.mkdir()
    for team, profile in profiles.items():
        write_team_report(team_directory / (team + ".md"), profile)
    lines = ["# Team profiles and matchup context", "", artifact["warning"], "",
             "All 32 profiles have season and recent-four-game offense/defense results, denominators, game history, last-game evidence, source hashes and roster/depth/injury context. Missing advanced scheme data remains explicit. Points include all scoring; sacks per dropback use attempts plus sacks, excluding scrambles. Box red-zone conversion is not a player's red-zone opportunity.", "",
             "| Team | Games | Recent offensive yards/play | Recent opponent yards/play | Profile version |", "|---|---:|---:|---:|---|"]
    for team, profile in profiles.items():
        own = profile["recent"]["offense"]["yards_per_play"]["value"]
        allowed = profile["recent"]["defense_allowed"]["yards_per_play"]["value"]
        report_path = (team_directory / (team + '.md')).as_posix()
        lines.append(f"| [{team}]({report_path}) | {profile['games']} | {own:.3f} | {allowed:.3f} | {profile['version'][:12]} |")
    if coaching_data:
        lines.extend(["", "## Coaching overview", "", "Caller labels distinguish confirmed reports, dated preseason assignments and unresolved responsibilities. A coordinator title alone does not verify calling duties. All 32 individual profiles contain sourced descriptions of offensive/defensive approach, leadership, scheme limitations and relevant changes.", "",
                      "| Team | Head coach | Offensive coordinator | Defensive coordinator | Offensive caller | Defensive caller |", "|---|---|---|---|---|---|"])
        for team, profile in profiles.items():
            c = profile["coaching"]
            names = [c["staff"][role]["name"] or "Not separately listed" for role in ("head_coach", "offensive_coordinator", "defensive_coordinator")]
            calls = [f"{c['play_callers'][side]['name'] or 'Unconfirmed'} ({c['play_callers'][side]['status']})" for side in ("offense", "defense")]
            lines.append("| " + " | ".join([team] + names + calls) + " |")
    lines.extend(["", "## Bears–Seahawks update", "",
                  "Chicago won 34–17. Seattle recorded 252 yards on 56 plays, converted 1 of 11 third downs, and allowed 3 sacks on 34 passing attempts. These same observations update Chicago's defense and Seattle's offense. Chicago's sack rate in this game is 3/(34+3) = 8.11% using the stated proxy; this is not pressure rate or time in the pocket.", "",
                  "The event informs both recent and season profiles; the model does not infer that one result permanently establishes a scheme advantage. Trade-era comparisons need more games and opponent/lineup context.", "",
                  "## Matchup to parlay workflow", "",
                  "1. Select both dated profiles, such as Baltimore and Jacksonville; compare each offense with the opposing defense.",
                  "2. Reconcile current player identities, trades, injuries and game-day status; supply supported scheme and role evidence.",
                  "3. Bind the team-profile versions to a game snapshot. A new binding invalidates old scenarios.",
                  "4. Build scenario-specific player opportunities, efficiencies, separate TD opportunities and joint samples.",
                  "5. Feed real offered lines into the 3/5/8-leg and three-player anytime-TD family builder. Existing engine checks still apply; exact combined quotes are required for price screening.", "",
                  "The matchup packet does not invent those missing inputs. There are no new approved wagers or verified historical parlay results. This command performs one import; recurring refresh is managed separately. Scheme interpretation requires reviewed evidence.", "",
                  f"Full profiles, player context, metrics and a Baltimore–Jacksonville example: {target.name}", ""])
    target.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"profiles": len(profiles), "games": len(games), "artifact": str(target)}, indent=2))
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("output", "cache", "database", "personnel"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--scheme-notes", type=Path)
    parser.add_argument("--coaching", type=Path)
    args = parser.parse_args()
    run(args.output, args.cache, args.database, args.personnel, args.scheme_notes, args.coaching)
