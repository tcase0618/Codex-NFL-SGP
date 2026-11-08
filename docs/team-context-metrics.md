# Team context overlay

This research overlay writes a new SQLite snapshot and never changes the input
team/player databases, runtime settings, betting gates, or historical artifacts.
It provides five exact existing catalog IDs; it creates no new catalog labels.

## Parent API

```python
from football_engine.team_context_metrics import TeamContextMetrics
context = TeamContextMetrics(overlay_path)
context.verify_base(base_path)  # optional one-time startup check, SHA256 streaming
value = context.value("HOU", 2026, "matchup.defense_vs_deep_passing", "season", as_of)
personnel = context.personnel("HOU", 2026, as_of)
sources = context.sources
base_hash = context.metadata["input_sha256"]
```

The parent may retain a singleton adapter. Each lookup opens and closes its own
read-only connection; no SQLite connection is shared across request threads.
`close()` is a compatibility no-op. Unsupported IDs/views and absent team-season
keys return `None`, so the parent can use its existing calculation. Supported
rows without applicable observations return `value=None`, a missing flag/reason,
and sample counts; a measured numeric zero remains zero.

Sources use string IDs `context:base:<base-id>` and
`context:official:<content-sha256>`. `sources` contains their references, hashes,
observation times, archived raw paths, and original base IDs when applicable.
Merge these source objects into the parent profile source list without assuming
all source IDs are integers. `verify_base()` compares the actual supplied base's
bytes with `metadata['input_sha256']`; it raises on mismatch. Do not call this
multi-GB check per request. The builder verifies the base hash before and after
the run, verifies each copied source hash, and refuses existing output folders.

## Existing supported IDs and definitions

| Exact ID | Definition | Favorable direction |
| --- | --- | --- |
| `offense.opponent_adjusted_offensive_epa` | Mean offensive play EPA minus opponent allowed-EPA baseline plus independent league mean | Higher |
| `matchup.opponent_adjusted_offensive_epa` | Same offensive calculation, existing duplicate label | Higher |
| `defense.opponent_adjusted_defensive_epa` | Mean EPA allowed minus opponent offensive-EPA baseline plus independent league mean | Lower |
| `matchup.opponent_adjusted_defensive_epa` | Same defensive calculation, existing duplicate label | Lower |
| `matchup.defense_vs_deep_passing` | Mean EPA allowed on competitive pass attempts with recorded air yards >=20 | Lower |

These are descriptive EPA statistics, not learned ratings, calibrated matchup
interactions, injury adjustments, or projected quality. The two duplicate label
pairs remain separate catalog slots but are only two distinct calculations.
The upstream EPA model is itself a provider model; its training vintage has not
been independently verified.

Competitive scrimmage excludes kneels, spikes and two-point attempts. EPA cohorts
include sacks/scrambles for aggregate opponent adjustment. Deep passing excludes
sacks and scrambles, includes incompletions and interceptions with known depth,
and never infers unknown depth from yardage. Its denominator is known-depth deep
attempts, not every opponent pass or completion.

For a focal team, all its games (including rematches) are excluded from the
opponent reference pools and league centering mean. Baselines pool all remaining
same-season observations before the snapshot cutoff; target samples use the
requested view. This avoids self-contamination but does not constitute pregame
walk-forward evaluation. Baseline sizes are reported for every opponent. Missing
opponent pools withhold the adjusted value. Sample gates require at least 100
target plays and three target games, plus 100 plays/three games for every used
opponent baseline. These are research screening gates, not empirically calibrated
reliability claims. No percentile or confidence interval is invented.

Views: season, last3, last5, last8, home, away, first_half, second_half, leading,
trailing, tied, short_rest, long_rest. Defensive game states reverse the source
offense's score differential. Rest views use reported schedule rest and exclude
week-one defaults. No man/zone, blitz/four-man, top/bottom opponent ranks,
percentile, confidence, opponent_adjusted lens, year-over-year or three-year
values are supplied by this adapter.

## Time and grain

Overlay keys are `(team, season, view, metric_id)` with a fixed `snapshot_as_of`.
Only source captures observed no later than that cutoff are eligible. Only games
whose date is strictly before the UTC cutoff date are included; same-day games
are conservatively excluded because the base has no verified completion time.
Earlier query cutoffs withhold values. Later queries expose the stored snapshot
cutoff and cannot make it fresh. Historical-season data is a retrospective
current archive, never certified as what was available before those games.

Per-result provenance includes source IDs, actual target game IDs, target and
baseline sample sizes, eligible/missing EPA counts, formula, revision, unit,
direction, cutoff policy, and provider-model limitation. Raw data keeps original
source grain: schedule game, play, roster listing, and depth-chart assignment.

## Personnel context and remaining gaps

The original 20 personnel metrics remain **0/20 populated**. Projected ratings,
backup quality, replacement drop-off, availability, missed starter snaps and
historical continuity have no supported measurement in this input. Current chart
rank one is not a confirmed starter, current roster listing is not health or
game-day availability, and a chart date label is not historical archive proof.

`personnel(team, season, as_of)` supplies supplemental current-capture rows and
distinct exact source-listed name counts: `roster_listed_count`,
`depth_listed_count`, `depth_row_count`, `depth_rank_one_listed_count`, and
`roster_listed_by_position`. Original roster sections and chart positions/ranks
remain in `rows`. Counts include every source-reported roster section; they do
not claim active or healthy players. These are outside the 401 metric slots.
Different-season queries return `None`; earlier-than-snapshot queries withhold
the counts. Visual transcriptions must bind to a locally hash-verified archived
image; HTML and referenced images are copied to the derived raw directory.

Build with `python -m research.build_team_context_metrics --help`. Use a fresh
output directory under the task's `outputs/metric-depth-expansion/team-context`.
`--compare-base` measures actual before/after season-view coverage across all
401 labels, records the parent calculation file hash, and reports exact IDs
added. See the generated `coverage.json`, `source-manifest.json`, `definitions.json`
and `personnel-limitations.json` alongside the new `team-context.sqlite`.

Source field reference: [nflverse play-by-play dictionary](https://nflreadr.nflverse.com/articles/dictionary_pbp.html).
