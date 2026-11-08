"""Versioned team evidence, paired matchup context and parlay-family binding."""
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from .team_metrics import TEAM_CATALOG,TEAM_VIEWS

from .construction import construct_tickets
from .engine import Leg, Snapshot


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not parsed.tzinfo:
        raise ValueError("aware timestamp required")
    return parsed


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class TeamProfileStore:
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path)
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS team_observations (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              team TEXT NOT NULL, game TEXT NOT NULL, available_at TEXT NOT NULL,
              content_hash TEXT NOT NULL, payload TEXT NOT NULL,
              UNIQUE(team, game, content_hash));
            CREATE TRIGGER IF NOT EXISTS team_observation_update BEFORE UPDATE ON team_observations
              BEGIN SELECT RAISE(ABORT, 'immutable team observation'); END;
            CREATE TRIGGER IF NOT EXISTS team_observation_delete BEFORE DELETE ON team_observations
              BEGIN SELECT RAISE(ABORT, 'immutable team observation'); END;
        """)

    def add_game(self, observations: list[dict]):
        if (len(observations) != 2 or len({o["team"] for o in observations}) != 2
                or len({o["game_id"] for o in observations}) != 1
                or any(o["opponent"] != other["team"] for o, other in
                       ((observations[0], observations[1]), (observations[1], observations[0])))):
            raise ValueError("paired team observations required")
        with self.connection:
            for item in observations:
                if (not item.get("final") or timestamp(item["kickoff"]) >= timestamp(item["available_at"])
                        or not item["source"].get("reference") or not item["source"].get("sha256")
                        or timestamp(item["source"]["observed_at"]) > timestamp(item["available_at"])):
                    raise ValueError("final sourced observation required")
                # Retrieval timestamp is excluded from content identity. An unchanged
                # box stays idempotent; corrections append a new version.
                identity = {k: v for k, v in item.items() if k not in ("source", "available_at")}
                identity["source_sha256"] = item["source"]["sha256"]
                digest = content_hash(identity)
                self.connection.execute("INSERT OR IGNORE INTO team_observations(team,game,available_at,content_hash,payload) VALUES(?,?,?,?,?)",
                    (item["team"], item["game_id"], item["available_at"], digest, json.dumps(item, allow_nan=False)))

    def profile(self, team: str, as_of: datetime, *, recent_games: int = 4) -> dict:
        if not as_of.tzinfo or recent_games < 1:
            raise ValueError("aware profile time and positive window required")
        # Select the latest known correction for each game, never future evidence.
        latest = {}
        for (raw,) in self.connection.execute("SELECT payload FROM team_observations WHERE team=? ORDER BY sequence", (team,)):
            item = json.loads(raw)
            if timestamp(item["available_at"]) <= as_of:
                latest[item["game_id"]] = item
        history = sorted(latest.values(), key=lambda item: (timestamp(item["kickoff"]), item["game_id"]))
        season = history[-1]["season"] if history else None
        season_history = [item for item in history if item["season"] == season]
        payload = {"team_id": team, "as_of": as_of.isoformat(), "games": len(history),
                   "quant_metric_library":{"schema_version":"team-quant-v1","metric_slots":len(TEAM_CATALOG),
                       "metric_ids":[metric['id'] for metric in TEAM_CATALOG],"analytical_views":[key for key,_ in TEAM_VIEWS],
                       "numerical_binding":"REQUIRES_VERSIONED_PLAY_STORE","predictive_model_validated":False},
                   "game_ids": [item["game_id"] for item in history],
                   "game_observations": history,
                   "season_year": season, "season_games": len(season_history),
                   "season": summarize(season_history), "recent": summarize(history[-recent_games:]),
                   "last_game": history[-1] if history else None,
                   "sources": [item["source"] for item in history],
                   "scheme_notes": [],
                   "scheme": {key: {"status": "UNAVAILABLE", "value": None} for key in
                              ("coverage_mix", "blitz_rate", "pressure_rate", "time_to_throw",
                               "personnel_groupings", "route_participation", "red_zone_roles", "play_action_rate")},
                   "interpretation": "Descriptive final-box evidence, not opponent-adjusted or calibrated forecasts."}
        return {**payload, "version": content_hash(payload)}


def add_scheme_note(profile: dict, note: dict) -> dict:
    if (note["team_id"] != profile["team_id"] or not note.get("detail")
            or note["status"] not in ("REPORTED_GAME_OBSERVATION", "MATCHUP_HYPOTHESIS")
            or timestamp(note["source"]["observed_at"]) > timestamp(profile["as_of"])
            or not note["source"].get("reference") or not note["source"].get("sha256")):
        raise ValueError("invalid or future scheme evidence")
    updated = {**profile, "scheme_notes": profile.get("scheme_notes", []) + [note]}
    updated["version"] = content_hash({k: v for k, v in updated.items() if k != "version"})
    return updated


def attach_team_quant(profile: dict, quant: dict) -> dict:
    """Bind an explicit play-store result; never convert ranks into forecasts."""
    if (quant.get('team')!=profile['team_id'] or quant.get('season')!=profile.get('season_year')
            or content_hash({k:v for k,v in profile.items() if k!='version'})!=profile['version']):
        raise ValueError('team quant identity, season or profile hash mismatch')
    cutoff=timestamp(profile['as_of'])
    if any(timestamp(source['observed_at'])>cutoff for source in quant.get('sources',[])):
        raise ValueError('future team quant source')
    expected={g['game_id'] for g in profile['game_observations'] if g['season']==profile['season_year']}
    observed={str(int(float(g['data']['espn']))) for g in quant['games'] if g['data'].get('espn')}
    missing=sorted(expected-observed)
    payload={key:quant[key] for key in ('team','season','view','metrics','coverage','sources','gap_offense','gap_defense','model_status','historically_pregame_certified')}
    payload.update(missing_current_season_game_ids=missing,forecast_available=False,
                   calculation_revision=quant.get('calculation_revision'),definition_revision=quant.get('definition_revision'))
    updated={**profile,'team_quant':payload}
    if missing:updated['blocking_review_reasons']=profile.get('blocking_review_reasons',[])+['team quant play coverage missing current-season finals']
    updated['version']=content_hash({k:v for k,v in updated.items() if k!='version'})
    return updated


def summarize(history: list[dict]) -> dict:
    def metric(side, numerator, denominator=None):
        rows = [item[side] for item in history if item[side].get(numerator) is not None
                and (denominator is None or item[side].get(denominator) is not None)]
        total = sum(row[numerator] for row in rows)
        exposure = sum(row[denominator] for row in rows) if denominator else len(rows)
        return {"value": total / exposure if exposure > 0 else None,
                "numerator": total, "denominator": exposure, "games": len(rows)}
    result = {}
    for label, side in (("offense", "offense"), ("defense_allowed", "opponent_offense")):
        result[label] = {
            **{name + "_per_game": metric(side, name) for name in
               ("points", "plays", "drives", "passing_attempts", "rushing_attempts", "total_yards", "passing_td", "rushing_td")},
            "yards_per_play": metric(side, "total_yards", "plays"),
            "gross_passing_yards_per_attempt": metric(side, "gross_passing_yards", "passing_attempts"),
            "rushing_yards_per_carry": metric(side, "rushing_yards", "rushing_attempts"),
            "sacks_per_dropback_proxy": metric(side, "sacks_taken", "dropbacks_proxy"),
            "third_down_conversion": metric(side, "third_down_made", "third_down_attempts"),
            "box_red_zone_conversion": metric(side, "red_zone_made", "red_zone_attempts"),
            "passing_td_per_attempt": metric(side, "passing_td", "passing_attempts"),
        }
    return result


def matchup_packet(first: dict, second: dict, as_of: datetime) -> dict:
    if not as_of.tzinfo or first["team_id"] == second["team_id"]:
        raise ValueError("two distinct teams and aware time required")
    profiles = (first, second)
    for profile in profiles:
        if (timestamp(profile["as_of"]) > as_of or not profile["games"]
                or content_hash({k: v for k, v in profile.items() if k != "version"}) != profile["version"]):
            raise ValueError("missing, future or altered team profile")
    comparisons = []
    for offense, defense in ((first, second), (second, first)):
        from .coaching import coaching_matchup_context
        comparisons.append({"offense": offense["team_id"], "defense": defense["team_id"],
                            "offense_recent": offense["recent"]["offense"],
                            "opponent_recent_allowed": defense["recent"]["defense_allowed"],
                            "coaching": coaching_matchup_context(offense, defense),
                            "offensive_quant_context":offense.get('team_quant'),
                            "defensive_quant_context":defense.get('team_quant')})
    return {"teams": [p["team_id"] for p in profiles], "as_of": as_of.isoformat(),
            "blocking_review_reasons": [f"{p['team_id']}: {reason}" for p in profiles for reason in p.get("blocking_review_reasons", [])],
            "profile_versions": {p["team_id"]: p["version"] for p in profiles},
            "comparisons": comparisons, "status": "REQUIRES_PERSONNEL_AND_JOINT_SCENARIO_MODEL",
            "missing": ["verified current eligibility and identity mapping", "supported scheme and role inputs",
                        "scenario-specific player opportunities and joint samples", "actual offered lines and combined quotes"]}


def bind_matchup(snapshot: Snapshot, packet: dict) -> Snapshot:
    if packet.get("blocking_review_reasons"):
        raise ValueError("matchup source review required before binding")
    if (timestamp(packet["as_of"]) > snapshot.as_of or len(set(packet["teams"])) != 2
            or set(packet["profile_versions"]) != set(packet["teams"])
            or any(player.team_id not in packet["teams"] for player in snapshot.players.values())):
        raise ValueError("matchup does not match snapshot")
    versions = packet["profile_versions"]
    # First binding also requires a rebuild; old scenarios have no proof that
    # they used the selected team versions.
    return replace(snapshot, team_profile_versions=dict(versions),
                   scenarios=snapshot.scenarios if snapshot.team_profile_versions == versions else ())


def construct_parlay_family(snapshot: Snapshot, packet: dict, offered: tuple[Leg, ...]) -> dict:
    if (timestamp(packet["as_of"]) > snapshot.as_of
            or snapshot.team_profile_versions != packet["profile_versions"]
            or any(leg.team_id not in packet["teams"] for leg in offered)):
        raise ValueError("rebuild and bind the selected matchup first")
    return {kind: construct_tickets(snapshot, tuple(leg for leg in offered if leg.market == "anytime_td")
                                   if kind == "TD" else offered, length)
            for kind, length in (("3", 3), ("5", 5), ("8", 8), ("TD", 3))}
