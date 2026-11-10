from dataclasses import replace
from datetime import timedelta

import pytest

from football_engine.engine import Leg, Player, evaluate_ticket, leg_key
from football_engine.roles import ReceivingRole, apply_receiving_roles
from football_engine.construction import construct_tickets, construct_anytime_td_tickets, verify_qb_context, verify_qb_underperformance
from football_engine.settlement import _final_totals
from research.collect_team_context import parse_tables
from research.team_candidates import team_candidates
from test_engine import NOW, SOURCE, snapshot
from test_settlement import final_box


def test_audited_passing_pool_requires_sourced_current_and_baseline_qb_ids():
    offered = (Leg("wr", "CHI", "receiving_yards", 95.5, 0, 2, SOURCE),)
    game = snapshot()
    assert "missing game-specific" in verify_qb_context(game, offered)[0]
    assert "missing game-specific" in verify_qb_context(
        game, (Leg("rb", "CHI", "anytime_td", 1, 0, 2, SOURCE),))[0]
    context = {"CHI": {"current_starter_id": "qb-1", "baseline_starter_id": "qb-1", "source": SOURCE}}
    assert verify_qb_context(replace(game, qb_context=context), offered) == ()
    changed = {"CHI": {**context["CHI"], "current_starter_id": "qb-2"}}
    assert "not declared" in verify_qb_context(replace(game, qb_context=changed), offered)[0]
    stale = {"CHI": {**context["CHI"], "source": replace(SOURCE, observed_at=NOW - timedelta(minutes=6))}}
    assert "source unavailable" in verify_qb_context(replace(game, qb_context=stale), offered)[0]
    from football_engine.ledger import _json_ready
    from football_engine.snapshot_evidence import decode
    restored = decode(_json_ready(replace(game, qb_context=context)))
    assert verify_qb_context(restored, offered) == ()


def test_audited_pool_requires_shared_sourced_qb_bad_game_for_yards_and_td():
    offered = (Leg("wr", "MIN", "receiving_yards", 60, 0, 2, SOURCE),
               Leg("wr", "MIN", "anytime_td", 1, 0, 2, SOURCE))
    game = snapshot()
    normal = replace(game.scenarios[0], weight=.8,
                     remaining={"wr": {"targets": 8, "red_zone_touches": 2}},
                     yards_per_opportunity={"wr": {"receiving_yards": 10}},
                     touchdown_rate={"wr": .2})
    bad = replace(game.scenarios[1], weight=.2,
                  remaining={"wr": {"targets": 4, "red_zone_touches": 0}},
                  yards_per_opportunity={"wr": {"receiving_yards": 10}},
                  touchdown_rate={"wr": .2})
    missing = replace(game, scenarios=(normal, bad))
    assert "scenario missing" in verify_qb_underperformance(missing, offered)[0]
    bad = replace(bad, qb_underperformance_teams=frozenset({"MIN"}), role_sources=(SOURCE,))
    covered = replace(game, scenarios=(normal, bad))
    assert verify_qb_underperformance(covered, offered) == ()
    yards_only = replace(bad, remaining={"wr": {"targets": 4, "red_zone_touches": 2}})
    assert "does not lower every affected leg" in verify_qb_underperformance(
        replace(game, scenarios=(normal, yards_only)), offered)[0]
    token = replace(game, scenarios=(replace(normal, weight=.99), replace(bad, weight=.01)))
    assert "below stress weight" in verify_qb_underperformance(token, offered)[0]
    from football_engine.ledger import _json_ready
    from football_engine.snapshot_evidence import decode
    assert decode(_json_ready(covered)).scenarios[1].qb_underperformance_teams == frozenset({"MIN"})


