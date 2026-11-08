from dataclasses import replace
from datetime import datetime, timedelta, timezone
from math import exp

import pytest

from football_engine.engine import GameState, Leg, Player, Scenario, Snapshot, Source, TicketQuote, _poisson_tail, compare_ticket_prices, evaluate_ticket, screen_prices


NOW = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)


def test_poisson_tail_handles_extreme_low_efficiency_without_overflow():
    assert _poisson_tail(5, 10000) == 0
    assert 0 < _poisson_tail(10, 8) < 1
SOURCE = Source("fixture", "test:snapshot", NOW)
PLAYER = Player("p1", "CHI", True, frozenset({"rushing_yards", "anytime_td"}), SOURCE)


def snapshot(*, carries=12, red_zone=3, second_carries=12):
    def scenario(name, weight, volume):
        return Scenario(name, weight, {"p1": {"carries": volume, "red_zone_touches": red_zone}},
                        {"p1": {"rushing_yards": 5}}, {"p1": 0.3}, SOURCE)
    return Snapshot("game-1", NOW, NOW + timedelta(hours=1), {"p1": PLAYER},
                    (scenario("lead", 0.6, carries), scenario("trail", 0.4, second_carries)), SOURCE)


def leg(market="rushing_yards", line=40, current=0):
    return Leg("p1", "CHI", market, line, current, 2.5, SOURCE)


def test_touchdown_opportunity_is_separate_from_rushing_yards():
    decision = evaluate_ticket(snapshot(carries=0, second_carries=0), (leg("anytime_td", 1),))
    assert decision.approved
    assert decision.leg_probabilities["p1:anytime_td:1"] > 0
    assert len(decision.skills) == 8
    assert len(decision.audit) == 7


def test_pregame_current_total_cannot_make_future_leg_certain():
    game = snapshot()
    for market, line in (("rushing_yards", 40), ("anytime_td", 1)):
        decision = evaluate_ticket(game, (leg(market, line, current=line),))
        assert not decision.approved
        assert decision.joint_probability is None
        assert any("pregame current" in reason for reason in decision.reasons)


def test_overlapping_td_opportunities_require_unique_count():
    game = snapshot()
    scenarios = tuple(replace(item, remaining={"p1": {"red_zone_touches": 3, "end_zone_targets": 2}})
                      for item in game.scenarios)
    decision = evaluate_ticket(replace(game, scenarios=scenarios), (leg("anytime_td", 1),))
    assert not decision.approved
    assert any("overlap" in reason for reason in decision.reasons)
    unique = tuple(replace(item, remaining={"p1": {"red_zone_touches": 3, "end_zone_targets": 2,
                                                     "unique_scoring_chances": 4}}) for item in game.scenarios)
    decision = evaluate_ticket(replace(game, scenarios=unique), (leg("anytime_td", 1),))
    assert decision.approved
    assert decision.joint_probability == pytest.approx(1 - exp(-1.2))
    impossible = tuple(replace(item, remaining={"p1": {"red_zone_touches": 3, "end_zone_targets": 2,
                                                         "unique_scoring_chances": 6}}) for item in game.scenarios)
    assert not evaluate_ticket(replace(game, scenarios=impossible), (leg("anytime_td", 1),)).approved


def test_live_alt_line_fails_when_remaining_workload_is_insufficient():
    game = snapshot(carries=2, second_carries=2)
    scenarios = tuple(replace(item, maximum_remaining={"p1": {"carries": 2}}) for item in game.scenarios)
    game = replace(game, kickoff=NOW, state=GameState(600, "in_progress", SOURCE, {"CHI": 20, "SEA": 9}), scenarios=scenarios)
    decision = evaluate_ticket(game, (leg(line=125, current=118),))
    assert decision.approved
    scenarios = tuple(replace(item, remaining={"p1": {"carries": 1, "red_zone_touches": 3}},
                              maximum_remaining={"p1": {"carries": 1}}) for item in game.scenarios)
    decision = evaluate_ticket(replace(game, scenarios=scenarios), (leg(line=125, current=118),))
    assert not decision.approved
    assert any("remaining opportunity" in reason for reason in decision.reasons)


