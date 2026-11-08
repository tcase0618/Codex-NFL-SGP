# Team identity and matchup UI

The team terminal now uses a black canvas, raised charcoal surfaces, and
franchise colors on the profile hero, navigation selection, charts and ranks.
Both teams retain their own accent in the matchup lab. Player screens keep
their existing styling; the new stylesheet is loaded only by `/teams`.

`terminal/team-visuals.js` contains all 32 primary/secondary palettes, taken
from the already archived November 4 ESPN scoreboards. Identity swatches retain
source colors. Text accents are lightened as needed to reach 4.5:1 contrast
against the raised dark surface; they are display variants, not new brand colors.

Strength percentiles are already normalized by the existing data layer, so
the UI does not invert defensive ranks again. Each metric separately labels
the preferred raw-value direction. A persistent legend explains that a higher
strength percentile is better. Context-only metrics show frequency and explicitly
have no preferred direction. Missing and low-sample ranks remain unranked.

The matchup table adds a third data column, **Combined matchup outlook**, beside
offense and defense. Passing efficiency, rushing success and sack exposure show
a descriptive comparison of league-relative profile ranks. The gap is measured
in percentile points across each metric's own qualified cohort; it is not a
learned matchup advantage, win probability, or expected statistic. Incompatible
explosive-pass and red-zone populations are flagged rather than combined.
Projected statistics and the game winner remain visibly unestimated because
the service supplies no supported prediction. No arithmetic midpoint or
independent-leg product is presented as a forecast.

The tables preserve metric/source inspection and export. Comparison tables
scroll inside a labeled keyboard-focusable region at narrow widths; both
team headers and the outlook remain available. Team/profile selection updates
theme variables without modifying source observations or model outputs.

Validation: five Node behavioral checks cover all-team contrast, normalized
defensive ranks, missing/contextual ranks, descriptive comparison direction,
and incompatible populations. Forty-nine team-quant regression tests pass.
The running local service returns HTTP 200 for the team page, both new static
assets, the updated script and the 32-team API.

Visual self-assessment: 8/10 for hierarchy, coherent surfaces, color identity
and explicit rank semantics. Final rendered visual assessment remains pending:
browser automation was unavailable, and native Computer Use stopped because it
could not confidently determine the browser URL. No screenshot review is claimed.
