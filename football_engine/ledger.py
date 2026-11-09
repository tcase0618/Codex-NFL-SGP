import hashlib
import json
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from math import isfinite

from .engine import Leg, Snapshot, TicketQuote, evaluate_ticket, leg_key


def _now():
    return datetime.now(timezone.utc)


def _time(value):
    if not isinstance(value, str):
        raise ValueError('aware ledger time required')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.utcoffset() is None:raise ValueError('aware ledger time required')
    return parsed


def _json_ready(value):
    if is_dataclass(value):
        data=asdict(value)
        if hasattr(value,'use_status'):data['use_status']=value.use_status
        return _json_ready(data)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


MARKETS = {"passing_yards", "rushing_yards", "receiving_yards", "passing_td", "anytime_td"}
STATUSES = {"won", "lost", "void", "unresolved"}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    try:
        return type(value) in (int, float) and isfinite(value)
    except OverflowError:
        return False


def _key(leg):
    return f"{leg['player_id']}:{leg['market']}:{leg['line']:g}"


def _source_times(value, cutoff):
    if isinstance(value, dict):
        if "source" in value and (not isinstance(value["source"], dict) or "observed_at" not in value["source"]):
            raise ValueError("frozen nested source missing")
        if "archive_file" in value:
            path = Path(value["archive_file"])
            if not path.is_absolute() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != value.get("sha256"):
                raise ValueError("frozen source archive hash mismatch")
        if "observed_at" in value:
            if not _text(value.get("provider")) or not _text(value.get("reference")) or _time(value["observed_at"]) > cutoff:
                raise ValueError("frozen source provenance invalid or future")
        for item in value.values():
            _source_times(item, cutoff)
    elif isinstance(value, list):
        for item in value:
            _source_times(item, cutoff)


