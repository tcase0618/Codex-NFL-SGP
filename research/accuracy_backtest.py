"""Read-only evidence gate, not a backtest runner or a strategy approval.

Manifest v1: kind='prospective_walk_forward', historically_frozen=True,
postgame_selected=False, current_vintage=False, rules_frozen_at, tickets,
selection_archive, settlements, folds, price_available and roi_available.
An archive is {path, sha256, reference, archive_reference, published_at,
version_available_at, observed_at, historical_version_verified: True}.
Paths must name existing local files; no downloads or runtime writes occur.
selection_archive contains JSON exactly equal to tickets. Ticket fields are
ticket_id, game_id, kickoff, frozen_at, legs and sources. Each leg has leg_id,
player_id, team_id, market, line. Each source has purpose (forecast, roster,
quote, model_inputs), leg_ids covering the ticket, and the archive fields.
Every source archive contains JSON with ticket_id, game_id, leg_ids, exact
legs and observed_at matching its source metadata. Forecasts additionally bind
model_sha256 to the fold model and include per-leg probabilities. Rosters
explicitly identify active (player_id, team_id) pairs for this game.
Settlements are separate full-ticket records with ticket_id, ticket_sha256,
leg_ids, outcome (won/lost/void/push), final=True, authoritative=True,
basis='full_ticket', settled_at and source (archive fields); their archive JSON
contains these same fields except source. Folds have fold_id, train_start,
train_end, test_start, test_end, train_games ({game_id, kickoff,
labels_available_at}), test_game_ids, trained_at and model (archive fields).
Ticket model_sha256 identifies the fold model. Model archive JSON binds fold_id,
train_start/end, trained_at, train_games, test_game_ids and rules_frozen_at.
Combined decimal_odds is required in quote archives when price_available;
stake, payout, currency in settlement records when roi_available.

PASS verifies supplied evidence consistency, not independent authenticity of
archive timestamps/authority, statistical sufficiency or predictive skill.
Fixture archives in tests are deliberately synthetic, never reported results.
"""

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _time(value):
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timezone required")
    return stamp.astimezone(timezone.utc)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _ids(value):
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError("nonempty ID list required")
    if len(set(value)) != len(value):
        raise ValueError("duplicate IDs")
    return set(value)


