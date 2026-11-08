"""Source-backed coaching descriptions, distinct from measured model inputs."""
from datetime import datetime, timedelta
from math import isfinite
import re
import unicodedata
from zoneinfo import ZoneInfo

from .team_profiles import content_hash, timestamp, summarize


ROLES = ("head_coach", "offensive_coordinator", "defensive_coordinator")
SCOPES = {"CURRENT_ROLE", "REPORTED_2026_PLAN", "CURRENT_SEASON_OBSERVATION", "HISTORICAL_BACKGROUND"}


def coach_identity(name):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", name or "").casefold())


def attach_coaching(profile: dict, coaching: dict) -> dict:
    as_of = timestamp(profile["as_of"])
    if coaching["team_id"] != profile["team_id"] or set(coaching["staff"]) != set(ROLES):
        raise ValueError("complete matching coaching role records required")
    verified = timestamp(coaching["directory_verified_at"])
    if verified > as_of or as_of - verified > timedelta(days=7):
        raise ValueError("coaching directory is future or stale; refresh staff verification")
    for role, staff in coaching["staff"].items():
        if not staff.get("name") and not (role == "defensive_coordinator" and staff["status"] == "NOT_SEPARATELY_LISTED"):
            raise ValueError("principal coaching role cannot be silently omitted")
    # Evidence is attached to each fact, not just a generic team URL.
    records = list(coaching["staff"].values()) + list(coaching["play_callers"].values())
    records += [coaching["head_coach_management"], coaching["offense"], coaching["defense"]]
    records += coaching.get("changes", [])
    records += coaching.get("reported_rates", [])
    records += [coaching[side]["foundation"] for side in ("offense", "defense") if "foundation" in coaching[side]]
    for record in records:
        if not record.get("sources"):
            raise ValueError("coaching fact requires sources")
        for source in record["sources"]:
            if (not source.get("reference") or not source.get("sha256")
                    or timestamp(source["observed_at"]) > as_of):
                raise ValueError("missing or future coaching source")
    for side in ("offense", "defense"):
        if coaching[side]["scope"] not in SCOPES or not coaching[side].get("description"):
            raise ValueError("invalid scheme scope")
        foundation = coaching[side].get("foundation")
        if foundation and (foundation["scope"] not in SCOPES or coach_identity(foundation["coach"]) not in
                           {coach_identity(s["name"]) for s in coaching["staff"].values()}):
            raise ValueError("scheme foundation belongs to an unreconciled coach or scope")
    for caller in coaching["play_callers"].values():
        if caller["status"] not in ("CONFIRMED", "UNCONFIRMED", "DATED_PRESEASON_REPORT") or (caller["status"] != "UNCONFIRMED" and not caller.get("name")):
            raise ValueError("invalid play-caller evidence")
        if caller.get("name") and coach_identity(caller["name"]) not in {coach_identity(s["name"]) for s in coaching["staff"].values()}:
            raise ValueError("play-caller is not reconciled with coaching staff")
    for rate in coaching.get("reported_rates", []):
        if (not isfinite(rate["value"]) or not 0 <= rate["value"] <= 1
                or not rate.get("window") or not rate.get("definition")):
            raise ValueError("reported coaching rate needs a bounded value and explicit scope")
    # Date-only reports do not authorize precise intraday era assignments.
    era = {}
    for side in ("offense", "defense"):
        start = coaching["era_start_dates"].get(side)
        observations = profile.get("game_observations", [])
        if start:
            boundary = datetime.strptime(start, "%Y-%m-%d").date()
            current = [item for item in observations
                       if timestamp(item["kickoff"]).astimezone(ZoneInfo("America/New_York")).date() > boundary]
            ambiguous = [item["game_id"] for item in observations
                         if timestamp(item["kickoff"]).astimezone(ZoneInfo("America/New_York")).date() == boundary]
            era[side] = {"boundary_date": start, "boundary_precision": "date_only",
                         "games": len(current), "same_day_excluded": ambiguous,
                         "metrics": summarize(current)["offense" if side == "offense" else "defense_allowed"],
                         "status": "DESCRIPTIVE_ONLY" if current else "NO_POST_CHANGE_GAME_EVIDENCE"}
        else:
            era[side] = {"status": "ERA_BOUNDARY_UNVERIFIED", "games": None, "metrics": None}
    updated = {**profile, "coaching": coaching, "coaching_era": era}
    updated["version"] = content_hash({k: v for k, v in updated.items() if k != "version"})
    return updated


def coaching_matchup_context(offense: dict, defense: dict) -> dict:
    own, opponent = offense.get("coaching"), defense.get("coaching")
    if not own or not opponent:
        return {"status": "COACHING_CONTEXT_UNAVAILABLE"}
    return {"status": "QUALITATIVE_CONTEXT_REQUIRES_SCENARIO_VALIDATION",
            "offensive_play_caller": own["play_callers"]["offense"],
            "defensive_play_caller": opponent["play_callers"]["defense"],
            "offensive_scheme": own["offense"], "defensive_scheme": opponent["defense"],
            "offense_era": offense["coaching_era"]["offense"],
            "defense_era": defense["coaching_era"]["defense"],
            "automatic_probability_multiplier": None,
            "review": ["run concepts versus defensive front and gap assignments",
                       "protection versus pressure origin and coverage disguise",
                       "route roles versus man/zone and personnel matchups",
                       "red-zone rushing and receiving roles separately from yardage",
                       "tempo, game management, installation and lineup uncertainty"]}
