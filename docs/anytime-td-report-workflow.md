# Anytime TD tier in future matchup reports

Every new game report should present four separate research families: 3-leg,
5-leg and 8-leg mixed-market parlays, and a dedicated **three-scorer anytime TD
parlay**. Keep the TD tier visible even when evidence is incomplete. In that
case say `NOT GENERATED` and identify the missing inputs rather than naming
three unsupported scorers.

The TD tier requires exactly three distinct players with actual anytime-TD
markets at line 1. Passing-touchdown markets are different contracts and do not
count. Confirm each player's team and game-day eligibility. Rank candidates
from scenario-specific scoring chances: projected red-zone touches and end-zone
targets, conversion evidence, team drive opportunities, role changes, and
opponent personnel. Yardage floors or season TD totals alone do not establish
tonight's scoring opportunity. Compare at least a competitive, favorite-led and
underdog-led game script; preserve correlations and shared drive constraints.

Use `football_engine.construction.construct_audited_anytime_td_tickets` for
evidence-gated research construction. The underlying evaluator requires
touchdown-opportunity inputs and shared scenario samples. A structural PASS
still means `RESEARCH_ONLY` until independent game-day eligibility, exact
market rules, combined sportsbook price, and model validation are checked.
The UI displays a frozen TD ticket only when it is recorded in the ledger for
the selected game; it never invents a ticket from a profile.

In the PDF, put the TD ticket after the 3/5/8 tiers. For each scorer show the
market's exact player/team, American single-leg Yes ask or `NOT LISTED`, quote
time and provider, scoring-opportunity rationale, failure path, and any
availability condition. Show the actual combined parlay quote only if captured
for that exact combination; do not multiply singles into an invented payout or
multiply past scoring frequencies into a joint win probability. If one scorer
fails the evidence gate, rebuild the entire TD construction or mark the tier
unavailable. Distinguish a Polymarket US research quote from FanDuel Tennessee
offers and settlement rules.
