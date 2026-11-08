"""Counterexamples for denominator and red-zone population matching."""
import pytest

from football_engine.team_quant import qualifies, field
from test_team_quant import quant


def play(identity, **changes):
    return dict(game_id=identity,play_type='pass',qb_dropback=1,pass_attempt=1,
                complete_pass=0,yards_gained=0,epa=0,yardline_100=15,source_id=1,**changes)


def attach(quant,monkeypatch,rows):
    monkeypatch.setattr(quant,'select',lambda team,season,side,group,view,as_of:
                        ([p for p in rows if qualifies(p,group)],3))


def test_explosive_passes_use_all_dropbacks_not_just_completed_passes(quant,monkeypatch):
    rows=[]
    for i,changes in enumerate([{'complete_pass':1,'yards_gained':25},
                                {'incomplete_pass':1},{'sack':1,'yards_gained':-6},
                                {'qb_scramble':1,'rush_attempt':1,'yards_gained':25}]):
        p=play(str(i));p.update(changes);rows.append(p)
    attach(quant,monkeypatch,rows)
    offense=quant.value('CHI',2026,'matchup.offense_explosive_pass_dropback')
    defense=quant.value('SEA',2026,'matchup.defense_explosive_pass_dropback')
    assert offense['value']==defense['value']==25
    assert offense['sample_plays']==defense['sample_plays']==4
    assert quant.value('CHI',2026,'passing.explosive_completion_rate')['value']==100
    assert field({'complete_pass':None},'explosive_completion') is None
    assert field({'complete_pass':1},'explosive_completion') is None


def test_red_zone_pass_and_rush_are_separate_on_both_sides(quant,monkeypatch):
    one=play('1');one.update(epa=1)
    two=play('2');two.update(epa=-1,sack=1)
    three=play('3');three.update(play_type='run',qb_dropback=0,pass_attempt=0,rush_attempt=1,epa=9)
    attach(quant,monkeypatch,[one,two,three])
    for side,team in [('offense','CHI'),('defense','SEA')]:
        assert quant.value(team,2026,f'matchup.{side}_red_pass_epa')['value']==0
        assert quant.value(team,2026,f'matchup.{side}_red_rush_epa')['value']==9
        assert quant.value(team,2026,f'matchup.{side}_red_epa')['value']==3


def test_every_displayed_comparison_uses_same_population_statistic_and_scale(quant):
    result=quant.matchup('CHI','SEA',2026)
    for comparison in result['comparisons']:
        assert len(comparison['factors'])==11
        assert all(factor['comparable'] is True for factor in comparison['factors'])
        assert {'Red-zone passing efficiency','Red-zone rushing efficiency','Red-zone overall efficiency'}<={f['factor'] for f in comparison['factors']}
    assert result['win_probability'] is None and result['score_distribution'] is None
