# Full roster coverage and mixed-market construction

Implemented November 3, 2026. These changes expand candidate coverage and
construction; they do not establish a profitable strategy or repair historical
pregame source availability.

## Collection and identity

`research.collect_team_context` downloads all 32 official team roster, depth-chart
and injury-report pages, preserving retrieval times, content hashes and raw HTML.
Roster sections include active, reserve and practice squad; all remain visible.
Depth charts preserve each alignment row and within-row depth rather than
flattening multiple starting WR positions into a misleading WR1/WR2 order.
An empty parser result means unavailable/unparsed, never no injuries.

The first 96-page collection parsed 32 roster tables, 31 depth charts and 28 injury
pages. Houston's chart is an image; its full 60 entries were visually transcribed
and tied to the downloaded image hash. `research.enrich_team_context` applies
that transcription only to the exact matching image and produces a new artifact.
The chart labels itself October 20; it was retrieved November 3. Three listed
defenders do not match the current roster, so those profiles require reconciliation.
An old chart label does not prove historical availability or present freshness.

`research.team_candidates` joins roster and depth names into 2,536 current player
profiles, including backups with no recent box appearances. Profiles preserve
source references, status sections, missing-roster flags and depth alignments.
No sportsbook ID, forecast or game-day active status is invented. Exact identity
mapping, transactions, final inactives and injury evidence remain required before
these profiles can become engine `Player` inputs.

## Injury opportunity

`football_engine.roles.apply_receiving_roles` supports scenario-specific receiving
target redistribution. Callers supply the full team's baseline targets, active
status, replacement weights and capacity, with sourced evidence. Weights should
come from supported route/personnel/role information; depth rank alone is not
an allocation rule. Zero-history backups can receive a projection when evidence
supports their role. A third- or fifth-string receiver need not have earlier
box-score appearances to be considered.

Confirmed absences free baseline targets. Active available recipients inherit
only supported shares within their capacity; unsupported targets remain
unassigned. Team budgets cannot increase. Questionable statuses must be split
into available/out scenarios rather than silently promoted to confirmed absences.
Other teams' targets remain intact. This module does not estimate snap/route
shares itself, automatically interpret coach reports, or infer TD opportunity or
efficiency from target gains. All role sources feed the existing provenance check.
Changing workloads clears previous joint samples, requiring renewed simulation.

## Construction, modeling and settlement

`football_engine.construction.construct_tickets` accepts real offered lines and
constructs bounded 3/5/8-leg combinations. It permits distinct markets on the same
player (QB passing yards plus passing TD, RB yardage plus anytime TD). Duplicate
legs and nested thresholds for the same player/market cannot pad a ticket. Both
teams remain required. Each candidate runs the existing engine's eight skill
statuses and seven auditor checks. This is not a newly executed multi-agent swarm.

`passing_td` now has a separate engine branch using scenario passing attempts and
a supplied passing-TD-per-attempt rate. It has live attempt-cap checks. Anytime TD
continues to use rushing/receiving scoring opportunities; passing TDs never count
toward it. Final-box settlement extracts passing TD separately.

Every multi-leg construction still needs joint scenario samples. A QB passing TD
and a receiver TD can be the same scoring play; independent marginal multiplication
is insufficient. Exact combined sportsbook quotes remain required for price
screening. New source collection does not supply offered player props or prices.

## Historical boundaries

Current sources cannot be used to unblock Weeks 1–8 retrospectively. The 410
provisional historical drafts and 74 blocked slots remain unchanged. The new
constructor does not silently mix TD markets into the earlier yardage-only rules.
A revised historical manifest needs time-correct roster/depth/injury evidence,
actual offered markets and separately frozen rules; outcomes must be reported as
exploratory unless those pregame input versions are established.

## Validation

Regression coverage includes a zero-history receiver inheriting capped targets,
team opportunity conservation, unknown shares remaining unassigned, future and
ambiguous role evidence rejection, joint-sample invalidation, mixed eight-leg
construction with a repeated QB, missing passing-TD inputs, distinct passing-TD
settlement, complete-roster backup inclusion, and separate WR alignment rows.
