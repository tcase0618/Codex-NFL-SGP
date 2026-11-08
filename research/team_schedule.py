"""Current sourced next-game calendar, separate from historical metric filters."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

import requests

from research.source_based_parlays import DOMAINS

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("schedule kickoff requires timezone")
    return parsed


def next_game(payload, team, now):
    """Skip completed/cancelled games and calendar gaps; never guess a bye."""
    if payload.get("team", {}).get("abbreviation") != team:
        raise ValueError("schedule response team mismatch")
    candidates = []
    for event in payload.get("events", []):
        competitions = event.get("competitions", [])
        if len(competitions) != 1:
            continue
        competition = competitions[0]
        status = competition.get("status", event.get("status", {})).get("type", {})
        if status.get("state") not in {"pre", "in"} or status.get("completed") is True:
            continue
        if status.get("name") in {"STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_POSTPONED"}:
            continue
        kickoff = instant(event["date"])
        if kickoff < now and status.get("state") != "in":
            continue
        competitors = competition.get("competitors", [])
        if len(competitors) != 2 or {c.get("homeAway") for c in competitors} != {"home", "away"}:
            continue
        teams = {c["team"]["abbreviation"]: c for c in competitors}
        if len(teams) != 2 or team not in teams or any(t not in DOMAINS for t in teams):
            continue
        opponent = next(t for t in teams if t != team)
        venue = competition.get("venue", {})
        candidates.append({"game_id": str(event["id"]), "kickoff": kickoff.isoformat(),
                           "team": team, "opponent": opponent,
                           "opponent_name": teams[opponent]["team"].get("displayName", opponent),
                           "home_away": teams[team]["homeAway"],
                           "week": event.get("week", {}).get("number"),
                           "season": event.get("season", {}).get("year"),
                           "venue": venue.get("fullName"), "state": status["state"],
                           "status_detail": status.get("detail"),
                           "kickoff_confirmed": not competition.get("isTBDFlex", False)
                           and "TBD" not in str(status.get("detail", "")),
                           "days_until": (kickoff.astimezone(ZoneInfo("America/New_York")).date()
                                          - now.astimezone(ZoneInfo("America/New_York")).date()).days})
    return min(candidates, key=lambda game: instant(game["kickoff"])) if candidates else None


class ScheduleProvider:
    def __init__(self, archive_dir=None, *, get=requests.get, clock=None):
        self.archive_dir = Path(archive_dir) if archive_dir else None
        self.get = get
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache = {}
        self.lock = Lock()

    def schedule(self, team):
        if team not in DOMAINS:
            raise ValueError("unknown schedule team")
        now = self.clock()
        with self.lock:
            cached = self.cache.get(team)
            if cached and 0 <= (now - cached["observed"]).total_seconds() < 900:
                payload, source = cached["payload"], cached["source"]
            else:
                local = now.astimezone(ZoneInfo("America/New_York"))
                season = local.year - (local.month < 3)
                reference = f"{BASE}/teams/{team.lower()}/schedule?season={season}"
                try:
                    response = self.get(reference, timeout=(5, 15), allow_redirects=False)
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ValueError("schedule did not return a successful response")
                    raw = response.content
                    payload = json.loads(raw)
                    now = self.clock()
                    next_game(payload, team, now)  # validate before caching
                    source = {"reference": reference, "observed_at": now.isoformat(),
                              "sha256": hashlib.sha256(raw).hexdigest()}
                    if self.archive_dir:
                        self.archive_dir.mkdir(parents=True, exist_ok=True)
                        path = self.archive_dir / (source["sha256"] + ".json")
                        if not path.exists():
                            with path.open("xb") as stream:
                                stream.write(raw)
                        if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
                            raise ValueError("schedule archive hash mismatch")
                        source["archive_file"] = str(path.resolve())
                    self.cache[team] = {"payload": payload, "source": source, "observed": now}
                except (requests.RequestException, ValueError, KeyError, TypeError, OSError):
                    return {"status": "UNAVAILABLE", "team": team, "game": None,
                            "reason": "The current schedule could not be verified. Try again later.",
                            "source": None, "as_of": now.isoformat()}
        game = next_game(payload, team, now)
        return {"status": "AVAILABLE" if game else "NO_UPCOMING_GAME", "team": team,
                "game": game, "source": source, "as_of": now.isoformat(),
                "scope": "Current calendar; independent of historical profile season and view."}
