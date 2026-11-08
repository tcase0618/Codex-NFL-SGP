# Official-source gaps: November 3, 2026

The refresh no longer treats a blank injury report as healthy players, merges
opponent injury tables into the reporting team's rows, or supplies stale
Houston depth-chart roles to current candidate profiles.

The selected official Week 9 pages for Houston, New York Giants, New Orleans
and San Francisco have no current team report rows. Cincinnati's page contains
an opposing-team report without a usable current Cincinnati report. These are
explicit CURRENT_REPORT_NOT_AVAILABLE observations, with UNKNOWN eligibility.
The reason for missing publication is unconfirmed. Up to two previous selected
regular-season weeks are checked for historical context, which is stored
separately and never establishes current activity.

Houston's official image remains hash-identical to the reviewed October 20
chart. It is preserved as evidence, flagged stale and excluded from candidate
depth roles. Roster records remain available; no fresh depth order is invented.

Dallas's annual club transaction route returns 404. A fallback checks all six
official NFL monthly transaction categories and follows Next Page links.
The November signings pagination currently repeats its first page. The updater
detects this cycle, preserves the partial source evidence and retains the gap.
Zero returned Dallas rows are not represented as zero verified transactions.
The fallback is a calendar-month window, not complete season history.

Slovis and Reed now have official Baltimore roster rows and biography links.
Reviewed identity records bind exact club URLs to existing GSIS/ESPN IDs using
position, age, height and college agreement. Source hashes and observation
cutoffs are checked when attaching identities. Slovis's stale ESPN GB assignment
is retained as a provider conflict; the official Baltimore signing and roster
control current membership. Neither identity establishes game-day activity.

Configuration points to a new versioned reviewed-personnel file. Completed
pending reconciliations are retained in an append-only personnel review log.
Future context refreshes apply reviewed identities by exact club reference and
position; they never move identities by name alone. Prior profiles and reports
are unchanged.
