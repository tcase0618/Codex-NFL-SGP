# Market first ticket workflow

The audited ticket constructor starts with a frozen capture of **actual offered FanDuel Tennessee player markets**. It will return `BLOCKED` before considering players when the capture or its separately recorded SHA-256 is missing. Historical replay thresholds such as 25+ receiving yards are research fixtures and cannot be substituted for an offer.

1. Observe the real game, player, market, line and individual decimal price at FanDuel Tennessee. Record the sportsbook page URL and observation time. Save the raw evidence separately for human review. Do not infer an offer from a box score, another sportsbook or a generic prop comparison.
2. Freeze a JSON capture using `FANDUEL_TN_OFFER_CAPTURE_V1`. Record its SHA-256 in the audit report's `verified_artifacts` and the market auditor's `evidence.market_capture_sha256`, independently of the capture file. Convert American odds to decimal only from an observed price: positive `+A` becomes `1 + A/100`; negative `-A` becomes `1 + 100/A` where `A` is the absolute value. Preserve the original display price in raw evidence.
3. Build typed `Leg` values from exactly those captured rows. Call `construct_audited_tickets(..., market_capture_path=<absolute path>, market_capture_sha256=<recorded digest>, accuracy_report=..., model_version=..., audit_checks=...)`. The preflight checks book, state, game, kickoff, source URL and time, unique offer IDs, and exact player, team, market, line and individual price equality. The seven-auditor clearance and scenario/eligibility engine then evaluate candidates.

   Run the [football player workup skill](../skills/football-player-workup/SKILL.md) for every distinct player in the offered pool before freezing a slip. The roster auditor must attach `player_workups` keyed by player ID; each one binds roster, complete injury report, transaction and affirmative game-day active archives to `verified_artifacts`. The constructor now rejects any missing, stale, mismatched, unbound or adverse per-player workup. An early report without official active evidence remains blocked. A structural workup pass does not prove publisher authenticity, health, or betting readiness.

   For every team with a passing, receiving or anytime-TD leg, bind a sourced low-QB-performance scenario alongside the normal script, even when the starter is already confirmed. Its modeled output must fall for every affected offered leg in one shared scenario, and its weight must meet the engine's stress floor. A changed starter also needs the separate QB-change evidence and downside check. The low-performance scenario's probability and player outcomes must come from a validated pregame model; do not invent a shock weight or a yardage haircut to clear this gate. A static PDF script that lists plausible legs but never invokes the audited constructor is a research illustration and cannot be presented as model-selected picks. If the same QB-dependent leg appears in all three tiers, review its common-failure risk explicitly before publishing a research card.
4. Before any value assessment, obtain the exact FanDuel **combined** quote for the chosen legs. `screen_prices` checks the complete leg keys and quote freshness. Compare its break-even probability with a separately validated joint probability and uncertainty. If the combination is unavailable, the quote is stale, or the joint model is unvalidated, label the ticket `NOT MEASURABLE` or `RESEARCH_ONLY` as appropriate.
5. `rank_priced_parlays` builds a research shortlist from exact combined FanDuel quotes. Its default 50% model joint-probability floor is a **screening preference**, not a calibrated claim. It also requires positive illustrative expected return, removes tickets beaten by another on both probability and payout, and shows the tradeoff frontier in American odds. Adjust the probability floor explicitly when studying other risk preferences. It never upgrades a ticket to betting ready.

Example capture shape (field names only; no prices are asserted here):

```json
{
  "schema": "FANDUEL_TN_OFFER_CAPTURE_V1",
  "book": "FanDuel",
  "jurisdiction": "TN",
  "game_id": "<exact game id>",
  "kickoff": "<ISO 8601 timestamp with offset>",
  "source": {"reference": "https://sportsbook.fanduel.com/<actual page>", "observed_at": "<ISO 8601 timestamp with offset>"},
  "offers": [{"offer_id": "<book market id>", "player_id": "<verified player id>", "team_id": "<team id>", "market": "receiving_yards", "line": "<numeric offered threshold>", "odds_decimal": "<numeric observed price>"}]
}
```

Replace the illustrative string placeholders for `line` and `odds_decimal` with numbers. The hash and URL checks detect changed files and obvious source mismatches; they do **not** authenticate a self-supplied file or prove a sportsbook displayed the offer. Independent source review and a real combined quote remain necessary. A structurally valid capture never guarantees favorable odds or predictive accuracy.
