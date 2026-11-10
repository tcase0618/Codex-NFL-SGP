from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import ceil, exp, isfinite, lgamma, log, sqrt
from typing import Literal

Market = Literal["receiving_yards", "rushing_yards", "passing_yards", "anytime_td", "passing_td"]
Opportunity = Literal["targets", "carries", "attempts", "red_zone_touches", "end_zone_targets", "unique_scoring_chances"]


@dataclass(frozen=True)
class Source:
    provider: str
    reference: str
    observed_at: datetime


@dataclass(frozen=True)
class Player:
    player_id: str
    team_id: str
    active: bool
    eligible_markets: frozenset[Market]
    source: Source


@dataclass(frozen=True)
class Leg:
    player_id: str
    team_id: str
    market: Market
    line: float
    current: float
    odds_decimal: float
    source: Source


@dataclass(frozen=True)
class Scenario:
    name: str
    weight: float
    remaining: dict[str, dict[Opportunity, float]]
    yards_per_opportunity: dict[str, dict[Market, float]]
    touchdown_rate: dict[str, float]
    source: Source
    maximum_remaining: dict[str, dict[Opportunity, int]] = field(default_factory=dict)
    joint_samples: tuple[dict[str, bool], ...] = ()
    passing_td_per_attempt: dict[str, float] = field(default_factory=dict)
    role_sources: tuple[Source, ...] = ()
    qb_change_teams: frozenset[str] = frozenset()
    qb_underperformance_teams: frozenset[str] = frozenset()


@dataclass(frozen=True)
class GameState:
    seconds_remaining: int
    status: Literal["scheduled", "in_progress", "final"]
    source: Source
    scores: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Snapshot:
    game_id: str
    as_of: datetime
    kickoff: datetime
    players: dict[str, Player]
    scenarios: tuple[Scenario, ...]
    source: Source
    state: GameState | None = None
    team_profile_versions: dict[str, str] = field(default_factory=dict)
    qb_change_teams: frozenset[str] = frozenset()
    qb_change_source: Source | None = None
    qb_context: dict[str, dict] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    approved: bool
    reasons: tuple[str, ...]
    leg_probabilities: dict[str, float]
    joint_probability: float | None
    audit: dict[str, str] = field(default_factory=dict)
    skills: dict[str, str] = field(default_factory=dict)

    @property
    def use_status(self) -> Literal["RESEARCH_ONLY", "BLOCKED"]:
        return "RESEARCH_ONLY" if self.approved else "BLOCKED"


@dataclass(frozen=True)
class TicketQuote:
    decimal_odds: float
    source: Source
    leg_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class PriceComparison:
    ticket_id: str
    break_even_probability: float
    illustrative_expected_return: float
    decision: Decision


@dataclass(frozen=True)
class PriceScreen:
    status: Literal["research_only", "not_measurable"]
    comparisons: tuple[PriceComparison, ...]
    reasons: tuple[str, ...]


YARDAGE_OPPORTUNITY: dict[Market, Opportunity] = {
    "receiving_yards": "targets", "rushing_yards": "carries", "passing_yards": "attempts"
}
SKILLS = ("source_provenance", "eligibility", "yardage_opportunity", "touchdown_opportunity",
          "remaining_opportunity", "game_scripts", "correlation", "ledger_ready")
AUDITORS = ("source", "roster", "market", "remaining", "scenario", "correlation", "ledger")


def leg_key(leg: Leg) -> str:
    return f"{leg.player_id}:{leg.market}:{leg.line:g}"


def _number(value: object) -> bool:
    # bool is an int in Python, but is never numerical evidence here.
    try:
        return type(value) in (int, float) and isfinite(value)
    except OverflowError:
        return False


def _aware(value: object) -> bool:
    try:
        return isinstance(value, datetime) and value.utcoffset() is not None
    except (ValueError, TypeError, OverflowError):
        return False


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_source(source: Source, as_of: datetime) -> bool:
    return (isinstance(source, Source) and _text(source.provider) and _text(source.reference)
            and _aware(as_of) and _aware(source.observed_at) and source.observed_at <= as_of)


