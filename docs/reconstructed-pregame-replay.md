# Retrospective reconstruction versus certified replay

Implemented November 3, 2026. The original strict evidence gate remains in place.
Missing historical prices do not prevent an unpriced statistical target test;
they prevent offered-market and profit claims. The new runner deliberately saves
its selections separately from the prospective ledger.

`research.reconstructed_replay` reconstructs 3/5/8/TD targets for each of the
121 cached 2026 Week 1–8 games. It uses prior boxes only, ESPN player IDs,
dated depth snapshots at or before kickoff minus 30 minutes with a 48-hour
freshness limit, a weekly roster reserve-state screen, and published official
inactive sections. Source aliases LA/WAS map to engine teams LAR/WSH.
It does not use final-game participation to choose or replace players.

The weekly roster file is a current historical reconstruction with no per-row
availability timestamps. Its ACT state clears a roster-state check only; it
does not establish game-day active status. Historical depth and article versions
are also not independently certified. Therefore every result remains provisional,
`valid_pregame_backtest=false`, `engine_validation=false`, ROI unavailable.

The ranking baseline uses last-four-game smoothed hit frequencies with fixed
150 passing, 20 rushing and 25 receiving yard targets and three rushing/receiving
TD scorers. These are research targets, not verified offered lines. No calibrated
joint win probability, scenario/opportunity engine performance or value edge is
claimed. Selection rules were developed after inspecting the same season.

Every selection is written before fresh final boxes are fetched and checked
for event ID, final status and team IDs. `research.reconstructed_resolution`
then diagnoses missing box-score rows using event rosters, without changing
selections. Confirmed participants with no offensive row receive zero;
nonparticipants remain a separate ticket category, even if another leg failed.
No sportsbook void rule is inferred. All earlier attempts are preserved.

To certify a pregame replay, supply inspectable immutable historical source
versions, complete active rosters and available-at model inputs, plus offered
market snapshots and exact combined quotes for return analysis. Validate the
full scenario/opportunity model independently on an untouched period. Current
profiles must never be backdated to repair these gaps.

The source comparison also found empty cached player tables for Week 5
Houston–Tennessee (401872984). The runner now rejects empty player history;
`--history-cache` can supply a separately archived refreshed history. Inputs
record their file paths and hashes, without asserting pregame availability.
The corrected v4 run changed one three-leg hit to a miss versus v3. Prior
versions remain intact.

Public historical CSV source:
https://github.com/nflverse/nflverse-data/releases/tag/weekly_rosters
https://github.com/nflverse/nflverse-data/releases/tag/depth_charts
The injuries file was downloaded for coverage assessment but is not used in
selection because its per-row historical availability is not established.

Regression coverage checks depth cutoffs, stale charts, aliases, stable-ID
membership, inactive exclusion, roster ambiguity/reserve states, and immutable
selection through settlement diagnosis, plus empty-history rejection. Sixteen
focused tests pass. Final v4 statistical categories: 143 hits, 327 misses,
14 nonparticipant tickets, zero unclassified. These remain provisional.
