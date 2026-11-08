# Profile cleanup and next matchup

The profile screens retain team colors and the dark team theme while removing
decorative Unicode icons, arrows, logo ornaments, table row glyphs and category
numbers. UTF-8 punctuation that had been decoded as Windows-1252 was repaired
in the team HTML and JavaScript. Files are now read and written explicitly as
UTF-8. Actual values, formulas, team names and source boundaries are retained.

Each profile has one percentile guide. Team strength ranks are normalized for
direction; player percentile ranks remain raw-value peer ranks. Their guides
describe those different contracts. Rows retain the rank and optional cohort
band without repeating the interpretation in every cell.

Both screens have a **Next matchup** tab. Team pages fetch the current team
schedule; player pages use the selected player's latest roster team only when
the stored player is flagged current. Historical-only player records do not
inherit a guessed current opponent. A fixture is team schedule context and
never evidence that the selected player is eligible or participating.

`/api/next-matchup?team=CHI` retrieves the current-season ESPN schedule through
`research.team_schedule.ScheduleProvider`. Results are cached in memory for
15 minutes. Selection is by future kickoff or current in-progress state, skips
completed and cancelled/postponed placeholders, and naturally crosses bye-week
gaps. It never fabricates a bye label. No upcoming published game, acquisition
failure, and unannounced kickoff times have explicit display states. Date/time
labels use America/New_York. The calendar is independent of historical profile
season and view filters. The comparison button switches back to the fixture's
season and season view before comparing the actual opponent.

Optional `--schedule-archive <directory>` saves exact returned schedule bytes
under their SHA-256; source observation time, URL and digest accompany each
result. The running local terminal uses a dedicated schedule archive under the
task's `outputs/team-ui-clean` folder. Existing profile/metric databases and
automated refresh schedules are not changed.

Dynamic team accents are applied using DOM style properties rather than HTML
style attributes, so the existing restrictive Content-Security-Policy remains
unchanged. Async selection tokens prevent a slow prior request from replacing
the newly selected team's fixture. Source text is escaped before display.

Validation: 59 Python schedule/team-quant tests, 10 Node date/selection/theme
checks, JavaScript syntax checks, and live HTTP checks on both profiles, shared
assets and three actual team calendars. A live calendar crossing to Week 10 was
confirmed. Source bytes were archived. Rendered browser inspection remains
unavailable because local browser use is disabled; no screenshot review is
claimed. Design self-assessment: 8/10, with final rendered sizing and narrow-panel
visual review still needed before a 10/10 assessment.