def _validate_proposal(payload, kind, recorded):
    if not all(key in payload for key in ("game_id", "snapshot", "decision", "legs")):
        raise ValueError("proposal requires game, snapshot, decision, and legs")
    game, decision, legs = payload["snapshot"], payload["decision"], payload["legs"]
    kickoff = _time(game["kickoff"])
    pregame_cutoff = kickoff - timedelta(minutes=30)
    # Preserve a specific chronology error even for incomplete historical fixtures.
    if kind == "pregame" and recorded > pregame_cutoff:
        raise ValueError("pregame record must meet kickoff minus 30-minute cutoff")
    frozen = _time(game["as_of"])
    if not frozen <= recorded or (kind == "pregame" and frozen > pregame_cutoff):
        raise ValueError("ledger proposal chronology invalid")
    if kind == "pregame" and (recorded - frozen).total_seconds() > 300:
        raise ValueError("pregame snapshot stale at ledger freeze")
    if not _text(payload["game_id"]) or payload["game_id"] != game.get("game_id"):
        raise ValueError("frozen ticket/game identifiers incomplete")
    if not isinstance(legs, list) or not legs or not isinstance(decision, dict) or type(decision.get("approved")) is not bool:
        raise ValueError("malformed frozen proposal")
    if kind == "live":
        state = game.get("state") or {}
        if (not kickoff <= frozen or not 0 <= (recorded - frozen).total_seconds() <= 300
                or state.get("status") != "in_progress" or not _number(state.get("seconds_remaining"))
                or not 0 < state["seconds_remaining"] <= 3600 or len(state.get("scores", {})) != 2
                or any(type(score) is not int or score < 0 for score in state["scores"].values())):
            raise ValueError("live proposal requires current in-progress snapshot")
    keys = set()
    for leg in legs:
        if (not isinstance(leg, dict) or not all(_text(leg.get(k)) for k in ("player_id", "team_id"))
                or leg.get("market") not in MARKETS
                or not all(_number(leg.get(k)) for k in ("line", "current", "odds_decimal"))
                or leg["line"] <= 0 or leg["odds_decimal"] <= 1 or leg["current"] < 0
                or float(f"{leg['line']:g}") != leg["line"]
                or (leg["market"] == "anytime_td" and leg["line"] != 1)):
            raise ValueError("malformed frozen leg")
        if kind == "pregame" and leg["current"] != 0:
            raise ValueError("pregame current total must be zero")
        key = _key(leg)
        if key in keys:
            raise ValueError("duplicate frozen leg")
        keys.add(key)
        if not isinstance(leg.get("source"), dict) or "observed_at" not in leg["source"]:
            raise ValueError("frozen leg source missing")
    if not isinstance(game.get("source"), dict) or "observed_at" not in game["source"]:
        raise ValueError("frozen snapshot source missing")
    _source_times(game, frozen)
    _source_times(legs, frozen)
    probabilities = decision.get("leg_probabilities")
    if not isinstance(probabilities, dict) or any(not _number(p) or not 0 <= p <= 1 for p in probabilities.values()):
        raise ValueError("frozen leg probabilities invalid")
    joint = decision.get("joint_probability")
    if joint is not None and (not _number(joint) or not 0 <= joint <= 1):
        raise ValueError("frozen joint probability invalid")
    if decision["approved"] and (set(probabilities) != keys or joint is None or joint > min(probabilities.values()) or joint < max(0, sum(probabilities.values()) - len(keys) + 1) - 1e-12):
        raise ValueError("approved forecast probabilities incomplete or contradictory")
    controls = payload.get("evaluation_controls", {})
    if not isinstance(controls, dict) or not set(controls) <= {
        "max_source_age_seconds", "minimum_scenario_probability", "aggressive_fraction"
    }:
        raise ValueError("invalid frozen evaluation controls")
    if decision["approved"]:
        from .snapshot_evidence import decode, source
        try:
            typed = decode(game)
            typed_legs = tuple(Leg(item["player_id"], item["team_id"], item["market"],
                                   item["line"], item["current"], item["odds_decimal"],
                                   source(item["source"])) for item in legs)
            computed = evaluate_ticket(typed, typed_legs, **controls)
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
            raise ValueError("approved forecast has invalid model inputs") from exc
        if not computed.approved or _json_ready(computed) != decision:
            raise ValueError("approved forecast differs from recomputed engine decision")
    quote = payload.get("quote")
    if quote is not None:
        if (not isinstance(quote, dict) or not _number(quote.get("decimal_odds")) or quote["decimal_odds"] <= 1
                or not isinstance(quote.get("leg_keys"), list) or len(quote["leg_keys"]) != len(keys)
                or set(quote["leg_keys"]) != keys or not isinstance(quote.get("source"), dict)
                or "observed_at" not in quote["source"]):
            raise ValueError("combined quote requires exact frozen leg and price provenance")
        _source_times(quote, frozen)
        if (frozen - _time(quote["source"]["observed_at"])).total_seconds() > 300:
            raise ValueError("combined quote is stale")


def _ticket_status(legs):
    statuses = {leg["status"] for leg in legs}
    return ("lost" if "lost" in statuses else "unresolved" if "unresolved" in statuses or
            ("void" in statuses and len(statuses) > 1) else "void" if "void" in statuses else "won")


