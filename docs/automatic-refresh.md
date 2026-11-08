# Automatic football profile refresh

Enabled November 3, 2026 for all 32 NFL teams through the active Codex chat
automation named **NFL game-day morning refresh** (ID
`update-all-nfl-team-profiles`). It uses a local 8:00 a.m. Eastern schedule check.
The football refresh proceeds only when an NFL game is scheduled for that local
date; non-game dates stop after the lightweight schedule check.
The computer must remain on and the desktop app running for local scheduled work.

## Cadence and scope

When invoked on a verified game-day morning, `research.automatic_refresh` fetches recent scoreboards and validates
new final games or statistical corrections. Daily schedule catch-up finds missed
regular-season games; postseason schedules are checked during the relevant months.
The importer is not limited to the earlier Week 1–8 research cache.

Roster, depth, injury, transaction, coaching-directory and news pages refresh
when due, after a newly ingested final/correction, or during a listed game window
if the updater is explicitly invoked. The saved automation adds no hourly,
evening or postgame run. This is polling, not an
instant final-whistle webhook. Every new final updates both teams' observations.

Source hashes and raw responses are preserved. Statistical change detection
ignores retrieval times and unrelated news/video metadata, so the same game is
not counted again merely because its web response changed. Revised statistics
append new observations without overwriting prior records.

## Sources and reviewed knowledge

The public data collectors require no paid sports-data subscription. Live odds,
combined parlay quotes and wager execution are not enabled by this automation.
Roster membership continues to be separate from game-day eligibility.

Missing injury pages have a targeted retry path with at most eight concurrent
club requests and a one-hour per-club cooldown. The cooldown is not a scheduler:
retries run only inside an authorized invocation. Clearing a source-context gap
requires a report matching the actual upcoming schedule's team, season and week,
with a future kickoff. Failures remain visible without discarding another club's
successful recovery. This still grants no game-day active clearance.

An optional `historical_injury_reports` config entry binds a recovered bundle by
path and SHA256. The updater replays the official archived report bytes and
matches team, opponent, season and week to stored games. These records are
historical context retrieved now, explicitly not verified pregame input versions.
They never fill unknown current-week cells or alter current player eligibility.

Dallas's bounded fallback now retains exact calendar-month and latest-club-news
coverage, detects repeated continuation pages, and preserves PARTIAL results.
Neither zero observed rows nor an exhausted page is certified season history.

Previously reviewed image charts may be reused only when the downloaded image
hash matches. Old image labels remain visible. Unparsed injury reports and failed
transaction sources remain gaps, including on otherwise quiet checks.

Coaching directories are compared to the reviewed coaching context. A new or
missing staff assignment removes current coaching approval and creates a source
review gate; `bind_matchup` rejects that affected matchup until reviewed. Old
scheme assumptions are not attached to a replacement coach. Existing unconfirmed
callers stay unconfirmed.

The scheduled agent reviews a bounded batch of pending official news items each
run, prioritizing staff/calling changes, trades, injuries and tactical observations.
It must archive the actual source and distinguish confirmed observations from
hypotheses. New coaching context is versioned, and a forced refresh applies a
reviewed change. Completed reviews go into an append-only review log. Remaining
items survive future polls; merely discovering a headline never marks it reviewed.

## Durable operation

The runtime config, state, lock and sources are under the task workspace's
`work/automatic-refresh` directory. The task outputs contain:

- `football-profiles-latest.md`: stable human-readable status and team links.
- `football-refresh-status.json`: current poll status, sources and gaps.
- Timestamped `automatic-football-refresh-*` folders: immutable run reports and,
  when rebuilt, complete profiles plus 32 individual team reports.

The updater holds an operating-system file lock. Overlapping runs skip without
overwriting health. A failed build leaves the prior validated profiles intact and
publishes a separate failure indicator. Missing critical context creates visible
review gates rather than silently reusing stale coaching or roster authority.

The heartbeat remains quiet on unchanged polls and already reported unchanged
gaps. It surfaces new finals, genuine corrections, verified material changes,
failures or issues requiring user action. It does not send external messages,
place bets, buy data, or modify unrelated repositories.

## Validation and current limits

The initial all-team run rebuilt 32 profiles and found no new finals or corrections.
It completed without fetch/ingestion errors, but retained six source gaps: a dated
Houston depth-chart image, unreadable injury tables for Houston/New York Giants/
New Orleans/San Francisco, and Dallas transaction-page coverage. These are
incomplete source coverage, not proof that those teams have no injuries or moves.
The next quiet poll preserved these gaps and did not rebuild unchanged profiles.

The full regression suite passed **78 tests**. Coverage includes final-only
ingestion, season phases, semantic correction detection, evidence time cutoffs,
staff-change matching, mutual exclusion, pending-review persistence, quiet-poll
gap persistence, near-game refresh windows and stable latest-report publication.

The automation was created and read back as active. The first unattended scheduled
wake had not yet occurred at enablement; completed interactive validation is not
claimed as an unattended run. Detailed scheme rates and player forecasts still
require supported inputs and reviewed evidence.
