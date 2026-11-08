# Player history and trade-aware forecast rebuilds

Implemented November 3, 2026 in `football_engine.player_history`.

## Preserved identity

A `PlayerProfile` uses a stable player ID plus explicitly mapped provider IDs.
A team is an assignment, not part of the player's identity. Team events preserve
both effective time and the source observation time. Prior statistics stay tagged
with their original team and role; history references survive a move unchanged.
Names alone do not authorize merging profiles. Snapshot persistence rejects
duplicate player/provider IDs and refuses to overwrite an existing file.

`record_team_event` returns a new profile instead of altering the previous version.
Pending trades do not transfer confirmed membership. Conflicting origin teams and
duplicate events require reconciliation. A date-only transaction must first have
its effective-time ambiguity resolved; the interface requires an aware timestamp
and must not be fed a guessed midnight. Querying `team_as_of` requires both an
effective assignment and evidence observed by that time. Today's source retrieval
cannot backdate historical model knowledge.

## Mandatory rebuild

`rebuild_after_team_event` clears the supplied snapshot's scenarios and correlation
samples. If the player is in its prop pool, it updates the team while clearing
active eligibility until confirmed again. A released player is removed. Defenders
outside the prop pool still invalidate forecasts. Existing frozen tickets and
earlier profile versions are never rewritten.

The handler must be called for each affected old-team, new-team and opponent
snapshot. It is an explicit integration API; the collector does not yet poll
transactions, automatically resolve cross-provider IDs or invoke the handler.
An empty scenario set fails the existing engine's scenario auditor.

## Scheme assessment to rebuild

Evaluate player ability separately from expected deployment. Offensive evidence
includes alignment, route and snap participation, personnel grouping, QB tendencies,
target competition, blocking duties and red-zone/goal-line roles. Defensive evidence
includes interior/edge alignment, gap assignment, run/pass rotation, pressure and
double-team rates, teammates and opponent protection. Team and opponent effects
must both be represented; the former team also loses the player's contributions.

Use limited-installation, normal-rotation and expanded-role scenarios as supported
by evidence, without preset numerical bonuses. An interior defender could change
opposing rushing efficiency, QB pressure and checkdown distribution; the direction
and magnitude remain hypotheses until supported. Receiving-target changes can use
the sourced role allocator. Other opportunities and efficiencies must be supplied
with their own evidence, then joint scenarios regenerated. This change provides
history and invalidation; it does not fit an automatic scheme-success predictor.

## Simmons example and evidence boundary

The Titans' October 29 announcement reported the Chicago agreement pending a
physical. The Bears' October 31 report describes the completed acquisition.

- https://www.tennesseetitans.com/news/titans-trade-dt-jeffery-simmons-to-the-chicago-bears-in-exchange-for-two-first-round-picks
- https://www.chicagobears.com/news/bears-players-coaches-thrilled-with-addition-of-jeffery-simmons

These reports support the move. They do not establish a quantified causal effect
on Seattle's final statistics. No trade-adjusted historical parlay outcomes or
prices were verified or regenerated in this change. No exact contract-effective
timestamp was inferred from either article for a historical assignment event.

## Regression coverage

Identity/history preservation, historical knowledge cutoff, pending trade gating,
conflicting-team rejection, activation reset, whole-snapshot invalidation including
defenders absent from the prop pool, and exclusive profile snapshot persistence.