def _blocked(reason: str) -> Decision:
    return Decision(False, (reason,), {}, None, {name: "fail" for name in AUDITORS},
                    {name: "fail" for name in SKILLS})


def _validate_inputs(snapshot, legs, max_age, minimum, aggressive) -> str | None:
    """Validate runtime shapes as well as annotations before doing arithmetic."""
    if (not _number(max_age) or not 0 <= max_age <= 300
            or not _number(minimum) or not 0.05 <= minimum <= 1
            or not _number(aggressive) or not 0.8 <= aggressive <= 1):
        return "invalid evaluation controls"
    if (not isinstance(snapshot, Snapshot) or not isinstance(legs, (tuple, list)) or not legs
            or not _aware(snapshot.as_of) or not _aware(snapshot.kickoff)
            or snapshot.as_of > datetime.now(timezone.utc)):
        return "invalid ticket or time"
    if (not _text(snapshot.game_id) or not isinstance(snapshot.players, dict)
            or not isinstance(snapshot.scenarios, (tuple, list)) or not snapshot.scenarios):
        return "invalid snapshot or scenario structure"
    if (not isinstance(snapshot.qb_change_teams, frozenset)
            or any(not _text(team) for team in snapshot.qb_change_teams)):
        return "invalid quarterback change evidence"
    if snapshot.qb_change_teams and not _valid_source(snapshot.qb_change_source, snapshot.as_of):
        return "missing quarterback change source provenance"
    markets = {*YARDAGE_OPPORTUNITY, "anytime_td", "passing_td"}
    opportunities = {"targets", "carries", "attempts", "red_zone_touches", "end_zone_targets", "unique_scoring_chances"}
    sources = [snapshot.source]
    if snapshot.qb_change_source is not None:
        sources.append(snapshot.qb_change_source)
    for pid, player in snapshot.players.items():
        if (not isinstance(player, Player) or not _text(pid) or pid != player.player_id
                or not _text(player.team_id) or type(player.active) is not bool
                or not isinstance(player.eligible_markets, (set, frozenset))
                or not player.eligible_markets <= markets):
            return "ineligible player or team: malformed roster or active proof"
        sources.append(player.source)
    for leg in legs:
        if (not isinstance(leg, Leg) or not _text(leg.player_id) or not _text(leg.team_id)
                or not isinstance(leg.market, str) or leg.market not in markets
                or not all(_number(x) for x in (leg.line, leg.current, leg.odds_decimal))
                or leg.line <= 0 or leg.current < 0 or leg.odds_decimal <= 1
                or (leg.market == "anytime_td" and leg.line != 1)
                or (leg.market in {"anytime_td", "passing_td"} and leg.current % 1 != 0)):
            return "invalid market data: unsupported market or numeric range"
        # The public key format uses six significant digits. Reject lines it cannot
        # identify exactly rather than binding a quote for a different threshold.
        if float(f"{leg.line:g}") != leg.line:
            return "invalid market data: line exceeds supported key precision"
        if snapshot.as_of < snapshot.kickoff and leg.current != 0:
            return "pregame current total must be zero"
        sources.append(leg.source)
    keys = {leg_key(leg) for leg in legs}
    if len(keys) != len(legs):
        return "duplicate leg or ambiguous market key"
    names = set()
    for scenario in snapshot.scenarios:
        if (not isinstance(scenario, Scenario) or not _text(scenario.name)
                or scenario.name in names or not _number(scenario.weight) or not 0 < scenario.weight <= 1
                or not isinstance(scenario.role_sources, (tuple, list))
                or not isinstance(scenario.qb_change_teams, frozenset)
                or any(not _text(team) for team in scenario.qb_change_teams)
                or not isinstance(scenario.qb_underperformance_teams, frozenset)
                or any(not _text(team) for team in scenario.qb_underperformance_teams)):
            return "invalid scenario weights or structure"
        if scenario.qb_change_teams and not scenario.role_sources:
            return "missing quarterback change scenario role provenance"
        if scenario.qb_underperformance_teams and not scenario.role_sources:
            return "missing quarterback underperformance scenario provenance"
        names.add(scenario.name)
        sources.extend((scenario.source, *scenario.role_sources))
        for mapping, allowed, kind in ((scenario.remaining, opportunities, "volume"),
                                      (scenario.maximum_remaining, opportunities, "cap"),
                                      (scenario.yards_per_opportunity, set(YARDAGE_OPPORTUNITY), "efficiency")):
            if not isinstance(mapping, dict):
                return "invalid scenario opportunity structure"
            for pid, values in mapping.items():
                if not _text(pid) or not isinstance(values, dict):
                    return "invalid scenario opportunity structure"
                for key, value in values.items():
                    if (key not in allowed or not _number(value) or value < 0
                            or (kind == "cap" and type(value) is not int)
                            or (kind == "efficiency" and value <= 0)
                            # Bound Poisson work explicitly; this is a numerical support limit,
                            # not an empirically established football workload range.
                            or (kind == "volume" and value > 10000)):
                        return "invalid scenario opportunity range"
        for pid, caps in scenario.maximum_remaining.items():
            for opportunity, cap in caps.items():
                if scenario.remaining.get(pid, {}).get(opportunity, 0) > cap:
                    return "hard remaining opportunity cap: mean exceeds maximum"
        for values in scenario.remaining.values():
            if "unique_scoring_chances" in values:
                rush = values.get("red_zone_touches", 0)
                targets = values.get("end_zone_targets", 0)
                unique = values["unique_scoring_chances"]
                if not max(rush, targets) <= unique <= rush + targets + 1e-12:
                    return "touchdown opportunity overlap: unique count outside component bounds"
        for mapping in (scenario.touchdown_rate, scenario.passing_td_per_attempt):
            if not isinstance(mapping, dict) or any(not _text(pid) or not _number(rate) or not 0 <= rate <= 1
                                                   for pid, rate in mapping.items()):
                return "invalid scenario touchdown rate"
        if not isinstance(scenario.joint_samples, (tuple, list)) or any(
                not isinstance(sample, dict) or not keys <= sample.keys()
                or any(not isinstance(key, str) or type(value) is not bool for key, value in sample.items())
                for sample in scenario.joint_samples):
            return "joint player correlation unverified: malformed samples"
        for sample in scenario.joint_samples:
            for leg in legs:
                if leg.current >= leg.line and not sample[leg_key(leg)]:
                    return "joint player correlation unverified: contradicted current total"
                for other in legs:
                    if (leg.player_id == other.player_id and leg.market == other.market
                            and (leg.current != other.current
                                 or (leg.line > other.line and sample[leg_key(leg)] and not sample[leg_key(other)]))):
                        return "joint player correlation unverified: inconsistent thresholds"
    state = snapshot.state
    live = snapshot.as_of >= snapshot.kickoff
    if state is not None:
        if (not isinstance(state, GameState) or not isinstance(state.status, str) or state.status not in {"scheduled", "in_progress", "final"}
                or type(state.seconds_remaining) is not int or not 0 <= state.seconds_remaining <= 3600
                or not isinstance(state.scores, dict)
                or any(not _text(team) or type(score) is not int or score < 0 for team, score in state.scores.items())
                or (not live and state.status != "scheduled")):
            return "live clock or game status unavailable"
        sources.append(state.source)
    if not all(_valid_source(source, snapshot.as_of)
               and (snapshot.as_of - source.observed_at).total_seconds() <= max_age for source in sources):
        return "missing, future, or stale provenance"
    return None


