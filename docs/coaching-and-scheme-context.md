# Coaching and scheme context for all 32 teams

Implemented November 3, 2026. All team profiles now include current coaching
directory evidence, head-coach management descriptions, offensive and defensive
scheme assessments, play-calling verification status, coaching changes and
era-aware metric views. Earlier profile snapshots remain unchanged.

## Evidence and coverage

The official directories identify 32 head coaches, 32 offensive coordinators and
31 separately listed defensive coordinators. Tampa Bay has no separately titled
DC in its current directory; that absence is explicit rather than filled with
an invented hire. Carolina's Todd Wash is retained as interim, not permanent.
Ten head coaches are marked new to their team in 2026 following review of their
current biographies and announcements.

`research.collect_coaching` archives official staff directories and biographies
with raw content hashes and retrieval times. Supporting official-team articles
and transcripts were retrieved and reviewed, with each fact linked to its own
source. A separately dated ESPN September 9 coaching table supplies preseason
offensive calling assignments where current confirmation was not established.
Those are labeled `DATED_PRESEASON_REPORT`, not confirmed current responsibilities.
Unresolved defensive calling ownership remains `UNCONFIRMED`.

Directory title, actual game-day caller and on-field green-dot communicator are
separate concepts. A head coach may call plays while a coordinator leads meetings;
a linebacker relaying calls is not treated as the coaching play-caller.

## Schemes, leadership and tactical context

Each team has a sourced head-coach management description and offensive/defensive
assessment. These can describe tactical principles, personnel adaptation,
installation or explicit evidence limitations. They do not pretend to reveal a
complete private playbook. Additional tactical foundations cover verified examples
such as Buffalo's three-down structure, Pittsburgh's early-down menu, Baltimore's
disguise principles, Houston's attacking front, Indianapolis' opponent-specific
coverage changes, Tampa Bay's hybrid fronts, and San Francisco's wide-zone and
condensed-formation background.

Descriptions are explicitly scoped as current role, reported 2026 plan,
current-season observation, or historical background. Prior jobs are not silently
treated as the current team's deployment. Foundations must identify a coach
reconciled with the current staff. Sources and timestamps accompany both the
description and foundation.

Management descriptions include delegation, communication, installation standards,
personnel fit and leadership where sources support them. Unknown clock or fourth-down
aggression is not assigned a numerical value based on a coach's reputation.

Five dated reported rates are preserved separately: New England's Week 4 motion
and play-action frequencies, the Chargers' six-game blitz report, Minnesota's
October 29 blitz report and Las Vegas' debut four-man-rush pressure report. Their
time windows and definitions remain visible. They are not silently converted into
current calibrated model inputs or season-long rates.

## Changes and coaching eras

- Denver: Payton announced October 8 that he would reassume offensive calls for
  Week 5. Webb remains offensive coordinator. The preseason caller table cannot
  overwrite the later explicit report.
- Carolina: Canales delegated offensive calls to Idzik for 2026 while retaining
  broader head-coach and timeout duties. On October 30, Wash replaced Evero as
  interim DC and defensive caller. Older defensive games are not labeled as
  post-Wash evidence.

`attach_coaching` produces separate offensive and defensive coaching-era views.
Date-only announcements exclude games on the boundary day rather than inventing
an intraday transition time. An era with no observed game is marked explicitly;
unverified era boundaries remain unverified. Season and recent-four summaries
are still available for broader context, with their separate meanings intact.

## Model integration

Coaching data participates in each team's content hash. `matchup_packet` now
includes the attacking team's offensive caller/scheme and the opponent's defensive
caller/scheme, along with era evidence and matchup review dimensions. Binding a
changed profile invalidates existing opportunities and joint simulations before
the existing parlay-family construction workflow can run again.

Qualitative concepts have no automatic probability multiplier. The model still
needs supported player opportunities, lineup eligibility, scenario weights and
joint samples. A scheme label or a coach's successful past is not a mathematical
edge, and no new historical parlay record is claimed.

## Refreshing safely

The reviewed staff manifest is `research/coaching_staff_review_20261103.json`.
`research.review_coaching_2026` refuses to carry descriptions forward when names
or role titles differ from that reviewed manifest. Re-review the new staff and
evidence before approving a new context version. This prevents a fired coach's
scheme from being silently attached to a replacement.

`attach_coaching` rejects missing/future fact sources and directories older than
seven days. Historical scheme sources may be old but remain marked as historical;
the current staff directory itself must be refreshed. These APIs are now invoked
by the automatic refresh workflow described in `automatic-refresh.md`. The fixed November 3 review is not a claim
of continuous live coaching coverage.

Build the team profiles with `--coaching <reviewed-context.json>` added to the
existing `research.build_team_profiles` command. It requires all 32 coaching
records and creates a new timestamped JSON artifact, coaching overview and 32
individual reports with source links. Existing eight-skill/seven-auditor evaluator
checks remain in place; no separate multi-agent swarm was run in this task.

## Validation

Tests cover staff parsing, interim status, anchor-card biography links, explicit
unlisted DCs, future/stale evidence, reported-rate scopes, apostrophe identity
normalization, wrong-coach rejection, changed-staff review gates, coaching-era
boundaries and profile-version invalidation. The full suite must pass before the
new profile snapshot is delivered.
