---
name: football-player-workup
description: Verify injury status, team membership, transactions, and game-day activity for every named NFL player before constructing or publishing Case Capital parlay slips.
---

# Football player workup

Apply before a 3-, 5-, 8-leg or anytime-TD ticket, PDF, or claim that a named player is eligible for tonight's game. Work once per distinct player per game and recheck every affected ticket after a personnel change.

1. Fix the game ID, kickoff and decision time. Enumerate every distinct player ID, team ID and market on all proposed slips. Do not match a traded or similarly named player by name alone.
2. For **each** player, inspect and archive the current official team roster, a complete game-scoped injury report, transaction history through the decision time, and affirmative official game-day active evidence. Record source URL, observation time and SHA-256 of the exact raw archive for each. A missing injury row requires proof that the whole correct report was reviewed; it does not prove health. Absence from an inactive list or presence on a roster does not establish active status.
3. Record the injury/body-part detail, practice participation, final game designation, and any reported workload concern. An out, doubtful, questionable, unknown or unverified status cannot be described as healthy or fully cleared. A confirmed active player may still carry a performance or snap-limit risk; reflect that in scenarios and the report.
4. Reconcile the player's stable identity and team across the roster, transaction record, active evidence, market offer and game. Check starter role separately where the leg depends on the quarterback or depth chart. If a late change occurs, rebuild affected scenario outputs and ticket prices.
5. Feed the per-player evidence to the roster auditor's `player_workups`, bind each raw archive to `verified_artifacts`, and use `football_engine.construction.construct_audited_tickets` (or its anytime-TD wrapper). The constructor blocks when any offered player lacks a fresh injury/team/active workup. Never bypass it with a hand-written PDF and call the resulting lines model-selected.

Show one compact review row per player: player/team/ID, injury finding and designation, roster and transaction finding, official active proof, last check time, and blocker or limitation. If active evidence is not yet published, label the ticket research-only and state the remaining check. Source hashes and reviewer notes support inspection; they do not authenticate a self-supplied archive or establish predictive accuracy. Do not use a later box score or participation record as pregame evidence.