def _poisson_tail(rate: float, minimum: int) -> float:
    if not _number(rate) or not 0 <= rate <= 10000 or type(minimum) is not int:
        raise ValueError("unsupported Poisson inputs")
    if minimum <= 0:
        return 1.0
    if rate == 0:
        return 0.0
    if minimum > rate + 12 * sqrt(rate) + 12:
        return 0.0
    # Start at the threshold in log space to avoid exp(-rate) underflow.
    count = minimum - 1 if minimum <= rate else minimum
    term = exp(-rate + count * log(rate) - lgamma(count + 1))
    total = term
    if minimum <= rate:
        while count > 0:
            term *= count / rate
            count -= 1
            total += term
            if term < total * 1e-15:
                break
        return max(0.0, min(1.0, 1 - total))
    while term > total * 1e-15:
        count += 1
        term *= rate / count
        total += term
    return max(0.0, min(1.0, total))


def compare_ticket_prices(snapshot: Snapshot, candidates: dict[str, tuple[tuple[Leg, ...], TicketQuote]],
                          *, max_source_age_seconds: int = 300) -> tuple[PriceComparison, ...]:
    comparisons: list[PriceComparison] = []
    if (not isinstance(candidates, dict) or not _number(max_source_age_seconds)
            or not 0 <= max_source_age_seconds <= 300):
        return ()
    # Ticket prices are pregame decisions; a fresh quote cannot reopen the
    # archived 30-minute decision window.
    if (not isinstance(snapshot, Snapshot) or not _aware(snapshot.as_of)
            or not _aware(snapshot.kickoff)
            or snapshot.as_of > snapshot.kickoff - timedelta(minutes=30)):
        return ()
    for ticket_id, candidate in candidates.items():
        if not _text(ticket_id) or not isinstance(candidate, (tuple, list)) or len(candidate) != 2:
            continue
        legs, quote = candidate
        if (not isinstance(quote, TicketQuote) or not _number(quote.decimal_odds)
                or quote.decimal_odds <= 1 or not isinstance(quote.leg_keys, (tuple, list))
                or any(not _text(key) for key in quote.leg_keys)):
            continue
        decision = evaluate_ticket(snapshot, legs, max_source_age_seconds=max_source_age_seconds)
        if not decision.approved:
            continue
        expected_keys = {leg_key(leg) for leg in legs}
        if (not decision.approved or decision.joint_probability is None or quote.decimal_odds <= 1
                or set(quote.leg_keys) != expected_keys or len(quote.leg_keys) != len(legs)
                or not _valid_source(quote.source, snapshot.as_of)
                or (snapshot.as_of - quote.source.observed_at).total_seconds() > max_source_age_seconds):
            continue
        comparisons.append(PriceComparison(ticket_id, 1 / quote.decimal_odds,
                                           decision.joint_probability * quote.decimal_odds - 1, decision))
    return tuple(sorted(comparisons, key=lambda item: item.illustrative_expected_return, reverse=True))


