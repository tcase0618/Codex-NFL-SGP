"""Weather alerts are source-bound screens, never measured projection changes."""
from datetime import datetime, timedelta, timezone
import json

from research.weather_alerts import WeatherProvider, classify

NOW = datetime(2026, 11, 6, 16, tzinfo=timezone.utc)
GAME = {"game_id": "fixture", "kickoff": (NOW + timedelta(days=2)).isoformat(),
        "kickoff_confirmed": True, "state": "pre", "venue": "Soldier Field", "venue_id": "3799",
        "venue_country": "USA", "venue_indoor": False}


def periods(*, wind="18 to 22 mph", temp=38, pop=50):
    start = datetime.fromisoformat(GAME["kickoff"])
    return [{"startTime": (start + timedelta(hours=i)).isoformat(),
             "endTime": (start + timedelta(hours=i+1)).isoformat(),
             "windSpeed": wind, "temperature": temp, "temperatureUnit": "F",
             "probabilityOfPrecipitation": {"value": pop}} for i in range(4)]


def test_screen_flags_weather_without_claiming_projection_change():
    result = classify(GAME, periods(), NOW)
    assert result["status"] == "WATCH"
    assert result["signals"] == ["wind", "cold", "precipitation"]
    assert result["max_wind_mph"] == 22
    assert "passing" in result["metric_reviews"][0]
    assert "yardage_adjustment" not in result


def test_missing_hour_and_unstarted_past_game_fail_closed():
    assert classify(GAME, periods()[:2], NOW)["status"] == "OUTSIDE_FORECAST_WINDOW"
    missing_wind = [{**period, "windSpeed": None} for period in periods(wind="5 mph", temp=70, pop=0)]
    assert classify(GAME, missing_wind, NOW)["status"] == "PARTIAL_FORECAST"
    one_missing = periods(wind="5 mph", temp=70, pop=0)
    one_missing[2]["probabilityOfPrecipitation"] = None
    assert classify(GAME, one_missing, NOW)["status"] == "PARTIAL_FORECAST"
    one_missing[0]["windSpeed"] = "20 mph"
    watch = classify(GAME, one_missing, NOW)
    assert watch["status"] == "WATCH" and watch["forecast_complete"] is False
    assert "incomplete" in watch["reason"]
    assert classify({**GAME, "state": "post"}, periods(), NOW)["status"] == "NOT_FUTURE"
    assert classify({**GAME, "kickoff_confirmed": False}, periods(), NOW)["status"] == "NOT_FUTURE"


def test_indoor_and_country_checks_precede_weather_fetch(tmp_path):
    points = {"coordinate_source": "fixture", "coordinate_source_sha256": "abc", "venues": {}}
    path = tmp_path / "points.json";path.write_text(json.dumps(points))
    def forbidden(*args, **kwargs): raise AssertionError("weather fetch should not occur")
    provider = WeatherProvider(get=forbidden, clock=lambda: NOW, points_path=path)
    assert provider.game({**GAME, "venue_indoor": True})["status"] == "INDOOR"
    assert provider.game({**GAME, "venue_country": "Spain"})["status"] == "OUTSIDE_NWS_COVERAGE"
    assert provider.game(GAME)["status"] == "LOCATION_UNVERIFIED"


def test_source_archive_and_cache_are_exact_and_return_screen(tmp_path):
    points = {"coordinate_source": "fixture", "coordinate_source_sha256": "abc",
              "venues": {"3799": {"name": "Soldier Field", "latitude": 41.8,
                                  "longitude": -87.6, "roof_type_reference": "Outdoors"}}}
    path = tmp_path / "points.json";path.write_text(json.dumps(points))
    calls = []
    class Response:
        status_code = 200
        def __init__(self, content): self.content = content
        def raise_for_status(self): pass
    def get(url, **_kwargs):
        calls.append(url)
        if "/points/" in url:
            return Response(json.dumps({"properties": {"forecastHourly": "https://api.weather.gov/gridpoints/LOT/1,1/forecast/hourly"}}).encode())
        return Response(json.dumps({"properties": {"generatedAt": NOW.isoformat(), "periods": periods(wind="5 mph", temp=70, pop=0)}}).encode())
    provider = WeatherProvider(tmp_path / "archive", get=get, clock=lambda: NOW, points_path=path)
    result = provider.game(GAME)
    assert result["status"] == "NO_SCREEN_TRIGGER"
    assert result["source"]["forecast"]["sha256"]
    assert len(list((tmp_path / "archive").glob("*.json"))) == 2
    assert provider.game(GAME) == result and len(calls) == 2


def test_source_failure_is_unavailable_not_clean(tmp_path):
    points = {"coordinate_source": "fixture", "coordinate_source_sha256": "abc",
              "venues": {"3799": {"name": "Soldier Field", "latitude": 41.8,
                                  "longitude": -87.6, "roof_type_reference": "Outdoors"}}}
    path = tmp_path / "points.json";path.write_text(json.dumps(points))
    def broken(*args, **kwargs): raise ValueError("source failed")
    assert WeatherProvider(get=broken, clock=lambda: NOW, points_path=path).game(GAME)["status"] == "UNAVAILABLE"


def test_missing_or_future_forecast_generation_time_cannot_clear_screen(tmp_path):
    points = {"coordinate_source": "fixture", "coordinate_source_sha256": "abc",
              "venues": {"3799": {"name": "Soldier Field", "latitude": 41.8,
                                  "longitude": -87.6, "roof_type_reference": "Outdoors"}}}
    path = tmp_path / "points.json";path.write_text(json.dumps(points))
    class Response:
        status_code = 200
        def __init__(self, content): self.content = json.dumps(content).encode()
        def raise_for_status(self): pass
    for generated in (None, (NOW + timedelta(hours=1)).isoformat()):
        def get(url, **_kwargs):
            if "/points/" in url:
                return Response({"properties": {"forecastHourly": "https://api.weather.gov/gridpoints/LOT/1,1/forecast/hourly"}})
            return Response({"properties": {"generatedAt": generated, "periods": periods(wind="5 mph", temp=70, pop=0)}})
        result = WeatherProvider(get=get, clock=lambda: NOW, points_path=path).game(GAME)
        assert result["status"] == "UNAVAILABLE"