def test_promoted_backup_inherits_supported_targets_without_inventing_td_role():
    scenario = replace(snapshot().scenarios[0], remaining={"starter": {"red_zone_touches": 4}}, joint_samples=({"old": True},) * 100)
    roles = (ReceivingRole("starter", "CHI", False, 8, 10, 0, SOURCE, "out"),
             ReceivingRole("wr5", "CHI", True, 0, 5, 3, SOURCE),
             ReceivingRole("te", "CHI", True, 4, 7, 1, SOURCE))
    result = apply_receiving_roles(scenario, roles, as_of=NOW, team_id="CHI", team_target_budget=12,
                                   player_teams={r.player_id: r.team_id for r in roles})
    assert result.remaining["wr5"]["targets"] == 5
    assert result.remaining["te"]["targets"] == 7
    assert result.remaining["starter"]["targets"] == 0
    assert result.remaining["starter"]["red_zone_touches"] == 0
    assert result.joint_samples == ()
    assert result.touchdown_rate == scenario.touchdown_rate
    assert result.role_sources == (SOURCE,) * 3


def test_unknown_replacement_share_leaves_targets_unassigned_and_checks_evidence():
    roles = (ReceivingRole("out", "CHI", False, 8, 8, 0, SOURCE, "out"),
             ReceivingRole("wr5", "CHI", True, 0, 8, 0, SOURCE))
    kwargs = dict(as_of=NOW, team_id="CHI", team_target_budget=8,
                  player_teams={r.player_id: r.team_id for r in roles})
    scenario = replace(snapshot().scenarios[0], remaining={})
    result = apply_receiving_roles(scenario, roles, **kwargs)
    assert result.remaining["wr5"]["targets"] == 0
    with pytest.raises(ValueError, match="evidence"):
        apply_receiving_roles(scenario, (roles[0], replace(roles[1], availability="questionable")), **kwargs)
    with pytest.raises(ValueError, match="evidence"):
        apply_receiving_roles(scenario, (roles[0], replace(roles[1], source=replace(SOURCE, observed_at=NOW + timedelta(seconds=1)))), **kwargs)


def test_mixed_eight_leg_allows_qb_yards_plus_passing_td_and_requires_joint_samples():
    game = snapshot()
    specs = [("q1", "CHI", "passing_yards", 150), ("q2", "SEA", "passing_yards", 150),
             ("w1", "CHI", "receiving_yards", 25), ("w2", "SEA", "receiving_yards", 25),
             ("r1", "CHI", "rushing_yards", 20), ("t1", "SEA", "receiving_yards", 25),
             ("r1", "CHI", "anytime_td", 1), ("q1", "CHI", "passing_td", 1)]
    legs = tuple(Leg(p, t, m, line, 0, 2, SOURCE) for p,t,m,line in specs)
    players = {p: Player(p, t, True, frozenset(m for pid,_,m,_ in specs if pid == p), SOURCE) for p,t,_,_ in specs}
    remaining = {p: {"attempts": 35, "targets": 8, "carries": 12, "red_zone_touches": 4} for p in players}
    scenario = replace(game.scenarios[0], weight=1, remaining=remaining,
                       yards_per_opportunity={p: {"passing_yards": 8, "receiving_yards": 10, "rushing_yards": 5} for p in players},
                       touchdown_rate={p: .3 for p in players}, passing_td_per_attempt={"q1": .05},
                       joint_samples=tuple({leg_key(item): (i < 70 if item.market == 'anytime_td' else
                                                            i < 83 if item.market == 'passing_td' else True)
                                            for item in legs} for i in range(100)))
    game = replace(game, players=players, scenarios=(scenario,))
    assert len(construct_tickets(game, legs, 8)) == 1
    assert not construct_tickets(replace(game, scenarios=(replace(scenario, joint_samples=()),)), legs, 8)
    decision = evaluate_ticket(replace(game, scenarios=(replace(scenario, passing_td_per_attempt={}),)), legs)
    assert not decision.approved
    assert any("passing touchdown" in reason for reason in decision.reasons)