def _selection_leakage(value):
    """Inspect archived content too; wrapper declarations cannot hide hindsight."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "didNotPlay":
                return True
            if key in {"uses_postgame_participation", "postgame_selected", "current_vintage"} and item is not False:
                return True
            if _selection_leakage(item):
                return True
    elif isinstance(value, list):
        return any(_selection_leakage(item) for item in value)
    return False


def audit_backtest_evidence(manifest, as_of) -> dict:
    """Fail closed on absent/malformed evidence; never infer or emit win/loss counts.

    A completely unpriced statistical audit may pass with both availability
    flags explicitly false. It still requires archived pregame quote evidence
    (which may document an unavailable price); this never authorizes ROI claims.
    """
    reasons = []
    evidence = {"ticket_ids": [], "verified_archives": [], "fold_ids": [],
                "price_available": False, "roi_available": False,
                "roi": None, "prospective_validation": False,
                "supplied_evidence_consistent": False, "historical_authenticity_verified": False,
                "scope": "supplied archive consistency; not strategy approval"}

    def reject(reason):
        if reason not in reasons:
            reasons.append(reason)

    def timestamp(value, label):
        try:
            stamp = _time(value)
            if stamp > now:
                reject(f"{label}: after as_of")
            return stamp
        except (ValueError, TypeError, AttributeError, OverflowError):
            reject(f"{label}: invalid timezone-aware timestamp")
            return None

    def archive(record, label, cutoff=None, json_required=False):
        if not isinstance(record, dict):
            reject(f"{label}: missing archive")
            return None
        for field in ("reference", "archive_reference"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                reject(f"{label}: missing {field}")
        if record.get("historical_version_verified") is not True:
            reject(f"{label}: historical version unverified/current-vintage")
        stamps = {}
        for field in ("published_at", "version_available_at", "observed_at"):
            stamp = timestamp(record.get(field), f"{label}.{field}")
            stamps[field] = stamp
            if stamp and cutoff and stamp > cutoff:
                reject(f"{label}.{field}: after freeze/cutoff")
        if all(stamps.values()) and not (stamps["published_at"] <= stamps["version_available_at"] <= stamps["observed_at"]):
            reject(f"{label}: source timestamp order invalid")
        sha = record.get("sha256")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            reject(f"{label}: invalid source/input sha256")
            return None
        try:
            path = record.get("path")
            if not isinstance(path, str) or not Path(path).is_absolute():
                raise ValueError("absolute archive path required")
            raw = Path(path).read_bytes()
            if hashlib.sha256(raw).hexdigest() != sha:
                reject(f"{label}: archive hash mismatch")
                return None
            evidence["verified_archives"].append({"purpose": label, "path": path, "sha256": sha})
            return json.loads(raw) if json_required else raw
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            reject(f"{label}: unreadable or malformed archive")
            return None

    try:
        now = _time(as_of)
    except (ValueError, TypeError, AttributeError, OverflowError):
        return {"status": "BLOCKED", "reasons": ["invalid timezone-aware as_of"], "evidence": evidence}
    if not isinstance(manifest, dict) or not manifest:
        return {"status": "BLOCKED", "reasons": ["missing backtest evidence manifest"], "evidence": evidence}

    try:
        if (manifest.get("kind") != "prospective_walk_forward"
                or manifest.get("historically_frozen") is not True
                or manifest.get("postgame_selected") is not False
                or manifest.get("current_vintage") is not False):
            reject("retrospective/current-vintage/postgame-selected replay is not prospective validation")
        if _selection_leakage(manifest.get("tickets")):
            reject("postgame/current-vintage content in ticket selection")
        for flag in ("price_available", "roi_available"):
            if type(manifest.get(flag)) is not bool:
                reject(f"{flag}: explicit boolean required")
        priced = manifest.get("price_available") is True
        roi = manifest.get("roi_available") is True
        if roi and not priced:
            reject("ROI requires available combined ticket prices")
        rules = timestamp(manifest.get("rules_frozen_at"), "rules_frozen_at")
        tickets = manifest.get("tickets")
        if not isinstance(tickets, list) or not tickets:
            raise ValueError("nonempty frozen tickets required")
        ticket_ids = _ids([t["ticket_id"] for t in tickets])
        evidence["ticket_ids"] = sorted(ticket_ids)
        games, frozen = {}, []
        by_id = {}
        all_prices = True
        for ticket in tickets:
            tid = ticket["ticket_id"]
            by_id[tid] = ticket
            kickoff = timestamp(ticket.get("kickoff"), f"{tid}.kickoff")
            freeze = timestamp(ticket.get("frozen_at"), f"{tid}.frozen_at")
            if not kickoff or not freeze or freeze >= kickoff:
                reject(f"{tid}: ticket not frozen strictly before kickoff")
            if freeze:
                frozen.append(freeze)
            if rules and freeze and rules > freeze:
                reject(f"{tid}: rules selected after freeze")
            game = ticket.get("game_id")
            if not isinstance(game, str) or not game.strip():
                raise ValueError("game_id required")
            if game in games and games[game] != kickoff:
                reject(f"{tid}: inconsistent game kickoff")
            games[game] = kickoff
            legs = ticket.get("legs")
            if not isinstance(legs, list) or not legs:
                raise ValueError("nonempty frozen legs required")
            leg_ids = _ids([leg["leg_id"] for leg in legs])
            leg_keys = set()
            for leg in legs:
                if any(not isinstance(leg.get(k), str) or not leg[k].strip()
                       for k in ("player_id", "team_id", "market")) or not _number(leg.get("line")):
                    reject(f"{tid}: incomplete leg identity/line")
                if (leg.get("market") not in {"passing_yards", "rushing_yards", "receiving_yards", "anytime_td", "passing_td"}
                        or not _number(leg.get("line")) or leg["line"] <= 0):
                    reject(f"{tid}: unsupported market or threshold")
                key = (leg.get("player_id"), leg.get("market"), leg.get("line"))
                if key in leg_keys:
                    reject(f"{tid}: duplicate leg selection")
                leg_keys.add(key)
            sources = ticket.get("sources")
            if not isinstance(sources, list) or not sources:
                raise ValueError("pregame sources required")
            purposes = set()
            for source in sources:
                purpose = source["purpose"]
                if purpose in purposes:
                    reject(f"{tid}: duplicate source purpose {purpose}")
                purposes.add(purpose)
                if _ids(source.get("leg_ids")) != leg_ids:
                    reject(f"{tid}.{purpose}: incomplete leg coverage")
                if _selection_leakage(source):
                    reject(f"{tid}.{purpose}: postgame participation in selection")
                payload = archive(source, f"{tid}.{purpose}", freeze, True)
                if not isinstance(payload, dict):
                    reject(f"{tid}.{purpose}: source JSON object required")
                    continue
                if (payload.get("ticket_id") != tid or payload.get("game_id") != game
                        or _ids(payload.get("leg_ids")) != leg_ids or payload.get("legs") != legs):
                    reject(f"{tid}.{purpose}: archive ticket/game/leg binding mismatch")
                if payload.get("observed_at") != source.get("observed_at"):
                    reject(f"{tid}.{purpose}: archived observation timestamp mismatch")
                for field in ("published_at", "version_available_at"):
                    if field in payload and payload[field] != source.get(field):
                        reject(f"{tid}.{purpose}: archived {field} mismatch")
                for field in ("generated_at", "forecast_at", "roster_at", "quote_at", "observed_at"):
                    if field in payload:
                        stamp = timestamp(payload[field], f"{tid}.{purpose}.{field}")
                        if stamp and freeze and stamp > freeze:
                            reject(f"{tid}.{purpose}.{field}: after freeze/cutoff")
                if _selection_leakage(payload):
                    reject(f"{tid}.{purpose}: postgame/current-vintage archived selection content")
                if purpose in {"forecast", "roster", "quote"}:
                    if purpose == "roster":
                        players = payload.get("players")
                        if not isinstance(players, list):
                            raise ValueError("archived roster players required")
                        roster_ids = [(p["player_id"], p["team_id"]) for p in players]
                        if len(set(roster_ids)) != len(roster_ids):
                            reject(f"{tid}: conflicting/duplicate roster player records")
                        active = {(p["player_id"], p["team_id"]) for p in players if p.get("active") is True}
                        if any((leg["player_id"], leg["team_id"]) not in active for leg in legs):
                            reject(f"{tid}: explicit game-specific active players missing")
                    if purpose == "forecast":
                        probabilities = payload.get("probabilities", {})
                        if not isinstance(probabilities, dict) or set(probabilities) != leg_ids or any(
                                not _number(probabilities.get(lid)) or not 0 <= probabilities[lid] <= 1 for lid in leg_ids):
                            reject(f"{tid}: frozen per-leg forecast probabilities missing")
                        if not ticket.get("model_sha256") or payload.get("model_sha256") != ticket.get("model_sha256"):
                            reject(f"{tid}: forecast/model identity mismatch")
                    if purpose == "quote":
                        odds = payload.get("decimal_odds") if isinstance(payload, dict) else None
                        if not _number(odds) or odds <= 1:
                            all_prices = False
                            if priced:
                                reject(f"{tid}: missing combined ticket decimal price")
            if not {"forecast", "roster", "quote", "model_inputs"} <= purposes:
                reject(f"{tid}: missing forecast/roster/quote/model_inputs evidence")
        selection = archive(manifest.get("selection_archive"), "selection_archive", min(frozen) if frozen else None, True)
        if selection != tickets:
            reject("frozen selection archive differs from manifest tickets")

        settlements = manifest.get("settlements")
        if not isinstance(settlements, list) or _ids([s["ticket_id"] for s in settlements]) != ticket_ids:
            raise ValueError("separate full-ticket settlements must exactly cover frozen tickets")
        for settlement in settlements:
            tid = settlement["ticket_id"]
            ticket = by_id[tid]
            if settlement.get("ticket_sha256") != _digest(ticket):
                reject(f"{tid}: settlement frozen ticket hash mismatch")
            if _ids(settlement.get("leg_ids")) != _ids([leg["leg_id"] for leg in ticket["legs"]]):
                reject(f"{tid}: settlement leg coverage mismatch")
            if (settlement.get("final") is not True or settlement.get("authoritative") is not True
                    or settlement.get("basis") != "full_ticket"
                    or settlement.get("outcome") not in {"won", "lost", "void", "push"}):
                reject(f"{tid}: authoritative final full-ticket outcome required; leg inference forbidden")
            settled = timestamp(settlement.get("settled_at"), f"{tid}.settled_at")
            kickoff = games[ticket["game_id"]]
            if not settled or not kickoff or settled <= kickoff:
                reject(f"{tid}: settlement must follow kickoff")
            result = archive(settlement.get("source"), f"{tid}.settlement", settled, True)
            if isinstance(settlement.get("source"), dict) and kickoff:
                published = timestamp(settlement["source"].get("published_at"), f"{tid}.settlement.published_at")
                if published and published <= kickoff:
                    reject(f"{tid}: final settlement source predates kickoff")
            if result != {k: v for k, v in settlement.items() if k != "source"}:
                reject(f"{tid}: settlement archive differs from full-ticket record")
            if roi and (not _number(settlement.get("stake")) or settlement["stake"] <= 0
                        or not _number(settlement.get("payout")) or settlement["payout"] < 0
                        or not isinstance(settlement.get("currency"), str) or not settlement["currency"].strip()):
                reject(f"{tid}: ROI requires explicit stake/payout/currency")

        folds = manifest.get("folds")
        if not isinstance(folds, list) or not folds:
            raise ValueError("nonempty walk-forward folds required")
        _ids([fold["fold_id"] for fold in folds])
        tested, windows = set(), []
        for fold in folds:
            fid = fold["fold_id"]
            evidence["fold_ids"].append(fid)
            ts, te, vs, ve, trained = [timestamp(fold.get(k), f"{fid}.{k}") for k in
                                     ("train_start", "train_end", "test_start", "test_end", "trained_at")]
            if not all((ts, te, vs, ve, trained)) or not ts <= te <= trained < vs <= ve:
                reject(f"{fid}: invalid/non-forward train/test windows")
            if rules and trained and rules > trained:
                reject(f"{fid}: rules frozen after model training")
            train = fold["train_games"]
            train_ids = _ids([g["game_id"] for g in train])
            test_ids = _ids(fold["test_game_ids"])
            if train_ids & test_ids or tested & test_ids or not test_ids <= games.keys():
                reject(f"{fid}: overlapping or unknown train/test games")
            tested.update(test_ids)
            for game in train:
                kick = timestamp(game.get("kickoff"), f"{fid}.train.kickoff")
                labels = timestamp(game.get("labels_available_at"), f"{fid}.train.labels_available_at")
                if not all((ts, te, trained, kick, labels)) or not ts <= kick < labels <= te <= trained:
                    reject(f"{fid}: training labels not available within training window")
                if game["game_id"] in games and games[game["game_id"]] != kick:
                    reject(f"{fid}: training game kickoff mismatch")
            for game in test_ids & games.keys():
                if not all((vs, ve, games[game])) or not vs <= games[game] <= ve:
                    reject(f"{fid}: test game outside test window")
                for ticket in tickets:
                    if ticket["game_id"] == game and trained and _time(ticket["frozen_at"]) < trained:
                        reject(f"{fid}: model trained after ticket freeze")
                    if ticket["game_id"] == game and ticket.get("model_sha256") != fold.get("model", {}).get("sha256"):
                        reject(f"{fid}: ticket/fold model identity mismatch")
                    if ticket["game_id"] == game and trained:
                        for source in ticket["sources"]:
                            if source.get("purpose") == "forecast":
                                if _time(source["observed_at"]) < trained:
                                    reject(f"{fid}: forecast predates model training")
                                forecast = archive(source, f"{fid}.forecast_model_binding", _time(ticket["frozen_at"]), True)
                                if isinstance(forecast, dict):
                                    for field in ("generated_at", "forecast_at"):
                                        if field in forecast and _time(forecast[field]) < trained:
                                            reject(f"{fid}: forecast generated before model training")
            model = archive(fold.get("model"), f"{fid}.model", trained, True)
            if isinstance(fold.get("model"), dict) and trained:
                observed = timestamp(fold["model"].get("observed_at"), f"{fid}.model.observed_at")
                if observed and observed < trained:
                    reject(f"{fid}: model archive observed before declared training completion")
            expected_model = {k: fold[k] for k in ("fold_id", "train_start", "train_end", "trained_at", "train_games", "test_game_ids")}
            expected_model["rules_frozen_at"] = manifest.get("rules_frozen_at")
            if not isinstance(model, dict) or any(model.get(k) != v for k, v in expected_model.items()):
                reject(f"{fid}: archived model/fold/training identity mismatch")
            if _selection_leakage(model):
                reject(f"{fid}: postgame/current-vintage model content")
            if vs and ve:
                if any(vs <= end and start <= ve for start, end in windows):
                    reject(f"{fid}: overlapping test windows")
                windows.append((vs, ve))
        if tested != set(games):
            reject("walk-forward tests do not exactly cover ticket games")
        if not reasons:
            evidence["price_available"] = priced and all_prices
            evidence["roi_available"] = roi and evidence["price_available"]
            evidence["supplied_evidence_consistent"] = True
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError) as exc:
        reject(f"malformed/incomplete backtest evidence: {exc}")
    return {"status": "BLOCKED" if reasons else "PASS", "reasons": reasons, "evidence": evidence}
