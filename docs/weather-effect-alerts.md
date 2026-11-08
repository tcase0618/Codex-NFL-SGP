# Future-game weather effect alerts

The Matchups page checks the National Weather Service hourly forecast for the four hours after a confirmed future kickoff at a verified outdoor U.S. venue. It checks the forecast only when someone opens the page; it does not change the game-day profile refresh schedule, place bets, or alter projections or odds.

## Screening rules

| Signal | Research threshold | Why review it |
| --- | --- | --- |
| Wind | Maximum forecast hourly wind speed at least 15 mph | Passing and kicking conditions merit review. |
| Cold | Minimum forecast hourly temperature below 40°F | Ball handling, passing, and kicking context merit review. |
| Precipitation chance | Maximum hourly probability of precipitation at least 30% | Confirm the eventual rain or snow type and field conditions. |

These thresholds are **uncalibrated screening choices**, not causal effects, predicted yardage changes, win probabilities, or bookmaker edges. The 2006–2026 Week 8 retrospective found weather associations, but historical game-day weather text and current pregame forecasts are different evidence. NWS precipitation probability is the chance of at least 0.01 inch at the point during the forecast period; it does not mean the game or field will be wet.

## Data and safeguards

- The weekly schedule supplies exact game identity, kickoff, venue ID, country and indoor flag. Stadium coordinates are a reviewed local snapshot from the `greerreNFL/stadiums` data file, bound to exact venue ID and name. The snapshot records its URL and SHA-256 hash. Unknown or changed venues fail closed.
- The NWS `/points/{lat},{lon}` API supplies the hourly forecast URL. Both source responses are archived by raw-byte SHA-256 when the terminal has a schedule archive directory. The API response exposes the source URL, hash, observation time and forecast generation time.
- A forecast older than six hours is marked stale. A missing or future generation time is unavailable. Missing full-window coverage is marked outside forecast window. Every game-window hour must have usable wind, Fahrenheit temperature and precipitation probability before a no-trigger screen is shown. A known trigger with incomplete other fields remains a watch and says the forecast is incomplete. Errors are unavailable. International games and uncertain roof exposure have their own states.
- `NO_SCREEN_TRIGGER` means only that none of the three research thresholds was met by a complete available forecast. It does not mean weather cannot affect the game. Retractable roofs and covered open-air venues need direct roof-state confirmation before treating outside weather as on-field conditions.
- The endpoint is `/api/week-weather` with the same optional `season`, `season_type`, and `week` selection as `/api/week-matchups`. It returns one state per game and never creates or modifies a ticket.

The NWS API documentation is at <https://www.weather.gov/documentation/services-web-api>. The NWS precipitation definition is at <https://www.weather.gov/ppg/forecast_terms>.

## Validation

Run `python -m pytest tests/test_weather_alerts.py tests/test_weekly_matchups.py -q` and `node --test tests/weekly-matchups.test.cjs` from the repository root. Verify the live Matchups page against `/api/week-weather` after restarting the local server so the new route is loaded.
