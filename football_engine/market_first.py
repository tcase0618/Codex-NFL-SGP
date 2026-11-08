"""Fail-closed, source-bound offered-market check before ticket selection.

This checks a frozen, machine-readable capture. A file hash cannot authenticate
the sportsbook itself; the capture still needs independent source review.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from math import isfinite
from pathlib import Path
from urllib.parse import urlparse

from .engine import Leg, Snapshot


@dataclass(frozen=True)
class MarketPreflight:
    status: str
    reasons: tuple[str, ...]
    archive_sha256: str | None = None


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("market capture time must include timezone")
    return parsed


def _number(value):
    return type(value) in (int, float) and isfinite(value)


def _identity(offer):
    return (offer["player_id"], offer["team_id"], offer["market"],
            offer["line"], offer["odds_decimal"])


def verify_offered_pool(snapshot: Snapshot, offered_legs, capture_path,
                        *, expected_sha256=None) -> MarketPreflight:
    """Require exact FanDuel TN player markets before considering any legs.

    The capture must be an immutable JSON snapshot of observed offers. This is
    structural verification, not proof that a self-supplied file came from the
    sportsbook or that the eventual combined quote has a positive edge.
    """
    if capture_path is None:
        return MarketPreflight("BLOCKED", ("actual FanDuel Tennessee market capture required before ticket selection",))
    try:
        path = Path(capture_path)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("absolute existing market capture required")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
                or digest != expected_sha256):
            raise ValueError("market capture SHA-256 missing or mismatched")
        data = json.loads(raw)
        if data.get("schema") != "FANDUEL_TN_OFFER_CAPTURE_V1":
            raise ValueError("unsupported market capture schema")
        if (data.get("book") != "FanDuel" or data.get("jurisdiction") != "TN"
                or data.get("game_id") != snapshot.game_id
                or _time(data["kickoff"]) != snapshot.kickoff):
            raise ValueError("market capture book, state or game mismatch")
        source = data["source"]
        parsed = urlparse(source["reference"])
        if (parsed.scheme != "https" or not parsed.hostname
                or not (parsed.hostname == "fanduel.com" or parsed.hostname.endswith(".fanduel.com"))
                or parsed.username or parsed.password):
            raise ValueError("market capture must reference FanDuel HTTPS source")
        observed = _time(source["observed_at"])
        if (snapshot.as_of.utcoffset() is None or snapshot.kickoff <= snapshot.as_of
                or not 0 <= (snapshot.as_of - observed).total_seconds() <= 300):
            raise ValueError("market capture future, stale or after kickoff")
        offers = data["offers"]
        if not isinstance(offers, list) or not offers or len(offers) != len(offered_legs):
            raise ValueError("complete offered pool required")
        ids = set()
        rows = set()
        for offer in offers:
            if (not isinstance(offer, dict) or not isinstance(offer.get("offer_id"), str)
                    or not offer["offer_id"].strip() or offer["offer_id"] in ids
                    or not all(isinstance(offer.get(k), str) and offer[k].strip()
                               for k in ("player_id", "team_id", "market"))
                    or not _number(offer.get("line")) or offer["line"] <= 0
                    or not _number(offer.get("odds_decimal")) or offer["odds_decimal"] <= 1):
                raise ValueError("invalid or duplicate offered market")
            ids.add(offer["offer_id"])
            row = _identity(offer)
            if row in rows:
                raise ValueError("duplicate player market line and price")
            rows.add(row)
        leg_rows = set()
        for leg in offered_legs:
            if (not isinstance(leg, Leg) or leg.source.provider != "FanDuel"
                    or leg.source.reference != source["reference"]
                    or leg.source.observed_at != observed):
                raise ValueError("leg source differs from captured FanDuel offer")
            leg_rows.add((leg.player_id, leg.team_id, leg.market, leg.line, leg.odds_decimal))
        if len(leg_rows) != len(offered_legs) or leg_rows != rows:
            raise ValueError("offered legs differ from captured lines or prices")
        return MarketPreflight("PASS", (), digest)
    except (OSError, KeyError, TypeError, ValueError, OverflowError, AttributeError,
            json.JSONDecodeError) as exc:
        return MarketPreflight("BLOCKED", (str(exc),))
