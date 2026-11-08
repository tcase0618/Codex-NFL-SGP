# Team profiles and paired matchup workflow

Implemented November 3, 2026. The generated current dataset contains 32 profiles
from 121 final 2026 Week 1–8 games, with current roster/depth/injury context and
individual readable team reports. Regression suite: 62 passed.

## Updating evidence without rewriting history

`football_engine.team_profiles.TeamProfileStore` appends paired offense/opponent
observations for each final game. SQLite rejects updates and deletes. Reingesting
an identical box is idempotent. Changed box contents append a correction, and
historical queries select only corrections observed by their requested time.
The source URL, raw content hash and availability timestamp accompany every game.

`research.build_team_profiles` reads the cached final boxes now; it deliberately
does not pretend those versions were observed before past kickoffs. It writes a
new timestamped snapshot and 32 reports on each run. Earlier snapshots remain.
`season` filters by the latest observed season; `recent` uses the last four games.
The latest game and the underlying game IDs remain visible. Ratios divide summed
counts by summed exposures instead of averaging per-game percentages.

Profiles include plays, drives, attempts, yards, scoring, passing/rushing TDs,
third downs, box red-zone results and sacks, for both offense and opponent offense.
Sacks/(attempts+sacks) is explicitly a proxy and excludes scrambles. Points include
all scoring. None of these is automatically an opponent-adjusted predictive rate.

## Scheme and personnel

Current roster/depth/injury context is attached with retrieval times and source
hashes. Reserve/practice-squad sections and roster/depth disagreements remain
visible. Membership is not game-day eligibility. Identity mapping and active
status still require reconciliation before creating engine players.

`add_scheme_note` attaches sourced game observations or explicitly labeled
hypotheses and changes the profile version. Chicago's Simmons-related blocking
attention and Seattle's protection/intermediate-middle observations are represented
as reporting and hypotheses. Neither sets a numerical pressure adjustment.

Coverage mix, blitz and pressure rates, time to throw, personnel grouping,
route participation, red-zone roles and play-action rates remain unavailable until
supported by suitable data. An excellent or poor game does not establish a
permanent trait or isolate one player's causal effect. No arbitrary single-game
bonus or penalty is applied to a forecast.

## Plugging two teams together

`matchup_packet(first, second, as_of)` validates two profile hashes and timestamps,
then pairs each team's recent offense with its opponent's recent defense-allowed
metrics. A Baltimore–Jacksonville packet is included in the generated dataset.

`bind_matchup(snapshot, packet)` records both team-profile versions. First binding
or a version change clears the old scenarios; old player opportunities and joint
samples cannot pass as if they used the new evidence. The trade handler now clears
these bindings too, including when the traded player is a defender outside the
prop candidate pool.

After personnel and scheme evidence are reconciled, callers must supply rebuilt
scenario weights, player attempts/carries/targets, yardage efficiencies, separate
TD opportunities and correlation samples. The receiving-role allocator can
redistribute supported targets for an injury scenario. The new packet does not
fit those inputs or synthesize them from descriptive box averages.

`construct_parlay_family(snapshot, packet, offered)` checks the binding and runs
the existing constructor/evaluator for 3-, 5-, 8-leg and three-player anytime-TD
families. Mixed markets, including passing TD, are supported. Each candidate runs
the existing eight skill statuses and seven auditor checks; this does not mean
a separate multi-agent swarm was executed. No approved candidates is a valid
result. The bounded search requires an explicitly narrowed offered-market pool.
Exact combined quotes still go through `screen_prices`; generated family members
are not placed wagers or proof of profitable expected value.

## Operation and boundaries

Refresh official roster/depth/injury context with `research.collect_team_context`,
apply only hash-matching reviewed image transcriptions using
`research.enrich_team_context`, then run:

```
python -m research.build_team_profiles --output <output> --cache <espn-cache> --database <team-store.sqlite> --personnel <current-context.json> --scheme-notes <sourced-notes.json>
```

Recurring ingestion is now enabled through the Codex automation described in
`automatic-refresh.md`. Scheme notes still require reviewed evidence; a feed does not
automatically interpret film, fit predictive models or map sportsbook identities.
The historical parlay manifests, unresolved source-version gaps and lack of
verified historical combined prices remain unchanged.

## Verification

Tests cover paired atomic ingestion, nonfinal rejection, immutable/idempotent
storage, evidence cutoffs, append-only corrections, pooled rate denominators,
missing-data preservation, profile hash validation, sourced scheme-note versioning,
scenario invalidation on new evidence and trade-related binding resets.