def test_bad_game_script_blocks_joint_ticket():
    decision = evaluate_ticket(snapshot(carries=12, second_carries=0), (leg(),))
    assert not decision.approved
    assert any("scenario stress" in reason for reason in decision.reasons)


def test_team_and_provenance_are_checked():
    assert not evaluate_ticket(snapshot(), (replace(leg(), team_id="SEA"),)).approved
    stale = replace(SOURCE, observed_at=NOW - timedelta(hours=1))
    assert not evaluate_ticket(replace(snapshot(), source=stale), (leg(),)).approved


def test_scenario_mixture_controls_correlated_joint_probability():
    both = (leg("rushing_yards", 40), leg("anytime_td", 1))
    decision = evaluate_ticket(snapshot(), both)
    assert not decision.approved
    assert decision.joint_probability is None
    assert decision.audit["correlation"] == "fail"
    keys = tuple({"p1:rushing_yards:40": i < 90, "p1:anytime_td:1": i < 60}
                 for i in range(100))
    game = snapshot()
    scenarios = tuple(replace(item, joint_samples=keys) for item in game.scenarios)
    verified = evaluate_ticket(replace(game, scenarios=scenarios), both)
    assert verified.approved
    assert verified.joint_probability == .6


def test_joint_samples_cannot_inflate_probability_against_model_marginals():
    game = snapshot()
    samples = ({"p1:rushing_yards:40": True, "p1:anytime_td:1": True},) * 100
    game = replace(game, scenarios=tuple(replace(item, joint_samples=samples) for item in game.scenarios))
    decision = evaluate_ticket(game, (leg(), leg("anytime_td", 1)))
    assert not decision.approved
    assert decision.joint_probability is None
    assert any("sample marginals disagree" in reason for reason in decision.reasons)


def test_scenario_weights_must_sum_to_one_at_arithmetic_precision():
    game = snapshot()
    scenarios = tuple(replace(item, weight=item.weight + 4e-7) for item in game.scenarios)
    assert not evaluate_ticket(replace(game, scenarios=scenarios), (leg(),)).approved


def test_live_hard_cap_rejects_impossible_alt_even_with_favorable_mean():
    game = snapshot(carries=1, second_carries=1)
    scenarios = tuple(replace(item, maximum_remaining={"p1": {"carries": 1}}) for item in game.scenarios)
    game = replace(game, kickoff=NOW, state=GameState(600, "in_progress", SOURCE, {"CHI": 20, "SEA": 9}), scenarios=scenarios)
    decision = evaluate_ticket(game, (leg(line=125, current=119),))
    assert not decision.approved
    assert any("hard remaining opportunity cap" in reason for reason in decision.reasons)


def test_exact_combined_quote_ranks_base_above_unrewarding_alt():
    candidates = {"base": ((leg(line=40),), TicketQuote(1.6, SOURCE, ("p1:rushing_yards:40",))),
                  "alt": ((leg(line=50),), TicketQuote(1.6, SOURCE, ("p1:rushing_yards:50",)))}
    ranked = compare_ticket_prices(snapshot(), candidates)
    assert [item.ticket_id for item in ranked] == ["base", "alt"]
    assert ranked[0].break_even_probability == 0.625
    stale = TicketQuote(4.0, replace(SOURCE, observed_at=NOW - timedelta(hours=1)), ("p1:rushing_yards:40",))
    assert not compare_ticket_prices(snapshot(), {"stale": ((leg(),), stale)})
    assert not compare_ticket_prices(snapshot(), {"stale": ((leg(),), stale)}, max_source_age_seconds=3600)
    assert screen_prices(snapshot(), candidates).status == "research_only"
    assert screen_prices(snapshot(), {"stale": ((leg(),), stale)}).status == "not_measurable"
    wrong_combination = TicketQuote(3.0, SOURCE, ("p1:rushing_yards:50",))
    assert not compare_ticket_prices(snapshot(), {"wrong": ((leg(line=40),), wrong_combination)})
    cheap = TicketQuote(1.01, SOURCE, ("p1:rushing_yards:40",))
    assert "no positive illustrative price edge" in screen_prices(snapshot(), {"cheap": ((leg(),), cheap)}).reasons


