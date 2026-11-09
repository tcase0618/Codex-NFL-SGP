# Parlay strategy backtest (walk-forward, 2016–2026)

Generated from `research/strategy_backtest.py`, `research/vacated_usage_test.py` and the skill-by-stat check. Every probability is out-of-sample (models trained only on earlier seasons). 2,335,254 leg rows across 2,883 games. No historical sportsbook prices exist in the data, so ROI cannot be measured; calibration, skill and break-even prices are reported instead.

## 1. The strategy as bet on Nov 8 (v1) was mispriced

Raw model P(over) was too low in the betting zone because it blends in the chance a player doesn't play, while books void inactive players.

| Ticket rule | Tickets | Priced to hit | Actually hit (voids excluded) | Actually hit (voids = losses) |
|---|---|---|---|---|
| v1_safe3 | 2,881 | 64.1% | 76.1% | 68.1% |
| v1_mid3 | 2,881 | 49.7% | 61.7% | 54.7% |
| v1_upside4 | 2,881 | 18.9% | 29.2% | 19.1% |
| v2_safe3 | 2,882 | 65.1% | 65.5% | 57.1% |
| v2_mid3 | 2,877 | 50.0% | 51.9% | 42.4% |
| v2_upside4 | 2,882 | 19.2% | 21.0% | 12.4% |

v2 (walk-forward isotonic recalibration, ≤2 legs per team) is calibrated to within ~2 points at the ticket level and ~1 point per leg bucket.

Leg log loss: raw 0.3220 → recalibrated 0.3115.

## 2. Correlation is small

Within calibrated tickets the realised/product lift is ≈ 1.03. Same-game parlays carry no correlation edge here; extra legs mainly compound the book's margin.

## 3. Where the model has skill over a naive average (players who played)

| Market | AUC model | AUC EWMA | Brier skill vs EWMA (both recalibrated) |
|---|---|---|---|
| carries | 0.9625 | 0.9635 | -0.6% |
| passing_tds | 0.8115 | 0.7991 | +2.9% |
| passing_yards | 0.7721 | 0.7599 | +2.3% |
| receiving_yards | 0.8817 | 0.8828 | +1.1% |
| receptions | 0.8803 | 0.8801 | -0.0% |
| rushing_yards | 0.9324 | 0.9443 | +0.5% |
| targets | 0.8770 | 0.8779 | -0.1% |

The 10–35% MAE gains reported for the player models came almost entirely from predicting *who plays*; conditional on playing (how props are graded) skill over a naive EWMA is 0–3%. Receptions, targets and carries show none.

## 4. Role-shock spots carry the real signal (vacated-usage features)

| Market | Role-shock rows | MAE before | MAE after | Improvement | Bias before → after |
|---|---|---|---|---|---|
| attempts | 222 | 12.13 | 10.58 | +12.8% | -7.61 → -3.93 |
| carries | 2,917 | 1.75 | 1.64 | +6.4% | -1.02 → -0.51 |
| passing_tds | 222 | 0.80 | 0.78 | +2.5% | -0.27 → -0.18 |
| passing_yards | 222 | 83.82 | 74.13 | +11.6% | -48.09 → -21.75 |
| receiving_yards | 6,448 | 16.84 | 17.03 | -1.1% | -4.55 → -2.68 |
| receptions | 6,448 | 1.32 | 1.31 | +0.4% | -0.42 → -0.28 |
| rushing_yards | 2,917 | 9.06 | 8.92 | +1.5% | -4.11 → -1.90 |
| targets | 6,448 | 1.72 | 1.71 | +0.4% | -0.56 → -0.34 |

These features are now in the production player model (`nflquant/models/vacated.py`). The stored full-target player validation predates them.

A related defect was fixed: projected starting QBs who had not played in the team's last three games (promoted backups) were missing from the projection set.

## 5. Strategy v3 (`python -m research.strategy_v3 <game_id>`)

1. Price legs with walk-forward-calibrated P(over | plays).
2. Bet only validated markets: passing yards/TDs, receiving yards, and role-shock QB/RB volume. Exclude receptions/targets/carries in normal games.
3. Parlay fair value = product × 1.03; prefer singles and 2-leg tickets.
4. Require book price ≥ fair + 5% (single) / +10% (parlay); stake quarter-Kelly, max 1% bankroll.
5. Confirm inactives and the book's void rule (voids-as-losses cuts hit rates 8–10 points).
6. Log every bet with the offered and closing price; closing-line value is the only way to prove edge.

## 6. What remains unproven

No result here shows profit against a sportsbook. Edge requires the book's line to be worse than our calibrated probability, which can only be measured by recording real prices going forward.
