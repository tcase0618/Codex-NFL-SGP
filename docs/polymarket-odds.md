# Polymarket public NFL odds

The Matchups page fetches Polymarket public quotes when a scheduled game is selected. Prices use **American odds** with an explicit plus/minus sign, e.g. +150 or -250. The UI provides market-type filters, eight-market pages, a manual refresh button, and refreshes every 30 seconds while visible. It displays only provider-listed markets; missing player props are not manufactured.

## Sources and selection

- Official Gamma discovery: `GET https://gamma-api.polymarket.com/events?slug=...`.
- Official public CLOB books: `GET https://clob.polymarket.com/book?token_id=...`.
- Documentation: https://docs.polymarket.com/api-reference/predictions/overview and https://docs.polymarket.com/api-reference/market-data/get-order-book.
- No API key, account, wallet, signing, order, purchase or execution operation is used.

The server binds the request to an actual game in the selected NFL schedule week. It requires the provider's two NFL team identities, away/home orientation, and kickoff within 60 seconds. Explicit aliases LAR/LA and WSH/WAS preserve canonical team identities. An absent, ambiguous, closed or mismatched event returns unavailable.

Only active, open, accepting-order markets with enabled order books are selected. Outcome labels and token IDs stay paired by the provider's array order. Books must match both the token and condition ID. Markets retain their original question, line, period/type, outcome wording, settlement rules, fee information and source metadata.

## Price definition and American conversion

The headline quote is the lowest positive-size ask for buying that specific outcome, not the first book row, midpoint, last trade, or Gamma outcomePrices. Bids are kept separately in the inspectable source details. Array order is not assumed to be price order.

For a valid share price p strictly between 0 and 1 with a $1 winning payout:

- p <= 0.5: American = +100 * (1-p) / p.
- p > 0.5: American = -100 * p / (1-p).
- Round to the nearest integer, half away from zero; p=0.5 is +100.

Examples: 0.40 -> +150; 0.60 -> -150; 0.25 -> +300; 0.75 -> -300. Exact source prices remain stored. Zero, one, NaN, infinity, malformed levels, wrong identities, crossed/locked books and future-dated books do not become fresh quotes.

Odds are **gross, before fees**. They are a representation of Polymarket contract prices, not model-calibrated probabilities or FanDuel Tennessee offers. Contract tie, cancellation and settlement rules can differ from sportsbook markets. No odds are multiplied into an invented parlay price, and the existing ticket/readiness gates are unchanged.

## API and freshness

`GET /api/polymarket/odds?season=2026&season_type=2&week=9&game_id=<schedule-id>&market_type=moneyline&page=0`

Omit the complete season/week tuple to use the current provider calendar. `market_type=all` is the default; returned `market_types` lists actually offered categories. Page size is eight markets with at most four outcomes per market; read requests use at most six workers. Metadata caches for 120 seconds, books for 20 seconds. A book older than 120 seconds is labeled STALE, never AVAILABLE. Missing asks have no American quote; failed fetches do not use stale-cache or last-trade fallbacks. Each quote shows its original book timestamp and retrieval/source evidence.

With `--schedule-archive` configured, raw provider JSON is retained by SHA256 under its `polymarket/` subfolder. Each response report has a new immutable filename. Prior player/team snapshots are unchanged. These are current captured observations, not historical pregame snapshots.

## Validation

Tests cover favorite/underdog/even-money conversions, invalid values, unordered book levels, identity/cutoff failures, stale and empty asks, exact raw archive hashes, array pairing, filtering, pagination, cache expiry and the actual local HTTP route. UI logic checks ensure American formatting, safe source text and stale/missing labels. Live API validation is saved under the task output directory `outputs/polymarket-odds`.

Final verification: 1,214 Python tests passed, 1 skipped; 30 dedicated Polymarket tests passed; four focused UI logic tests passed. Live HTTP checks returned fresh American quotes for JAX–BAL (all-market page and moneyline filter) and LAR–WSH (alias mapping). All inspected archived order-book hashes matched. The widget and Matchups HTML are served by the updated local server; browser-rendered visual QA was not performed.