def test_live_ticket_requires_clock_and_hard_caps():
    live = replace(snapshot(), as_of=NOW + timedelta(hours=1), state=GameState(600, "in_progress", SOURCE, {"CHI": 20, "SEA": 9}))
    assert not evaluate_ticket(live, (leg(),)).approved
    fresh = replace(SOURCE, observed_at=live.as_of)
    capped = tuple(replace(item, source=fresh, maximum_remaining={"p1": {"carries": 12}})
                   for item in live.scenarios)
    player = replace(PLAYER, source=fresh)
    live = replace(live, source=fresh, state=GameState(600, "in_progress", fresh, {"CHI": 20, "SEA": 9}),
                   players={"p1": player}, scenarios=capped)
    assert evaluate_ticket(live, (replace(leg(), source=fresh),)).approved
    assert not evaluate_ticket(replace(live, state=GameState(0, "final", fresh, {"CHI": 34, "SEA": 17})),
                               (replace(leg(), source=fresh),)).approved



def assert_blocked(decision):
    assert not decision.approved
    assert decision.use_status == "BLOCKED"
    assert decision.joint_probability is None
    assert decision.leg_probabilities == {}
    assert len(decision.skills) == 8
    assert len(decision.audit) == 7
    assert "pending" not in decision.audit.values()


@pytest.mark.parametrize("active", [False, None, 0, 1, "true", "false", [], {}])
def test_active_requires_literal_true(active):
    assert_blocked(evaluate_ticket(replace(snapshot(), players={"p1": replace(PLAYER, active=active)}), (leg(),)))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, None, "12"])
@pytest.mark.parametrize("field", ["line", "current", "odds_decimal"])
def test_market_numbers_fail_closed(field, value):
    assert_blocked(evaluate_ticket(snapshot(), (replace(leg(), **{field: value}),)))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, None, "12"])
@pytest.mark.parametrize("field", ["weight", "remaining", "maximum_remaining", "yards_per_opportunity",
                                    "touchdown_rate", "passing_td_per_attempt"])
def test_scenario_numbers_fail_closed(field, value):
    game = snapshot()
    changes = {field: value}
    if field in {"remaining", "maximum_remaining"}:
        changes[field] = {"p1": {"carries": value}}
    elif field == "yards_per_opportunity":
        changes[field] = {"p1": {"rushing_yards": value}}
    elif field in {"touchdown_rate", "passing_td_per_attempt"}:
        changes[field] = {"p1": value}
    assert_blocked(evaluate_ticket(replace(game, scenarios=(replace(game.scenarios[0], **changes),)), (leg(),)))


@pytest.mark.parametrize("option,value", [("max_source_age_seconds", float("nan")),
    ("max_source_age_seconds", float("inf")), ("max_source_age_seconds", -1),
    ("max_source_age_seconds", 301),
    ("minimum_scenario_probability", 0), ("minimum_scenario_probability", float("nan")),
    ("minimum_scenario_probability", 0.01), ("minimum_scenario_probability", 1.1),
    ("aggressive_fraction", 0), ("aggressive_fraction", 0.5),
    ("aggressive_fraction", float("nan")), ("aggressive_fraction", 1.1)])
def test_controls_fail_closed(option, value):
    assert_blocked(evaluate_ticket(snapshot(), (leg(),), **{option: value}))


@pytest.mark.parametrize("source", [None, {}, Source(" ", "test", NOW), Source("fixture", " ", NOW),
    Source("fixture", "test", NOW.replace(tzinfo=None)), Source("fixture", "test", "yesterday"),
    Source("fixture", "test", NOW + timedelta(microseconds=1)),
    Source("fixture", "test", NOW - timedelta(seconds=301))])
@pytest.mark.parametrize("location", ["snapshot", "player", "scenario", "role", "leg", "quote"])
def test_all_source_locations_require_fresh_timed_proof(location, source):
    game, offered = snapshot(), leg()
    if location == "snapshot":
        game = replace(game, source=source)
    elif location == "player":
        game = replace(game, players={"p1": replace(PLAYER, source=source)})
    elif location in {"scenario", "role"}:
        changes = {"source": source} if location == "scenario" else {"role_sources": (source,)}
        game = replace(game, scenarios=tuple(replace(item, **changes) for item in game.scenarios))
    elif location == "leg":
        offered = replace(offered, source=source)
    if location == "quote":
        result = screen_prices(game, {"bad": ((offered,), TicketQuote(2, source, ("p1:rushing_yards:40",)))})
        assert result.status == "not_measurable"
    else:
        assert_blocked(evaluate_ticket(game, (offered,)))


