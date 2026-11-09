# Full walk-forward backtest of the current strategy (2013-2026)

`research/full_backtest.py`. Player models retrained each season on prior seasons only (current features),
isotonic calibration and template lifts estimated from prior seasons only; one best slip per game per strategy;
voided (inactive) legs excluded. 2006-2011 cannot be tested out-of-sample (player history starts 2009).

| Strategy | Slips | Hits | Hit rate | Predicted | ROI if book prices legs independently (4.5%/leg) | ROI if book prices correlation (+4.5%/leg) | ROI at our fair odds |
|---|---|---|---|---|---|---|---|
| A. Ceiling QB stack (QB yds + 2 own WR/TE yds) | 3,410 | 473 | 13.9% | 13.3% | **+30.3%** | −5.1% | +8.3% |
| B. ATTD 2+1 split | 3,676 | 195 | 5.3% | 5.4% | −12.6% | −11.1% | +1.4% |

* Strategy A: walk-forward lift 1.38, positive under independent pricing in both halves (2013-19 +27%, 2020-26 +33%);
  max drawdown 57 units, longest losing streak 45 slips. Edge exists only to the extent the book under-prices
  the QB-receiver correlation.
* Strategy B: after calibration the 2+1 lift on the model's top picks is 0.99 — the 1.30 pair lift seen in the raw
  study came from uncalibrated (too-low) TD probabilities. ATTD parlays carry no correlation edge; max drawdown 513 units.