def _validate_settlement(payload, proposal, recorded, proposed_at):
    legs = payload.get("legs")
    if payload.get("status") not in STATUSES or not isinstance(legs, list) or len(legs) != len(proposal["legs"]):
        raise ValueError("invalid settlement status or leg coverage")
    if recorded < proposed_at:
        raise ValueError("settlement precedes frozen proposal")
    if any(leg.get("status") != "unresolved" for leg in legs) and recorded < _time(proposal["snapshot"]["kickoff"]):
        raise ValueError("resolved settlement cannot precede kickoff")
    originals = {_key(leg): leg for leg in proposal["legs"]}
    matched = set()
    for leg in legs:
        if not isinstance(leg, dict) or leg.get("status") not in STATUSES or not _number(leg.get("threshold")):
            raise ValueError("invalid leg result or threshold")
        key = f"{leg.get('player_id')}:{leg.get('market')}:{leg['threshold']:g}"
        if key not in originals or key in matched:
            raise ValueError("settlement leg identity differs from frozen proposal")
        matched.add(key)
        original = originals[key]
        if leg.get("team_id") != original["team_id"]:
            raise ValueError("settlement team identity differs from frozen proposal")
        if leg["status"] == "void":
            raise ValueError("void settlement requires bookmaker rules evidence; unsupported")
        if leg["status"] in {"won", "lost"}:
            predicted = leg.get("predicted_probability")
            frozen_probability = proposal["decision"]["leg_probabilities"].get(key)
            unavailable = (proposal["decision"]["approved"] is False
                           and frozen_probability is None and predicted is None)
            if (not all(_number(leg.get(k)) for k in ("observed", "odds_decimal"))
                    or (not unavailable and not _number(predicted))):
                raise ValueError("settlement numerical evidence invalid")
            if (leg["status"] == "won") != (leg["observed"] >= original["line"]):
                raise ValueError("settlement outcome contradicts observed threshold")
            if (leg["predicted_probability"] != proposal["decision"]["leg_probabilities"].get(key)
                    or leg["odds_decimal"] != original["odds_decimal"]):
                raise ValueError("settlement rewrites frozen probability or odds")
            if leg.get("source") != payload.get("source") or not _text(leg.get("error_category")):
                raise ValueError("settlement leg source or error category invalid")
    if payload["status"] != _ticket_status(legs):
        raise ValueError("ticket status conflicts with leg results")
    # A final box is frozen inside the event so later file edits cannot silently
    # replace the evidence. Offline replay checks it again against any raw archive.
    if "final_box" in payload or any(leg["status"] != "unresolved" for leg in legs):
        from .settlement import _validate_final_source, _settled_legs
        box = payload.get("final_box")
        _validate_final_source(box, payload.get("source"), proposal, recorded)
        if _time(payload["source"]["retrieved_at"]) < proposed_at:
            raise ValueError("final source retrieval precedes frozen proposal")
        expected = _settled_legs(proposal, box, payload["source"])
        if legs != expected:
            raise ValueError("settlement contradicts frozen final source evidence")


def _replay(rows, cutoff=None):
    previous = "0" * 64
    last_time = None
    proposals, settlements = {}, {}
    for ticket, kind, recorded, body, prior, digest in rows:
        expected = hashlib.sha256(f"{previous}|{ticket}|{kind}|{recorded}|{body}".encode()).hexdigest()
        if prior != previous or digest != expected:
            raise ValueError("ledger hash chain invalid")
        previous = digest
        recorded = _time(recorded)
        if (cutoff is not None and recorded > cutoff) or (last_time is not None and recorded < last_time):
            raise ValueError("ledger event chronology invalid")
        last_time = recorded
        if not _text(ticket):
            raise ValueError("invalid ledger ticket identity")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("invalid ledger event payload")
        if kind in {"pregame", "live"}:
            if ticket in proposals:
                raise ValueError("multiple frozen proposals for ticket")
            _validate_proposal(payload, kind, recorded)
            proposals[ticket] = (payload, recorded)
        elif kind == "settlement":
            if ticket not in proposals or ticket in settlements:
                raise ValueError("settlement without unique frozen proposal")
            proposal, when = proposals[ticket]
            _validate_settlement(payload, proposal, recorded, when)
            settlements[ticket] = payload
        else:
            raise ValueError("unknown ledger event kind")
    return proposals, settlements


