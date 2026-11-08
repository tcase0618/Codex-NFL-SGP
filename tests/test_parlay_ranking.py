from dataclasses import replace

import pytest

from football_engine.engine import TicketQuote, Source, leg_key
from football_engine.parlay_ranking import rank_priced_parlays
from test_engine import NOW, leg, snapshot


def test_probability_floor_and_exact_combined_prices_define_tradeoff_frontier():
    source = Source('FanDuel', 'https://sportsbook.fanduel.com/', NOW)
    td = replace(leg('anytime_td', 1), source=source)
    yards = {line: replace(leg(line=line), source=source) for line in (40, 50, 60)}
    samples = tuple({leg_key(yards[40]): i < 90,
                     leg_key(yards[50]): i < 70,
                     leg_key(yards[60]): i < 60,
                     leg_key(td): 20 <= i < 80} for i in range(100))
    game = snapshot()
    game = replace(game, scenarios=tuple(replace(scenario, joint_samples=samples)
                                          for scenario in game.scenarios))

    def candidate(line, odds):
        legs = (yards[line], td)
        return legs, TicketQuote(odds, source, tuple(leg_key(item) for item in legs))

    board = {'higher_hit': candidate(40, 2.0),
             'higher_payout': candidate(50, 2.6),
             'longshot': candidate(60, 4.0),
             'wrong_book': (candidate(40, 10.0)[0],
                            replace(candidate(40, 10.0)[1], source=replace(source, provider='Other')))}
    result = rank_priced_parlays(game, board)
    assert result.status == 'RESEARCH_ONLY'
    assert {item.ticket_id for item in result.parlays} == {'higher_hit', 'higher_payout'}
    assert result.parlays[0].ticket_id == 'higher_payout'
    assert result.parlays[0].american_odds == '+160'
    assert result.parlays[0].model_joint_probability == pytest.approx(0.5)
    assert 'not prospectively calibrated' in result.reasons[0]


def test_missing_or_unrewarding_prices_do_not_get_a_parlay_pick():
    assert rank_priced_parlays(snapshot(), {}).status == 'NOT_MEASURABLE'
    with pytest.raises(ValueError):
        rank_priced_parlays(snapshot(), {}, minimum_joint_probability=float('nan'))