def screen_prices(snapshot: Snapshot, candidates: dict[str, tuple[tuple[Leg, ...], TicketQuote]],
                  *, max_source_age_seconds: int = 300) -> PriceScreen:
    comparisons = compare_ticket_prices(snapshot, candidates, max_source_age_seconds=max_source_age_seconds)
    if not comparisons:
        return PriceScreen("not_measurable", (), ("no valid priced ticket",))
    reasons = ["joint probabilities are not prospectively calibrated"]
    if all(item.illustrative_expected_return <= 0 for item in comparisons):
        reasons.append("no positive illustrative price edge")
    return PriceScreen("research_only", comparisons, tuple(reasons))


def evaluate_ticket(snapshot: Snapshot, legs: tuple[Leg, ...], *, max_source_age_seconds: int = 300,
                    minimum_scenario_probability: float = 0.05, aggressive_fraction: float = 0.8) -> Decision:
    invalid = _validate_inputs(snapshot, legs, max_source_age_seconds,
                               minimum_scenario_probability, aggressive_fraction)
    if invalid:
        return _blocked(invalid)
    reasons: list[str] = []
    audit = {name: "pending" for name in AUDITORS}
    probabilities: dict[str, float] = {}
    if not legs or snapshot.as_of.tzinfo is None or snapshot.kickoff.tzinfo is None:
        reasons.append("invalid ticket or time")
    live = bool(snapshot.as_of.tzinfo and snapshot.kickoff.tzinfo and snapshot.as_of >= snapshot.kickoff)
    if live and (snapshot.state is None or snapshot.state.status != "in_progress"
                 or snapshot.state.seconds_remaining <= 0 or len(snapshot.state.scores) != 2
                 or any(score < 0 for score in snapshot.state.scores.values())
                 or any(leg.team_id not in snapshot.state.scores for leg in legs)):
        reasons.append("live clock or game status unavailable")
    sources = [snapshot.source, *(player.source for player in snapshot.players.values()),
               *(scenario.source for scenario in snapshot.scenarios), *(leg.source for leg in legs)]
    sources.extend(source for scenario in snapshot.scenarios for source in scenario.role_sources)
    if snapshot.state:
        sources.append(snapshot.state.source)
    if not all(_valid_source(source, snapshot.as_of) and
               (snapshot.as_of - source.observed_at).total_seconds() <= max_source_age_seconds
               for source in sources):
        reasons.append("missing, future, or stale provenance")
    audit["source"] = "pass" if not any("provenance" in reason for reason in reasons) else "fail"
    if not snapshot.scenarios or abs(sum(item.weight for item in snapshot.scenarios) - 1) > 1e-12 or any(
            item.weight <= 0 for item in snapshot.scenarios):
        reasons.append("invalid scenario weights")
    audit["scenario"] = "pass" if "invalid scenario weights" not in reasons else "fail"
    if len({(leg.player_id, leg.market, leg.line) for leg in legs}) != len(legs):
        reasons.append("duplicate leg")
    qb_dependent = tuple(leg for leg in legs if leg.market in {"receiving_yards", "passing_yards", "passing_td", "anytime_td"})
    for team in snapshot.qb_change_teams | frozenset().union(*(scenario.qb_change_teams for scenario in snapshot.scenarios)):
        if team not in snapshot.qb_change_teams:
            reasons.append(f"quarterback change scenario lacks snapshot evidence: {team}")
            continue
        affected = tuple(leg for leg in qb_dependent if leg.team_id == team)
        if not affected:
            continue
        changed = tuple(s for s in snapshot.scenarios if team in s.qb_change_teams)
        baseline = tuple(s for s in snapshot.scenarios if team not in s.qb_change_teams)
        if not changed or not baseline:
            reasons.append(f"quarterback change downside scenario missing: {team}")
            continue
        if sum(s.weight for s in changed) < minimum_scenario_probability:
            reasons.append(f"quarterback change downside scenario below stress weight: {team}")
        def output(scenario: Scenario, leg: Leg) -> float | None:
            remaining = scenario.remaining.get(leg.player_id, {})
            if leg.market == "anytime_td":
                volume = remaining.get("unique_scoring_chances")
                if volume is None:
                    volume = remaining.get("red_zone_touches", 0) + remaining.get("end_zone_targets", 0)
                efficiency = scenario.touchdown_rate.get(leg.player_id)
            elif leg.market == "passing_td":
                volume = remaining.get("attempts")
                efficiency = scenario.passing_td_per_attempt.get(leg.player_id)
            else:
                volume = remaining.get(YARDAGE_OPPORTUNITY[leg.market])
                efficiency = scenario.yards_per_opportunity.get(leg.player_id, {}).get(leg.market)
            return volume * efficiency if volume is not None and efficiency is not None else None
        # One shared change scenario must lower every affected leg's modeled
        # output. This checks structure, without assigning a new shock weight.
        if not any(all(output(s, leg) is not None and
                       any(output(b, leg) is not None and output(s, leg) < output(b, leg)
                           for b in baseline) for leg in affected) for s in changed):
            reasons.append(f"quarterback change downside unmodeled: {team}")
    scenario_joint: list[float] = []
    scenario_leg_probs: list[dict[str, float]] = []
    for scenario in snapshot.scenarios:
        joint = 1.0
        by_leg: dict[str, float] = {}
        for leg in legs:
            key = leg_key(leg)
            player = snapshot.players.get(leg.player_id)
            if not player or player.active is not True or player.team_id != leg.team_id or leg.market not in player.eligible_markets:
                reasons.append(f"ineligible player or team: {key}")
                continue
            if leg.line <= 0 or leg.current < 0 or leg.odds_decimal <= 1:
                reasons.append(f"invalid market data: {key}")
                continue
            remaining = scenario.remaining.get(leg.player_id, {})
            if leg.market == "anytime_td":
                if leg.current >= 1:
                    probability = 1.0
                else:
                    rate = scenario.touchdown_rate.get(leg.player_id)
                    rush = remaining.get("red_zone_touches", 0)
                    targets = remaining.get("end_zone_targets", 0)
                    scoring_chances = remaining.get("unique_scoring_chances", rush + targets)
                    if rate is None or not remaining.keys() & {"red_zone_touches", "end_zone_targets"} or not 0 <= rate <= 1 or scoring_chances < 0:
                        reasons.append(f"missing touchdown opportunity: {key}")
                        continue
                    if rush > 0 and targets > 0 and "unique_scoring_chances" not in remaining:
                        reasons.append(f"touchdown opportunity overlap unresolved: {key} in {scenario.name}")
                        continue
                    caps = scenario.maximum_remaining.get(leg.player_id, {})
                    required = ("unique_scoring_chances",) if "unique_scoring_chances" in remaining else (
                        "red_zone_touches", "end_zone_targets")
                    if live and any(opportunity not in caps for opportunity in required if remaining.get(opportunity, 0) > 0):
                        reasons.append(f"missing live opportunity cap: {key} in {scenario.name}")
                    probability = 1 - exp(-rate * scoring_chances)
            elif leg.market == "passing_td":
                attempts = remaining.get("attempts")
                rate = scenario.passing_td_per_attempt.get(leg.player_id)
                if attempts is None or rate is None or attempts < 0 or not 0 <= rate <= 1:
                    reasons.append(f"missing passing touchdown opportunity: {key}")
                    continue
                needed = max(0, ceil(leg.line - leg.current))
                maximum = scenario.maximum_remaining.get(leg.player_id, {}).get("attempts")
                if live and maximum is None:
                    reasons.append(f"missing live opportunity cap: {key} in {scenario.name}")
                if maximum is not None and (maximum < 0 or needed > maximum):
                    reasons.append(f"hard remaining opportunity cap: {key} in {scenario.name}")
                probability = _poisson_tail(attempts * rate, needed)
            elif leg.market in YARDAGE_OPPORTUNITY:
                opportunity = YARDAGE_OPPORTUNITY[leg.market]
                volume = remaining.get(opportunity)
                efficiency = scenario.yards_per_opportunity.get(leg.player_id, {}).get(leg.market)
                if volume is None or efficiency is None or volume < 0 or efficiency <= 0:
                    reasons.append(f"missing yardage opportunity: {key}")
                    continue
                needed = max(0.0, leg.line - leg.current)
                maximum = scenario.maximum_remaining.get(leg.player_id, {}).get(opportunity)
                if live and maximum is None:
                    reasons.append(f"missing live opportunity cap: {key} in {scenario.name}")
                if not isfinite(needed / efficiency) or not isfinite(volume * efficiency / aggressive_fraction):
                    reasons.append(f"invalid market data: unsupported arithmetic range: {key}")
                    continue
                if maximum is not None and ceil(needed / efficiency) > maximum:
                    reasons.append(f"hard remaining opportunity cap: {key} in {scenario.name}")
                if needed > volume * efficiency / aggressive_fraction:
                    reasons.append(f"remaining opportunity insufficient: {key} in {scenario.name}")
                probability = _poisson_tail(volume, int(needed / efficiency) + (needed % efficiency > 0))
            else:
                reasons.append(f"invalid market data: {key}")
                continue
            by_leg[key] = probability
            joint *= probability
        if len(legs) > 1 and len(scenario.joint_samples) >= 100:
            keys = tuple(leg_key(leg) for leg in legs)
            if all(all(key in sample and isinstance(sample[key], bool) for key in keys)
                   for sample in scenario.joint_samples):
                if any(by_leg.get(key) == 0 and any(sample[key] for sample in scenario.joint_samples)
                       for key in keys):
                    reasons.append(f"joint player correlation unverified: impossible outcome in {scenario.name}")
                # A shared simulation must reproduce its own scenario marginals.
                # This simultaneous Hoeffding bound is deliberately lenient at
                # small n and is only an internal consistency check, not proof
                # that the model or its samples are calibrated to real games.
                n = len(scenario.joint_samples)
                tolerance = sqrt(log(2 * len(legs) * len(snapshot.scenarios) / 0.01) / (2 * n))
                if any(abs(sum(sample[key] for sample in scenario.joint_samples) / n - by_leg[key]) > tolerance
                       for key in keys if key in by_leg):
                    reasons.append(f"joint player correlation unverified: sample marginals disagree with model in {scenario.name}")
                joint = sum(all(sample[key] for key in keys) for sample in scenario.joint_samples) / len(scenario.joint_samples)
                by_leg = {key: sum(sample[key] for sample in scenario.joint_samples) / len(scenario.joint_samples)
                          for key in keys}
        scenario_joint.append(joint)
        scenario_leg_probs.append(by_leg)
    if len(legs) > 1 and not all(len(scenario.joint_samples) >= 100 and
                                  all(all(isinstance(sample.get(leg_key(leg)), bool)
                                          for leg in legs) for sample in scenario.joint_samples)
                                  for scenario in snapshot.scenarios):
        reasons.append("joint player correlation unverified")
    for leg in legs:
        key = leg_key(leg)
        if all(key in item for item in scenario_leg_probs):
            probabilities[key] = sum(scenario.weight * item[key] for scenario, item in zip(snapshot.scenarios, scenario_leg_probs))
    joint_probability = sum(scenario.weight * value for scenario, value in zip(snapshot.scenarios, scenario_joint)) if not reasons else None
    for scenario, joint in zip(snapshot.scenarios, scenario_joint):
        if scenario.weight >= minimum_scenario_probability and joint < minimum_scenario_probability:
            reasons.append(f"scenario stress failed: {scenario.name}")
    if (joint_probability is not None and not 0 <= joint_probability <= 1
            or any(not _number(value) or not 0 <= value <= 1 for value in probabilities.values())):
        reasons.append("invalid scenario probability range")
        audit["scenario"] = "fail"
    audit["roster"] = "fail" if any("ineligible" in reason for reason in reasons) else "pass"
    audit["market"] = "fail" if any("market data" in reason or "opportunity:" in reason for reason in reasons) else "pass"
    audit["remaining"] = "fail" if any("remaining opportunity" in reason or "live opportunity cap" in reason
                                        or "live clock" in reason for reason in reasons) else "pass"
    audit["correlation"] = "fail" if any("scenario stress" in reason or "correlation unverified" in reason
                                          for reason in reasons) else "pass"
    if any("quarterback change" in reason for reason in reasons):
        audit["scenario"] = "fail"
        audit["correlation"] = "fail"
    audit["ledger"] = "pass" if snapshot.game_id and all(leg.source.reference for leg in legs) else "fail"
    if audit["ledger"] == "fail":
        reasons.append("ledger identifiers missing")
    skills = {
        "source_provenance": audit["source"], "eligibility": audit["roster"],
        "yardage_opportunity": audit["market"], "touchdown_opportunity": audit["market"],
        "remaining_opportunity": audit["remaining"], "game_scripts": audit["scenario"],
        "correlation": audit["correlation"], "ledger_ready": audit["ledger"],
    }
    return Decision(not reasons, tuple(dict.fromkeys(reasons)), probabilities if not reasons else {},
                    joint_probability if not reasons else None, audit, skills)