@pytest.mark.parametrize("changes", [{"as_of": None}, {"as_of": NOW.replace(tzinfo=None)},
    {"kickoff": "tomorrow"}, {"as_of": datetime.now(timezone.utc) + timedelta(days=1)}])
def test_invalid_snapshot_time_is_blocked(changes):
    assert_blocked(evaluate_ticket(replace(snapshot(), **changes), (leg(),)))


@pytest.mark.parametrize("changes", [{"remaining": None}, {"remaining": {"p1": []}},
    {"remaining": {"p1": {"unsupported": 1}}}, {"remaining": {"p1": {"carries": 10001}}},
    {"maximum_remaining": {"p1": {"carries": 12.5}}}, {"role_sources": None},
    {"joint_samples": None}, {"joint_samples": (None,)}, {"joint_samples": ({},)},
    {"joint_samples": ({"p1:rushing_yards:40": 1},)},
    {"joint_samples": ({"p1:rushing_yards:40": True, "extra": float("nan")},)}])
def test_malformed_scenario_shapes_and_ranges(changes):
    game = snapshot()
    assert_blocked(evaluate_ticket(replace(game, scenarios=tuple(replace(item, **changes) for item in game.scenarios)), (leg(),)))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), None, True, "2", 1])
def test_invalid_quotes_do_not_enter_research_ranking(value):
    assert screen_prices(snapshot(), {"bad": ((leg(),), TicketQuote(value, SOURCE, ("p1:rushing_yards:40",)))}).status == "not_measurable"


@pytest.mark.parametrize("candidate", [None, (), (None,), (None, None), ((), TicketQuote(2, SOURCE)),
    ((leg(),), TicketQuote(2, SOURCE, None)), ((leg(),), TicketQuote(2, SOURCE, ({},)))])
def test_malformed_price_candidates_are_excluded(candidate):
    assert screen_prices(snapshot(), {"bad": candidate}).status == "not_measurable"


def test_price_screen_keeps_valid_candidates_and_explicit_research_status():
    quote = TicketQuote(2, SOURCE, ("p1:rushing_yards:40",))
    result = screen_prices(snapshot(), {"bad": None, "good": ((leg(),), quote)})
    assert result.status == "research_only"
    assert len(result.comparisons) == 1
    assert result.comparisons[0].decision.use_status == "RESEARCH_ONLY"
    assert "joint probabilities are not prospectively calibrated" in result.reasons


@pytest.mark.parametrize("changes", [{"seconds_remaining": float("nan")}, {"seconds_remaining": True},
    {"seconds_remaining": 3601}, {"seconds_remaining": -1}, {"status": []},
    {"scores": {"CHI": True, "SEA": 1}}, {"scores": {"CHI": float("nan"), "SEA": 1}},
    {"scores": None}, {"source": None}])
def test_invalid_live_state_is_blocked(changes):
    game = replace(snapshot(), kickoff=NOW, state=replace(GameState(600, "in_progress", SOURCE, {"CHI": 1, "SEA": 2}), **changes))
    assert_blocked(evaluate_ticket(game, (leg(),)))


def test_tiny_efficiency_with_cap_is_blocked_before_ceil_overflow():
    game = snapshot()
    scenarios = tuple(replace(item, yards_per_opportunity={"p1": {"rushing_yards": 5e-324}},
                              maximum_remaining={"p1": {"carries": 12}}) for item in game.scenarios)
    assert_blocked(evaluate_ticket(replace(game, scenarios=scenarios), (leg(),)))


def test_correlated_samples_cannot_override_zero_scoring_opportunity():
    game = snapshot(red_zone=0)
    samples = ({"p1:rushing_yards:40": True, "p1:anytime_td:1": True},) * 100
    game = replace(game, scenarios=tuple(replace(item, joint_samples=samples) for item in game.scenarios))
    decision = evaluate_ticket(game, (leg(), leg("anytime_td", 1)))
    assert_blocked(decision)
    assert decision.audit["correlation"] == "fail"


