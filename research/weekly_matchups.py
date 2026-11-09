"""Weekly calendar and exact-game recorded research tickets; no wager creation."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Lock

import requests

from research.team_schedule import BASE, instant
from research.source_based_parlays import DOMAINS


def parse_week(payload, expected=None):
    if not any(l.get("abbreviation") == "NFL" for l in payload.get("leagues", [])):
        raise ValueError("NFL schedule identity missing")
    season = int(payload["season"]["year"])
    kind = int(payload["season"]["type"])
    week = int(payload["week"]["number"])
    if expected and (season, kind, week) != expected:
        raise ValueError("returned calendar does not match selected week")
    games = []
    seen = set()
    for event in payload.get("events", []):
        identity = str(event["id"])
        if identity in seen:
            raise ValueError("duplicate game in weekly schedule")
        seen.add(identity)
        if len(event.get("competitions", [])) != 1:
            raise ValueError("ambiguous game competition")
        competition = event["competitions"][0]
        competitors = competition["competitors"]
        if len(competitors) != 2 or {c["homeAway"] for c in competitors} != {"home", "away"}:
            raise ValueError("invalid game opponents")
        teams = {c["homeAway"]: c["team"] for c in competitors}
        if any(t["abbreviation"] not in DOMAINS for t in teams.values()) or teams["home"]["abbreviation"] == teams["away"]["abbreviation"]:
            raise ValueError("unknown or duplicate game teams")
        status = competition.get("status", event.get("status", {})).get("type", {})
        kickoff = instant(event["date"])
        venue = competition.get("venue", {})
        games.append({"game_id": identity, "season": season, "season_type": kind, "week": week,
                      "kickoff": kickoff.isoformat(),
                      "kickoff_confirmed": not competition.get("isTBDFlex", False) and "TBD" not in str(status.get("detail", "")),
                      "home": teams["home"]["abbreviation"], "away": teams["away"]["abbreviation"],
                      "home_name": teams["home"].get("displayName", teams["home"]["abbreviation"]),
                      "away_name": teams["away"].get("displayName", teams["away"]["abbreviation"]),
                      "venue": venue.get("fullName"), "venue_id": str(venue.get("id", "")),
                      "venue_country": venue.get("address", {}).get("country"),
                      "venue_indoor": venue.get("indoor"),
                      "state": status.get("state", "unknown"), "status_detail": status.get("detail", "Status unavailable")})
    choices = []
    for league in payload["leagues"]:
        if league.get("abbreviation") != "NFL": continue
        for period in league.get("calendar", []):
            for entry in period.get("entries", []):
                choices.append({"season": season, "season_type": int(period["value"]),
                                "week": int(entry["value"]), "label": entry["label"]})
    return {"season": season, "season_type": kind, "week": week,
            "games": sorted(games, key=lambda g: (instant(g["kickoff"]), g["game_id"])),
            "weeks": choices, "byes": [t["abbreviation"] for t in payload["week"].get("teamsOnBye", []) if t.get("abbreviation") in DOMAINS]}


class WeeklyProvider:
    def __init__(self, archive_dir=None, *, get=requests.get, clock=None):
        self.archive_dir = Path(archive_dir) if archive_dir else None
        self.get = get
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache = {}
        self.lock = Lock()

    def week(self, season=None, kind=None, week=None):
        explicit = any(v is not None for v in (season, kind, week))
        if explicit and any(v is None for v in (season, kind, week)):
            raise ValueError("season, season type and week must be selected together")
        expected = tuple(int(v) for v in (season, kind, week)) if explicit else None
        if expected and not (1999 <= expected[0] <= 2100 and expected[1] in (1, 2, 3) and 1 <= expected[2] <= 25):
            raise ValueError("invalid calendar selection")
        key = expected or "current"
        now = self.clock()
        with self.lock:
            cached = self.cache.get(key)
            if cached and 0 <= (now - cached[0]).total_seconds() < 300:
                return cached[1]
            reference = BASE + "/scoreboard?limit=1000"
            if expected:
                reference += f"&dates={expected[0]}&seasontype={expected[1]}&week={expected[2]}"
            response = self.get(reference, timeout=(5, 15), allow_redirects=False)
            response.raise_for_status()
            if response.status_code != 200: raise ValueError("weekly schedule retrieval failed")
            raw = response.content
            data = parse_week(json.loads(raw), expected)
            observed = self.clock()
            digest = hashlib.sha256(raw).hexdigest()
            source = {"reference": reference, "observed_at": observed.isoformat(), "sha256": digest}
            if self.archive_dir:
                self.archive_dir.mkdir(parents=True, exist_ok=True)
                path = self.archive_dir / (digest + ".json")
                if not path.exists():
                    with path.open("xb") as stream: stream.write(raw)
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest: raise ValueError("schedule archive mismatch")
                source["archive_file"] = str(path.resolve())
            data.update(status="AVAILABLE", source=source)
            self.cache[key] = (observed, data)
            return data


def game_tiers(game, ledger_path=None, *, as_of=None):
    """Expose genuine frozen proposals only; no historical replay substitutions."""
    tiers = [{"tier": i, "leg_count": count, "market_family": "mixed",
              "status": "NOT_GENERATED", "tickets": []}
             for i, count in enumerate((3, 5, 8), 1)]
    tiers.append({"tier": "TD", "leg_count": 3, "market_family": "anytime_td",
                  "status": "NOT_GENERATED", "tickets": []})
    report = {"game_id": game["game_id"], "tiers": tiers, "book": "FanDuel", "state": "Tennessee",
              "betting_ready": False, "status": "NO_RECORDED_TICKETS", "source": None,
              "excluded_research_count": 0,
              "reason": "No frozen game-specific tickets are available. Forecasts, player eligibility and actual offered lines are required."}
    if ledger_path is None:
        return report
    try:
        from football_engine.ledger import _replay
        cutoff = as_of or datetime.now(timezone.utc)
        path = Path(ledger_path).resolve(strict=True)
        wal = Path(str(path) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise ValueError("ledger must be checkpointed before display")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute("SELECT ticket_id,kind,recorded_at,payload,previous_hash,event_hash FROM events ORDER BY sequence").fetchall()
        proposals, settlements = _replay(rows, cutoff)
        for ticket, (proposal, recorded) in proposals.items():
            if proposal["game_id"] != game["game_id"]: continue
            snapshot = proposal["snapshot"]
            legs = proposal["legs"]
            if instant(snapshot["kickoff"]) != instant(game["kickoff"]): continue
            decision_cutoff = instant(game["kickoff"]) - timedelta(minutes=30)
            if (recorded > decision_cutoff
                    or instant(snapshot["as_of"]) > decision_cutoff):
                report["excluded_research_count"] += 1
                continue
            if instant(snapshot["as_of"]) >= instant(game["kickoff"]):
                report["excluded_research_count"] += 1
                continue
            if {leg["team_id"] for leg in legs} != {game["home"], game["away"]}: continue
            quote = proposal.get("quote")
            if proposal["decision"].get("approved") is not True or quote is None:
                report["excluded_research_count"] += 1
                continue
            if quote.get("source", {}).get("provider") != "FanDuel":
                report["excluded_research_count"] += 1
                continue
            family = "anytime_td" if len(legs) == 3 and all(
                leg["market"] == "anytime_td" and leg["line"] == 1 for leg in legs
            ) else "mixed"
            tier = next((t for t in tiers if t["leg_count"] == len(legs)
                         and t["market_family"] == family), None)
            if tier is None: continue
            tier["tickets"].append({"ticket_id": ticket, "recorded_at": recorded.isoformat(),
                                    "legs": [{k: leg[k] for k in ("player_id", "team_id", "market", "line")} for leg in legs],
                                    "quote": quote, "decision_status": "RESEARCH_ONLY",
                                    "reasons": proposal["decision"].get("reasons", []),
                                    "settlement_status": settlements.get(ticket, {}).get("status", "unresolved"),
                                    "price_status": "FROZEN_OBSERVATION_NOT_LIVE" if quote else "UNAVAILABLE",
                                    "tennessee_quote_verified": False})
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest: raise ValueError("ledger changed during display")
        for tier in tiers:
            if tier["tickets"]: tier["status"] = "RECORDED_RESEARCH"
        report["source"] = {"path": str(path), "sha256": digest}
        if any(t["tickets"] for t in tiers):
            report.update(status="RECORDED_RESEARCH", reason="Frozen research observations, not live executable offers. Tennessee book availability is not certified.")
        elif report["excluded_research_count"]:
            report.update(status="NO_DISPLAYABLE_PREGAME_TICKETS",
                          reason="Recorded drafts were excluded because they lacked cutoff, approval or exact combined FanDuel price evidence.")
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error):
        for tier in tiers: tier.update(status="UNAVAILABLE", tickets=[])
        report.update(status="UNAVAILABLE", reason="The configured ticket ledger could not be verified.")
    return report