class Ledger:
    def __init__(self, path: str | Path):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('pregame', 'live', 'settlement')),
                recorded_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE,
                UNIQUE(ticket_id, kind)
            );
            CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events
            BEGIN SELECT RAISE(ABORT, 'immutable ledger'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events
            BEGIN SELECT RAISE(ABORT, 'immutable ledger'); END;
            CREATE UNIQUE INDEX IF NOT EXISTS one_proposal_per_ticket ON events(ticket_id)
            WHERE kind IN ('pregame', 'live');
        """)

    def append(self, ticket_id: str, kind: str, payload: dict) -> str:
        payload = json.loads(json.dumps(payload, allow_nan=False))
        recorded_at = _now().isoformat()
        if kind not in {"pregame", "live", "settlement"} or not _text(ticket_id) or not isinstance(payload, dict):
            raise ValueError("invalid ledger event")
        try:
            if kind in {"pregame", "live"}:
                _validate_proposal(payload, kind, _time(recorded_at))
            else:
                row = self.connection.execute(
                    "SELECT payload,recorded_at FROM events WHERE ticket_id=? AND kind IN ('pregame','live')",
                    (ticket_id,)).fetchone()
                if not row:
                    raise ValueError("settlement requires frozen proposal")
                _validate_settlement(payload, json.loads(row[0]), _time(recorded_at), _time(row[1]))
        except (KeyError, TypeError, AttributeError, OverflowError) as exc:
            raise ValueError("malformed ledger evidence") from exc
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if not self.verify():
                raise ValueError("cannot append to invalid ledger")
            previous = self.connection.execute("SELECT event_hash FROM events ORDER BY sequence DESC LIMIT 1").fetchone()
            previous_hash = previous[0] if previous else "0" * 64
            if previous:
                last_time = self.connection.execute("SELECT recorded_at FROM events ORDER BY sequence DESC LIMIT 1").fetchone()[0]
                if _time(recorded_at) < _time(last_time):
                    raise ValueError("ledger chronology moved backwards")
            event_hash = hashlib.sha256(f"{previous_hash}|{ticket_id}|{kind}|{recorded_at}|{body}".encode()).hexdigest()
            self.connection.execute("INSERT INTO events(ticket_id,kind,recorded_at,payload,previous_hash,event_hash) VALUES(?,?,?,?,?,?)",
                                    (ticket_id, kind, recorded_at, body, previous_hash, event_hash))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return event_hash

    def evaluate_and_record(self, ticket_id: str, snapshot: Snapshot, legs: tuple[Leg, ...],
                            *, quote: TicketQuote | None = None, **options):
        expected_keys = {leg_key(leg) for leg in legs}
        if quote and (quote.decimal_odds <= 1 or set(quote.leg_keys) != expected_keys
                      or len(quote.leg_keys) != len(legs) or not quote.source.provider or not quote.source.reference
                      or not quote.source.observed_at.tzinfo or quote.source.observed_at > snapshot.as_of
                      or (snapshot.as_of - quote.source.observed_at).total_seconds() > options.get("max_source_age_seconds", 300)):
            raise ValueError("combined quote requires valid price provenance")
        decision = evaluate_ticket(snapshot, legs, **options)
        kind = "live" if snapshot.as_of >= snapshot.kickoff else "pregame"
        self.append(ticket_id, kind, _json_ready({"game_id": snapshot.game_id, "snapshot": snapshot,
                                                       "decision": decision, "legs": legs, "quote": quote,
                                                       "evaluation_controls": options}))
        return decision

    def verify(self) -> bool:
        """Check content integrity and replay event contracts, not external authenticity."""
        try:
            rows = self.connection.execute(
                "SELECT ticket_id,kind,recorded_at,payload,previous_hash,event_hash FROM events ORDER BY sequence").fetchall()
            _replay(rows)
            return True
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError, sqlite3.Error):
            return False

    def prospective_report(self, minimum_settled: int = 100) -> dict:
        """Score quoted pregame forecasts; expose other forecasts as research only."""
        if type(minimum_settled) is not int or minimum_settled < 1:
            raise ValueError("minimum_settled must be positive")
        if not self.verify():
            return {'status':'INVALID_LEDGER','settled_forecasts':None,'hit_rate':None,'brier_score':None,'account_roi':None}
        probabilities = []
        outcomes = []
        research_probabilities = []
        research_outcomes = []
        research_counts = {"unpriced_pregame_forecasts": 0, "live_forecasts": 0}
        for kind, proposal, settlement in self.connection.execute("""
            SELECT proposal.kind, proposal.payload, settlement.payload FROM events AS proposal
            JOIN events AS settlement ON settlement.ticket_id=proposal.ticket_id AND settlement.kind='settlement'
            WHERE proposal.kind IN ('pregame','live') ORDER BY proposal.sequence
        """):
            frozen = json.loads(proposal)
            forecast = frozen["decision"]
            result = json.loads(settlement)
            probability = forecast.get("joint_probability")
            complete = all(item.get("status") in {"won", "lost"} for item in result["legs"])
            if forecast.get("approved") is True and probability is not None and complete and result.get("status") in {"won", "lost"}:
                outcome = int(result["status"] == "won")
                if kind == "pregame" and frozen.get("quote") is not None:
                    probabilities.append(probability)
                    outcomes.append(outcome)
                else:
                    research_probabilities.append(probability)
                    research_outcomes.append(outcome)
                    research_counts["live_forecasts" if kind == "live" else "unpriced_pregame_forecasts"] += 1
        count = len(outcomes)
        return {"status": "MEASURABLE_RESEARCH" if count >= minimum_settled else "NOT MEASURABLE",
                "settled_forecasts": count, "minimum_settled": minimum_settled,
                "hit_rate": sum(outcomes) / count if count else None,
                "brier_score": sum((probability - outcome) ** 2 for probability, outcome
                                   in zip(probabilities, outcomes)) / count if count else None,
                "account_roi": None,
                "research_only": {
                    "settled_forecasts": len(research_outcomes), **research_counts,
                    "hit_rate": sum(research_outcomes) / len(research_outcomes) if research_outcomes else None,
                    "brier_score": sum((probability - outcome) ** 2 for probability, outcome
                                       in zip(research_probabilities, research_outcomes)) / len(research_outcomes)
                                   if research_outcomes else None,
                }}


def audit_ledger_evidence(manifest, as_of):
    """Read only; hashes establish internal integrity, never source authenticity."""
    reasons = []
    evidence = {"ledger_present": False, "proposals": 0, "settlements": 0, "markets": [],
                "hash_chain_verified": False, "semantic_replay_verified": False,
                "external_authenticity_verified": False}
    try:
        cutoff = as_of if isinstance(as_of, datetime) else _time(as_of)
        if cutoff.utcoffset() is None:
            raise ValueError("aware cutoff required")
        if not isinstance(manifest, dict) or not manifest.get("path"):
            raise ValueError("missing frozen pregame/settlement ledger artifact")
        path = Path(manifest["path"])
        if not path.is_absolute() or not path.is_file():
            raise ValueError("absolute existing ledger path required")
        if Path(str(path) + "-wal").exists() and Path(str(path) + "-wal").stat().st_size:
            raise ValueError("ledger archive has uncheckpointed WAL evidence")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != manifest.get("sha256"):
            raise ValueError("ledger artifact hash mismatch")
        evidence.update(ledger_present=True, path=str(path), sha256=digest)
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute("SELECT ticket_id,kind,recorded_at,payload,previous_hash,event_hash FROM events ORDER BY sequence").fetchall()
            triggers = {row[0]: row[1] for row in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")}
            # Exercise triggers inside a rolled-back in-memory copy. Names alone prove nothing.
            probe = sqlite3.connect(":memory:")
            try:
                db.backup(probe)
                for command in ("UPDATE events SET ticket_id=ticket_id", "DELETE FROM events"):
                    try:
                        probe.execute(command)
                    except sqlite3.IntegrityError:
                        pass
                    else:
                        reasons.append("ledger immutability triggers ineffective")
                    finally:
                        probe.rollback()
            finally:
                probe.close()
        if not {"immutable_events_update", "immutable_events_delete"} <= triggers.keys():
            reasons.append("ledger immutability triggers missing")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("ledger artifact changed during audit")
        proposals, settlements = _replay(rows, cutoff)
        evidence.update(hash_chain_verified=True, semantic_replay_verified=True,
                        proposals=len(proposals), settlements=len(settlements))
        if not proposals:
            reasons.append("ledger has no frozen observations")
        for proposal, _ in proposals.values():
            if proposal.get("quote") is None:
                reasons.append("frozen exact combined ticket quote missing")
        for ticket, result in settlements.items():
            if result["status"] not in {"won", "lost"} or any(leg["status"] not in {"won", "lost"} for leg in result["legs"]):
                reasons.append("unresolved or partial ticket settlement remains")
            source = result.get("source", {})
            if not isinstance(source, dict) or not source.get("archive_file"):
                reasons.append("settlement lacks inspectable final source archive")
            else:
                from .settlement import _validate_final_source
                _validate_final_source(result["final_box"], source, proposals[ticket][0],
                                       _time(next(row[2] for row in rows if row[0] == ticket and row[1] == "settlement")),
                                       require_archive=True)
        if not settlements:
            reasons.append("no resolved full-ticket settlement evidence")
        if len(settlements) != len(proposals):
            reasons.append("frozen proposals without settlements")
        evidence["markets"] = sorted({leg["market"] for ticket, settlement in settlements.items()
                                      if settlement["status"] in {"won", "lost"}
                                      and all(leg["status"] in {"won", "lost"} for leg in settlement["legs"])
                                      for leg in proposals[ticket][0]["legs"]})
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError, sqlite3.Error) as exc:
        reasons.append(f"ledger evidence invalid: {exc}")
    return {"status": "BLOCKED" if reasons else "PASS", "reasons": list(dict.fromkeys(reasons)), "evidence": evidence}


def audit_current_recording_evidence(manifest, as_of, *, game_id, ticket_ids):
    """Verify pending pregame records separately from historical performance.

    A structurally valid blocked or unpriced research proposal may be recorded.
    This check proves recording integrity only. It cannot replace historical
    ledger validation, eligibility, fresh quotes, or model approval.
    """
    historical = audit_ledger_evidence(manifest, as_of)
    lifecycle_reasons = {
        "no resolved full-ticket settlement evidence", "frozen proposals without settlements",
        "unresolved or partial ticket settlement remains", "frozen exact combined ticket quote missing",
        "settlement lacks inspectable final source archive",
    }
    reasons = [reason for reason in historical["reasons"] if reason not in lifecycle_reasons]
    evidence = dict(historical["evidence"], scope="CURRENT_RECORDING_INTEGRITY_ONLY",
                    recording_status="UNVERIFIED", historical_validation_status=historical["status"],
                    historical_validation_reasons=historical["reasons"], betting_ready=False)
    try:
        if (not isinstance(game_id, str) or not game_id.strip() or
                not isinstance(ticket_ids, (list, tuple)) or not ticket_ids or
                any(not isinstance(ticket, str) or not ticket.strip() for ticket in ticket_ids) or
                len(set(ticket_ids)) != len(ticket_ids)):
            raise ValueError("exact game and distinct ticket IDs required")
        if reasons or not evidence["hash_chain_verified"] or not evidence["semantic_replay_verified"]:
            raise ValueError("ledger storage or semantic integrity not verified")
        path = Path(manifest["path"])
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute("SELECT ticket_id,kind,recorded_at,payload,previous_hash,event_hash FROM events ORDER BY sequence").fetchall()
        cutoff = as_of if isinstance(as_of, datetime) else _time(as_of)
        proposals, settlements = _replay(rows, cutoff)
        kinds = {row[0]: row[1] for row in rows if row[1] in {"pregame", "live"}}
        for ticket in ticket_ids:
            if ticket not in proposals:
                raise ValueError("selected frozen proposal missing")
            proposal, _ = proposals[ticket]
            if proposal["game_id"] != game_id or proposal["snapshot"]["game_id"] != game_id:
                raise ValueError("selected proposal game mismatch")
            if kinds[ticket] != "pregame" or cutoff >= _time(proposal["snapshot"]["kickoff"]):
                raise ValueError("pending pregame recording requires future kickoff")
            if ticket in settlements:
                raise ValueError("selected proposal already has settlement evidence")
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"]:
            raise ValueError("ledger changed during current recording audit")
        evidence.update(recording_status="RECORDED_UNSETTLED", game_id=game_id,
                        ticket_ids=list(ticket_ids), selected_proposals=len(ticket_ids),
                        quoted_proposals=sum(proposals[ticket][0].get("quote") is not None for ticket in ticket_ids))
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError, sqlite3.Error) as exc:
        reasons.append(f"current recording evidence invalid: {exc}")
    return {"status": "BLOCKED" if reasons else "PASS", "reasons": list(dict.fromkeys(reasons)), "evidence": evidence}