def test_near_unit_weights_cannot_emit_probability_above_one():
    game = snapshot()
    samples = ({"p1:rushing_yards:40": True, "p1:anytime_td:1": True},) * 100
    game = replace(game, scenarios=tuple(replace(item, weight=item.weight + 4e-7, joint_samples=samples)
                                        for item in game.scenarios))
    assert_blocked(evaluate_ticket(game, (leg(), leg("anytime_td", 1))))


def test_ambiguous_formatted_keys_cannot_collapse_distinct_legs():
    game = snapshot()
    samples = ({"p1:rushing_yards:40": True},) * 100
    game = replace(game, scenarios=tuple(replace(item, joint_samples=samples) for item in game.scenarios))
    assert_blocked(evaluate_ticket(game, (leg(line=40.000001), leg(line=40.000002))))


@pytest.mark.parametrize("market,line,current", [("unsupported", 1, 0), ("anytime_td", 2, 0),
    ("anytime_td", 1, .5), ("passing_td", 1.5, .5)])
def test_unsupported_market_semantics_are_blocked(market, line, current):
    assert_blocked(evaluate_ticket(snapshot(), (leg(market, line, current),)))


def test_mean_opportunities_cannot_exceed_declared_hard_cap():
    game = snapshot()
    game = replace(game, scenarios=tuple(replace(item, maximum_remaining={"p1": {"carries": 0}})
                                        for item in game.scenarios))
    assert_blocked(evaluate_ticket(game, (leg(line=1),)))


def test_live_touchdown_requires_cap_for_positive_scoring_opportunities():
    game = replace(snapshot(), kickoff=NOW, state=GameState(600, "in_progress", SOURCE, {"CHI": 1, "SEA": 2}))
    assert_blocked(evaluate_ticket(game, (leg("anytime_td", 1),)))
    game = replace(game, scenarios=tuple(replace(item, maximum_remaining={"p1": {"red_zone_touches": 3}})
                                        for item in game.scenarios))
    assert evaluate_ticket(game, (leg("anytime_td", 1),)).use_status == "RESEARCH_ONLY"


def test_high_poisson_rate_does_not_underflow_to_certain_success():
    assert .49 < _poisson_tail(1000, 1000) < .52
    assert .49 < _poisson_tail(10000, 10000) < .52


def test_stress_rejection_clears_computed_probabilities():
    assert_blocked(evaluate_ticket(snapshot(carries=0, second_carries=0), (leg(),)))



def test_single_leg_quote_cannot_bind_a_rounded_different_threshold():
    offered = leg(line=40.000001)
    quote = TicketQuote(2, SOURCE, ("p1:rushing_yards:40",))
    assert_blocked(evaluate_ticket(snapshot(), (offered,)))
    assert screen_prices(snapshot(), {"wrong_threshold": ((offered,), quote)}).status == "not_measurable"


@pytest.mark.parametrize("samples", [({"p1:rushing_yards:40": True, "p1:anytime_td:1": True},) * 99,
    ({"p1:rushing_yards:40": True, "p1:anytime_td:1": True},) * 99 + (None,),
    ({"p1:rushing_yards:40": True},) * 100,
    ({"p1:rushing_yards:40": True, "p1:anytime_td:1": "true"},) * 100])
def test_multileg_requires_complete_boolean_shared_samples(samples):
    game = snapshot()
    game = replace(game, scenarios=tuple(replace(item, joint_samples=samples) for item in game.scenarios))
    assert_blocked(evaluate_ticket(game, (leg(), leg("anytime_td", 1))))


def test_samples_cannot_reverse_nested_thresholds_or_current_totals():
    game = snapshot()
    nested = ({"p1:rushing_yards:40": False, "p1:rushing_yards:50": True},) * 100
    game = replace(game, scenarios=tuple(replace(item, joint_samples=nested) for item in game.scenarios))
    assert_blocked(evaluate_ticket(game, (leg(), leg(line=50))))
    contradicted = ({"p1:rushing_yards:40": False},) * 100
    game = replace(game, scenarios=tuple(replace(item, joint_samples=contradicted) for item in game.scenarios))
    assert_blocked(evaluate_ticket(game, (leg(current=40),)))
