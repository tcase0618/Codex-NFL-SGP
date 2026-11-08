"""Research-only odds/probability tradeoffs for exactly quoted parlays."""

from dataclasses import dataclass
from math import isfinite

from .engine import Snapshot, TicketQuote, compare_ticket_prices


@dataclass(frozen=True)
class RankedParlay:
    ticket_id: str
    model_joint_probability: float
    decimal_odds: float
    american_odds: str
    break_even_probability: float
    illustrative_expected_return: float


@dataclass(frozen=True)
class ParlayShortlist:
    status: str
    parlays: tuple[RankedParlay, ...]
    reasons: tuple[str, ...]


def _american(decimal_odds: float) -> str:
    if decimal_odds >= 2:
        return f"+{round((decimal_odds - 1) * 100)}"
    return f"-{round(100 / (decimal_odds - 1))}"


def rank_priced_parlays(snapshot: Snapshot, candidates: dict,
                        *, minimum_joint_probability: float = 0.5,
                        minimum_illustrative_edge: float = 0.0,
                        book: str = "FanDuel") -> ParlayShortlist:
    """Return the non-dominated odds/probability shortlist, never a bet approval.

    The probability floor and edge floor are screening preferences, not learned
    thresholds. Each candidate must already have passed the audited market-first
    construction path; this function does not authenticate self-supplied inputs.
    """
    if (type(minimum_joint_probability) not in (int, float)
            or not isfinite(minimum_joint_probability)
            or not 0 < minimum_joint_probability < 1
            or type(minimum_illustrative_edge) not in (int, float)
            or not isfinite(minimum_illustrative_edge)
            or minimum_illustrative_edge < 0
            or not isinstance(book, str) or not book.strip()):
        raise ValueError("invalid probability, edge or sportsbook preference")
    if not isinstance(candidates, dict) or not candidates:
        return ParlayShortlist("NOT_MEASURABLE", (), ("no exact combined sportsbook quotes",))
    scoped = {}
    for ticket_id, candidate in candidates.items():
        if (not isinstance(candidate, (tuple, list)) or len(candidate) != 2
                or not isinstance(candidate[1], TicketQuote)):
            continue
        legs, quote = candidate
        if (not isinstance(legs, (tuple, list)) or len(legs) < 2
                or quote.source.provider != book
                or any(getattr(getattr(leg, "source", None), "provider", None) != book
                       for leg in legs)):
            continue
        scoped[ticket_id] = candidate
    comparisons = compare_ticket_prices(snapshot, scoped)
    eligible = []
    for item in comparisons:
        probability = item.decision.joint_probability
        if (probability is None or probability < minimum_joint_probability
                or item.illustrative_expected_return <= minimum_illustrative_edge):
            continue
        odds = scoped[item.ticket_id][1].decimal_odds
        eligible.append(RankedParlay(item.ticket_id, probability, odds,
                                     _american(odds), item.break_even_probability,
                                     item.illustrative_expected_return))
    if not eligible:
        return ParlayShortlist("NOT_MEASURABLE", (),
                               ("no quoted parlay clears the probability and illustrative edge floors",))
    frontier = tuple(item for item in eligible if not any(
        (other.model_joint_probability >= item.model_joint_probability
         and other.decimal_odds >= item.decimal_odds
         and (other.model_joint_probability > item.model_joint_probability
              or other.decimal_odds > item.decimal_odds))
        for other in eligible))
    ranked = tuple(sorted(frontier, key=lambda item: (
        -item.illustrative_expected_return, -item.model_joint_probability,
        -item.decimal_odds, item.ticket_id)))
    return ParlayShortlist("RESEARCH_ONLY", ranked,
                           ("joint probabilities are not prospectively calibrated; no parlay is predicted to hit with certainty",))
