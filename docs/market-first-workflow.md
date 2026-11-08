# Market first ticket workflow

The audited ticket constructor starts with a frozen capture of **actual offered FanDuel Tennessee player markets**. It will return `BLOCKED` before considering players when the capture or its separately recorded SHA-256 is missing. Historical replay thresholds such as 25+ receiving yards are research fixtures and cannot be substituted for an offer.

1. Observe the real game, player, market, line and individual decimal price at FanDuel Tennessee. Record the sportsbook page URL and observation time. Save the raw evidence separately for human review. Do not infer an offer from a box score, another sportsbook or a generic prop comparison.
2. Freeze a JSON capture using `FANDUEL_TN_OFFER_CAPTURE_V1`. Record its SHA-256 in the audit report's `verified_artifacts` and the market auditor's `evidence.market_capture_sha256`, independently of the capture file. Convert American odds to decimal only from an observed price: positive `+A` becomes `1 + A/100`; negative `-A` becomes `1 + 100/A` where `A` is the absolute value. Preserve the original display price in raw evidence.
3. Build typed `Leg` values from exactly those captured rows. Call `construct_audited_tickets(..., market_capture_path=<absolute path>, market_capture_sha256=<recorded digest>, accuracy_report=..., model_version=..., audit_checks=...)`. The preflight checks book, state, game, kickoff, source URL and time, unique offer IDs, and exact player, team, market, line and individual price equality. The seven-auditor clearance and scenario/eligibility engine then evaluate candidates.
4. Before any value assessment, obtain the exact FanDuel **combined** quote for the chosen legs. `screen_prices` checks the complete leg keys and quote freshness. Compare its break-even probability with a separately validated joint probability and uncertainty. If the combination is unavailable, the quote is stale, or the joint model is unvalidated, label the ticket `NOT MEASURABLE` or `RESEARCH_ONLY` as appropriate.

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