def test_three_scorer_family_requires_real_td_opportunity_and_joint_samples():
    game = snapshot()
    specs = (("r1", "CHI"), ("w1", "CHI"), ("r2", "SEA"))
    legs = tuple(Leg(player, team, "anytime_td", 1, 0, 2, SOURCE)
                 for player, team in specs)
    players = {player: Player(player, team, True, frozenset({"anytime_td"}), SOURCE)
               for player, team in specs}
    scenario = replace(game.scenarios[0], weight=1,
                       remaining={player: {"red_zone_touches": 4} for player, _ in specs},
                       touchdown_rate={player: .35 for player, _ in specs},
                       joint_samples=tuple({leg_key(leg): i < 75 for leg in legs}
                                           for i in range(100)))
    game = replace(game, players=players, scenarios=(scenario,))
    assert len(construct_anytime_td_tickets(game, legs)) == 1
    assert not construct_anytime_td_tickets(
        replace(game, scenarios=(replace(scenario, joint_samples=()),)), legs)
    assert not construct_anytime_td_tickets(
        replace(game, scenarios=(replace(scenario, touchdown_rate={}),)), legs)
    with pytest.raises(ValueError, match="anytime-TD"):
        construct_anytime_td_tickets(game, legs[:-1] +
                                     (replace(legs[-1], market="passing_td"),))


def test_passing_touchdown_settlement_is_not_anytime_td():
    box = final_box()
    box["boxscore"]["players"][0]["statistics"].append({"name": "passing", "labels": ["YDS", "TD"],
        "athletes": [{"athlete": {"id": "qb"}, "stats": ["240", "2"]}]})
    qb = _final_totals(box)["qb"]
    assert qb["passing_td"] == 2
    assert "anytime_td" not in qb


def test_roles_preserve_opponent_and_reject_missing_same_team_recipient():
    scenario = replace(snapshot().scenarios[0], remaining={"opponent": {"targets": 10}})
    roles = (ReceivingRole("backup", "CHI", True, 2, 4, 1, SOURCE),)
    kwargs = dict(as_of=NOW, team_id="CHI", team_target_budget=4,
                  player_teams={"backup": "CHI", "opponent": "SEA", "missing": "CHI"})
    result = apply_receiving_roles(scenario, roles, **kwargs)
    assert result.remaining["opponent"]["targets"] == 10
    with pytest.raises(ValueError, match="cover"):
        apply_receiving_roles(replace(scenario, remaining={"missing": {"targets": 2}}), roles, **kwargs)
    with pytest.raises(ValueError, match="budget"):
        apply_receiving_roles(scenario, roles, **{**kwargs, "team_target_budget": 1})


def test_full_roster_candidate_includes_no_history_backup_and_preserves_wr_alignments():
    html = b'<h2>Active</h2><table><thead><tr><th>Player</th><th>Pos</th></tr></thead><tr><td><a href="/p/rookie">New Rookie</a></td><td>WR</td></tr></table>'
    rows = parse_tables(html, "https://club.example/team/", "roster")
    source = {"team": "CHI", "kind": "roster", "reference": "fixture:roster", "retrieved_at": NOW.isoformat(), "content_sha256": "hash", "rows": rows}
    profile = team_candidates([source], "CHI")[0]
    assert profile["player"] == "New Rookie" and profile["forecast"] is None
    assert profile["eligibility"] == "REQUIRES_GAME_DAY_CONFIRMATION"
    depth = b'<h2>Offense</h2><table><tr><th>Position</th><th>First</th><th>Second</th></tr><tr><td>WR</td><td><a>A One</a></td><td><a>B Two</a></td></tr><tr><td>WR</td><td><a>C Three</a></td><td><a>D Four</a></td></tr></table>'
    parsed = parse_tables(depth, "https://club.example", "depth")
    assert [r["depth"] for r in parsed] == [1,2,1,2]
    assert parsed[0]["alignment_row"] != parsed[2]["alignment_row"]
