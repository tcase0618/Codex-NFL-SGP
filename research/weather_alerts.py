"""Read-only future-game weather screens; no projection or betting adjustments."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from math import isfinite
from pathlib import Path
import re
from threading import Lock

import requests

from research.team_schedule import instant

POINTS = Path(__file__).with_name("stadium_points.json")
USER_AGENT = "CaseCapitalFootballResearch/1.0 (local weather research)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
METRICS = {
    "wind": "Review passing accuracy, air yards, receiving volume and field-goal range; effects are not calibrated for this game.",
    "cold": "Review ball handling, passing and kicking context; this is a screening threshold, not a predicted change.",
    "precipitation": "Review passing, rushing share, ball security and field conditions if rain or snow is confirmed.",
}


def wind_mph(value):
    if isinstance(value, str) and value.strip().lower() == "calm":
        return 0.0
    numbers = re.findall(r"\d+(?:\.\d+)?", str(value or ""))
    return max(map(float, numbers)) if numbers and "mph" in str(value).lower() else None


def valid_number(value):
    return type(value) in (int, float) and isfinite(value)


def classify(game, periods, now):
    kickoff = instant(game["kickoff"])
    if game.get("state") != "pre" or not game.get("kickoff_confirmed") or kickoff <= now:
        return {"status": "NOT_FUTURE", "reason": "Only confirmed future kickoffs are screened."}
    end = kickoff + timedelta(hours=4)
    covered = []
    for period in periods:
        try:
            start = instant(period["startTime"])
            stop = instant(period["endTime"])
        except (KeyError, ValueError, TypeError):
            continue
        if start < end and stop > kickoff and stop > start:
            covered.append((start, stop, period))
    covered.sort(key=lambda row: row[0])
    cursor = kickoff
    for start, stop, _ in covered:
        if start > cursor:
            break
        cursor = max(cursor, stop)
    if cursor < end:
        return {"status": "OUTSIDE_FORECAST_WINDOW", "reason": "Hourly forecast does not cover the full four-hour game window."}
    periods = [row[2] for row in covered]
    speeds = [wind_mph(p.get("windSpeed")) for p in periods]
    temps = [p.get("temperature") if p.get("temperatureUnit") == "F" and valid_number(p.get("temperature")) else None
             for p in periods]
    pops = [p.get("probabilityOfPrecipitation", {}).get("value")
            if isinstance(p.get("probabilityOfPrecipitation"), dict) else None for p in periods]
    pops = [p if valid_number(p) and 0 <= p <= 100 else None for p in pops]
    complete = all(w is not None and t is not None and p is not None
                   for w, t, p in zip(speeds, temps, pops))
    max_wind = max((v for v in speeds if v is not None), default=None)
    min_temp = min((v for v in temps if v is not None), default=None)
    max_pop = max((v for v in pops if v is not None), default=None)
    signals = []
    if max_wind is not None and max_wind >= 15: signals.append("wind")
    if min_temp is not None and min_temp < 40: signals.append("cold")
    if max_pop is not None and max_pop >= 30: signals.append("precipitation")
    if all(v is None for v in (max_wind, min_temp, max_pop)):
        return {"status": "UNAVAILABLE", "reason": "Forecast periods have no usable weather values."}
    if not signals and not complete:
        return {"status": "PARTIAL_FORECAST", "reason": "At least one weather field is missing, so this game cannot receive a clear screen."}
    return {"status": "WATCH" if signals else "NO_SCREEN_TRIGGER",
            "reason": ("Screening threshold met; other forecast fields are incomplete." if not complete else
                       "Screening thresholds met; review before using game projections.") if signals else
                      "No screening threshold met in the complete hourly forecast.",
            "signals": signals, "metric_reviews": [METRICS[key] for key in signals],
            "forecast_complete": complete,
            "max_wind_mph": max_wind, "min_temperature_f": min_temp, "max_precipitation_probability_pct": max_pop,
            "window_start": kickoff.isoformat(), "window_end": end.isoformat(),
            "thresholds": {"wind_mph_at_least": 15, "temperature_f_below": 40, "precipitation_probability_pct_at_least": 30}}


class WeatherProvider:
    def __init__(self, archive_dir=None, *, get=requests.get, clock=None, points_path=POINTS):
        self.archive_dir = Path(archive_dir) if archive_dir else None
        self.get = get
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.points = json.loads(Path(points_path).read_text(encoding="utf-8"))
        self.cache = {}
        self.lock = Lock()

    def _fetch(self, url):
        if not url.startswith("https://api.weather.gov/"):
            raise ValueError("unexpected weather source")
        response = self.get(url, headers=HEADERS, timeout=(5, 15), allow_redirects=False)
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("weather source did not return data")
        raw = response.content
        digest = hashlib.sha256(raw).hexdigest()
        if self.archive_dir:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            target = self.archive_dir / (digest + ".json")
            if not target.exists():
                with target.open("xb") as stream: stream.write(raw)
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError("weather archive mismatch")
        return json.loads(raw), {"reference": url, "sha256": digest,
                                 "observed_at": self.clock().isoformat()}

    def game(self, game):
        now = self.clock()
        base = {"game_id": game["game_id"], "venue": game.get("venue"), "betting_ready": False}
        if game.get("state") != "pre" or not game.get("kickoff_confirmed") or instant(game["kickoff"]) <= now:
            return {**base, "status": "NOT_FUTURE", "reason": "Only confirmed future kickoffs are screened."}
        if game.get("venue_country") not in ("USA", "United States", "US"):
            return {**base, "status": "OUTSIDE_NWS_COVERAGE", "reason": "This venue is outside the US forecast service or its country is unverified."}
        if game.get("venue_indoor") is True:
            return {**base, "status": "INDOOR", "reason": "Schedule marks this venue indoor; stadium roof state can change for retractable roofs."}
        venue = self.points["venues"].get(str(game.get("venue_id", "")))
        if not venue or venue["name"] != game.get("venue"):
            return {**base, "status": "LOCATION_UNVERIFIED", "reason": "Stadium coordinate and schedule venue identity do not match."}
        if venue["roof_type_reference"] != "Outdoors" or game.get("venue_indoor") is not False:
            return {**base, "status": "EXPOSURE_UNVERIFIED", "reason": "Outdoor weather exposure is not verified for this stadium or roof state."}
        cache_key = (game["game_id"], game["kickoff"], game["venue_id"])
        with self.lock:
            cached = self.cache.get(cache_key)
            if cached and timedelta(0) <= now - cached[0] < timedelta(minutes=15):
                return cached[1]
        try:
            lat, lon = venue["latitude"], venue["longitude"]
            point, point_source = self._fetch(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
            hourly_url = point["properties"]["forecastHourly"]
            hourly, forecast_source = self._fetch(hourly_url)
            result = classify(game, hourly["properties"]["periods"], now)
            generated = hourly["properties"].get("generatedAt") or hourly["properties"].get("updateTime")
            if not generated or instant(generated) > now + timedelta(minutes=5):
                result = {"status": "UNAVAILABLE", "reason": "Forecast generation time is missing or ahead of the source check."}
            elif now - instant(generated) > timedelta(hours=6):
                result = {"status": "STALE_FORECAST", "reason": "The hourly forecast was generated more than six hours ago."}
            result = {**base, **result,
                      "source": {"provider": "US National Weather Service", "forecast": forecast_source,
                                 "point_lookup": point_source, "generated_at": generated,
                                 "coordinate_reference": self.points["coordinate_source"],
                                 "coordinate_source_sha256": self.points["coordinate_source_sha256"]}}
        except (requests.RequestException, ValueError, KeyError, TypeError, OSError, json.JSONDecodeError):
            result = {**base, "status": "UNAVAILABLE", "reason": "Current hourly weather forecast could not be verified."}
        with self.lock:
            self.cache[cache_key] = (now, result)
        return result
