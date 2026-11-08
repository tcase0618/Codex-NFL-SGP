from research.source_based_parlays import eligible, norm, transaction_state, choose, explicit_roster_names


def fixture():
    leg = {"team_id": "CHI", "player": "Example Player", "player_id": "1"}
    game = {"kickoff": "2026-09-13T17:00:00Z"}
    names = {"CHI": {norm("Example Player")}}
    sources = {("CHI", norm("Example Player")): ["fixture:initial-roster"]}
    sections = [{"published_at": "2026-09-13T15:30:00Z", "source": "fixture:inactives",
                 "names_as_published": ["QB Different Player"]}]
    return leg, game, names, sources, {}, sections


def test_inactive_player_is_excluded_without_consulting_participation():
    values = fixture()
    assert eligible(*values)[0]
    values[-1][0]["names_as_published"].append("WR Example Player (injury)")
    ok, reason, _ = eligible(*values)
    assert not ok
    assert reason == "listed_inactive"


def test_not_listed_inactive_does_not_prove_team_membership():
    values = fixture()
    values[2]["CHI"].clear()
    ok, reason, _ = eligible(*values)
    assert not ok
    assert reason == "unknown_membership"


def test_future_inactive_source_and_same_day_transaction_fail_closed():
    from datetime import date
    values = fixture()
    values[-1][0]["published_at"] = "2026-09-13T18:00:00Z"
    assert eligible(*values)[1] == "inactive_report_published_after_cutoff"
    values[-1][0]["published_at"] = "2026-09-13T15:30:00Z"
    values[4]["CHI"] = [(date(2026, 9, 13), "Signed Example Player to the active roster.", "fixture:transaction")]
    assert eligible(*values)[1] == "same_day_transaction_time_unknown"


def test_practice_window_is_not_activation_and_no_selection_padding():
    assert transaction_state("Designated Example Player for return from IR.") == "reserve"
    assert transaction_state("Signed Example Player to the practice squad.") == "practice_squad"
    assert transaction_state("Signed Example Player from the practice squad.") == "active_roster"
    assert choose([], 8) == []


def test_evening_game_uses_eastern_date_for_undated_transaction_times():
    from datetime import date
    values = fixture()
    values[1]["kickoff"] = "2026-09-14T01:15:00Z"
    values[4]["CHI"] = [(date(2026, 9, 13), "Signed Example Player to the active roster.", "fixture:transaction")]
    assert eligible(*values)[1] == "same_day_transaction_time_unknown"


def test_roster_parser_accepts_position_lists_but_not_release_mentions(tmp_path):
    html = tmp_path / "source.html"
    text = tmp_path / "source.txt"
    html.write_text('<p>Released QB Wrong Person.</p><h3>Quarterback (1)</h3><div><ul><li>17 Right Person</li></ul></div><h3>Related Content</h3><ul><li>Later Person</li></ul>')
    text.write_text('Released QB Wrong Person.\nQuarterback (1)\n17 Right Person\nRB (2): 2 Good Runner, 3 Other Runner\nReserves (IR)\nBad Runner')
    names = explicit_roster_names({"html_file": str(html), "text_file": str(text)})
    assert norm("Right Person") in names
    assert norm("Good Runner") in names
    assert norm("Other Runner") in names
    assert norm("Wrong Person") not in names
    assert norm("Later Person") not in names
    assert norm("Bad Runner") not in names
