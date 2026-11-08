import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlparse, parse_qs
from math import isfinite
from pathlib import Path
from copy import deepcopy

from .ledger import Ledger, _time, _ticket_status


def fetch_final_box(game_id: str) -> tuple[dict, dict]:
    if not isinstance(game_id, str) or not game_id.isdigit():
        raise ValueError("ESPN game ID must be numeric")
    url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={game_id}"
    with urlopen(Request(url, headers={"User-Agent": "CaseCapitalFootballBetting/0.1"}), timeout=15) as response:
        raw = response.read()
    box = json.loads(raw)
    return box, {"provider": "ESPN", "reference": url,
                             "sha256": hashlib.sha256(raw).hexdigest(),
                             "content_sha256": hashlib.sha256(json.dumps(box, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                             "retrieved_at": datetime.now(timezone.utc).isoformat()}


def _stat_number(value):
    if not isinstance(value, str) or not re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", value):
        raise ValueError("invalid final statistic")
    number = float(value.replace(",", ""))
    if not isfinite(number):
        raise ValueError("nonfinite final statistic")
    return number


def _final_totals(box: dict) -> dict:
    try:
        return _parse_final_totals(box)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise ValueError("malformed final box score") from exc


def _parse_final_totals(box: dict) -> dict[str, dict[str, float]]:
    competitions = box.get("header", {}).get("competitions", [])
    if len(competitions) != 1 or competitions[0].get("status", {}).get("type", {}).get("name") != "STATUS_FINAL":
        raise ValueError("game is not final")
    expected_teams = {item["team"]["abbreviation"] for item in competitions[0].get("competitors", [])}
    players = box.get("boxscore", {}).get("players", [])
    if len(competitions[0].get("competitors", [])) != 2 or len(expected_teams)!=2 or len(players) != 2 or {item["team"]["abbreviation"] for item in players} != expected_teams:
        raise ValueError("incomplete final box score")
    totals: dict[str, dict[str, float]] = {}
    sections = set()
    seen=set()
    for team in players:
        abbreviation = team["team"]["abbreviation"]
        for section in team.get("statistics", []):
            market = {"passing": "passing_yards", "rushing": "rushing_yards",
                      "receiving": "receiving_yards"}.get(section.get("name"))
            if not market:
                continue
            section_key = (abbreviation, market)
            if section_key in sections:
                raise ValueError("duplicate team market in final box")
            sections.add(section_key)
            labels = section.get("labels", [])
            if labels.count("YDS") != 1 or labels.count("TD") != 1:
                continue
            try:
                yards_index = labels.index("YDS")
                touchdown_index = labels.index("TD")
            except ValueError:
                continue
            for entry in section.get("athletes", []):
                athlete = entry.get("athlete", {})
                player_id = str(athlete.get("id", ""))
                stats = entry.get("stats", [])
                if not player_id or max(yards_index, touchdown_index) >= len(stats):
                    continue
                identity=(abbreviation,section.get('name'),player_id)
                if identity in seen:raise ValueError('duplicate player market in final box')
                seen.add(identity)
                if player_id in totals and totals[player_id]['team_id']!=abbreviation:
                    raise ValueError('player appears for opposing teams in final box')
                record = totals.setdefault(player_id, {"team_id": abbreviation})
                try:
                    record[market] = _stat_number(stats[yards_index])
                    if not isfinite(record[market]) or record[market] % 1:raise ValueError('invalid final yards')
                    if market == "passing_yards":
                        record["passing_td"] = _stat_number(stats[touchdown_index])
                        if not isfinite(record['passing_td']) or record['passing_td']<0 or record['passing_td']%1:raise ValueError('invalid touchdowns')
                    else:
                        td=_stat_number(stats[touchdown_index])
                        if not isfinite(td) or td<0 or td%1:raise ValueError('invalid touchdowns')
                        record["anytime_td"] = record.get("anytime_td", 0) + td
                except (ValueError, AttributeError, TypeError, OverflowError):
                    record.pop(market, None)
                    if market=='passing_yards':record.pop('passing_td',None)
                    else:record['_td_incomplete']=True
    for record in totals.values():
        if record.pop('_td_incomplete',False):record.pop('anytime_td',None)
    return totals


def _content_hash(box):
    return hashlib.sha256(json.dumps(box, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _validate_final_source(box, source, proposal, recorded, *, require_archive=False):
    try:
        return _parse_final_source(box, source, proposal, recorded, require_archive=require_archive)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise ValueError("malformed final source evidence") from exc


def _parse_final_source(box, source, proposal, recorded, *, require_archive=False):
    if not isinstance(box, dict) or not isinstance(source, dict) or not all(
            source.get(key) for key in ("provider", "reference", "sha256", "content_sha256", "retrieved_at")):
        raise ValueError("final box source proof required")
    if source["content_sha256"] != _content_hash(box):
        raise ValueError("final box content changed after retrieval")
    parsed = urlparse(source["reference"])
    game_id = proposal["game_id"]
    header = box.get("header", {})
    if (source["provider"] != "ESPN" or parsed.scheme != "https" or parsed.netloc != "site.api.espn.com"
            or parsed.path != "/apis/site/v2/sports/football/nfl/summary" or parsed.fragment
            or parse_qs(parsed.query).get("event") != [game_id] or str(header.get("id")) != game_id):
        raise ValueError("final box source does not match game")
    totals = _final_totals(box)
    competition = header["competitions"][0]
    if "id" in competition and str(competition["id"]) != game_id:
        raise ValueError("final competition does not match game")
    teams = {item["team"]["abbreviation"] for item in competition["competitors"]}
    frozen_teams = {leg["team_id"] for leg in proposal["legs"]}
    frozen_teams.update(player["team_id"] for player in proposal["snapshot"].get("players", {}).values())
    state = proposal["snapshot"].get("state")
    if state:
        frozen_teams.update(state.get("scores", {}))
    if not frozen_teams <= teams:
        raise ValueError("final box teams do not match frozen game")
    kickoff = _time(proposal["snapshot"]["kickoff"])
    if competition.get("date") and _time(competition["date"]) != kickoff:
        raise ValueError("final box kickoff does not match frozen game")
    retrieved = _time(source["retrieved_at"])
    if not kickoff <= retrieved <= recorded:
        raise ValueError("final source retrieval chronology invalid")
    raw_hash = source["sha256"]
    if not isinstance(raw_hash, str) or len(raw_hash) != 64 or any(c not in "0123456789abcdef" for c in raw_hash):
        raise ValueError("final source raw hash invalid")
    if require_archive or source.get("archive_file"):
        path = Path(source.get("archive_file", ""))
        if not path.is_absolute() or not path.is_file():
            raise ValueError("absolute existing final source archive required")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != raw_hash:
            raise ValueError("final source archive hash mismatch")
        if _content_hash(json.loads(raw)) != source["content_sha256"]:
            raise ValueError("final source archive differs from frozen box")
    return totals


def _settled_legs(proposal, box, source):
    totals = _final_totals(box)
    results = []
    for leg in proposal["legs"]:
        player = totals.get(leg["player_id"], {})
        observed = player.get(leg["market"]) if player.get("team_id") == leg["team_id"] else None
        # Rushing/receiving TD rows prove a positive anytime TD. Zero does not
        # exclude a return/recovery TD, absent from those offensive tables.
        if leg["market"] == "anytime_td" and (observed is None or observed < leg["line"]):
            observed = None
        identity = {"player_id": leg["player_id"], "team_id": leg["team_id"],
                    "market": leg["market"], "threshold": leg["line"]}
        if observed is None:
            results.append(dict(identity, status="unresolved"))
            continue
        won = observed >= leg["line"]
        results.append(dict(identity, status="won" if won else "lost", observed=observed,
                            predicted_probability=proposal["decision"]["leg_probabilities"].get(
                                f"{leg['player_id']}:{leg['market']}:{leg['line']:g}"),
                            odds_decimal=leg["odds_decimal"], source=source,
                            observation_basis="offensive_td_lower_bound" if leg["market"] == "anytime_td" else "final_box_total",
                            error_category="none" if won else "unclassified"))
    return results


def settle_recorded_ticket(ledger: Ledger, ticket_id: str, box: dict, source: dict) -> dict:
    row = ledger.connection.execute(
        "SELECT payload FROM events WHERE ticket_id=? AND kind IN ('pregame','live')", (ticket_id,)).fetchone()
    if not row:
        raise ValueError("ticket has no frozen pregame record")
    proposal = json.loads(row[0])
    # Copy caller-owned structures before binding and freezing their content.
    box, source = deepcopy(box), deepcopy(source)
    from .ledger import _now
    _validate_final_source(box, source, proposal, _now())
    results = _settled_legs(proposal, box, source)
    payload = {"status": _ticket_status(results), "source": source, "legs": results, "final_box": box}
    ledger.append(ticket_id, "settlement", payload)
    return payload
