"""Open-Meteo client (https://open-meteo.com). Free, no API key, metric by default."""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

# WMO weather interpretation codes -> (short text, icon kind)
WMO = {
    0: ("Clear", "sun"), 1: ("Mostly clear", "sun"), 2: ("Partly cloudy", "partly"),
    3: ("Overcast", "cloud"), 45: ("Fog", "fog"), 48: ("Icy fog", "fog"),
    51: ("Light drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Heavy drizzle", "rain"),
    56: ("Freezing drizzle", "rain"), 57: ("Freezing drizzle", "rain"),
    61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"), 77: ("Snow grains", "snow"),
    80: ("Showers", "rain"), 81: ("Showers", "rain"), 82: ("Violent showers", "rain"),
    85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "storm"), 96: ("Storm, hail", "storm"), 99: ("Storm, hail", "storm"),
}


def describe(code) -> tuple[str, str]:
    return WMO.get(int(code) if code is not None else -1, ("Unknown", "cloud"))


@dataclass
class Weather:
    location: str
    now_temp: float | None
    now_code: int | None
    now_humidity: float | None
    now_wind_kmh: float | None
    today_min: float | None
    today_max: float | None
    tom_min: float | None
    tom_max: float | None
    tom_code: int | None
    tom_rain_prob: float | None
    tom_rain_mm: float | None
    fetched_at: str  # local HH:MM


def _get(url: str, params: dict, timeout=10) -> dict:
    q = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(q, headers={"User-Agent": "smartscreen-local/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def geocode(city: str) -> tuple[float, float, str] | None:
    data = _get(GEOCODE_URL, {"name": city, "count": 1, "language": "en", "format": "json"})
    res = (data.get("results") or [None])[0]
    if not res:
        return None
    return res["latitude"], res["longitude"], res.get("name", city)


def fetch(lat: float, lon: float, label: str) -> Weather:
    import time
    data = _get(FORECAST_URL, {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max,precipitation_sum",
        "timezone": "auto", "forecast_days": 2,
        "wind_speed_unit": "kmh", "temperature_unit": "celsius", "precipitation_unit": "mm",
    })
    cur, day = data.get("current", {}), data.get("daily", {})

    def d(key, i):
        v = day.get(key) or []
        return v[i] if len(v) > i else None

    return Weather(
        location=label,
        now_temp=cur.get("temperature_2m"), now_code=cur.get("weather_code"),
        now_humidity=cur.get("relative_humidity_2m"), now_wind_kmh=cur.get("wind_speed_10m"),
        today_min=d("temperature_2m_min", 0), today_max=d("temperature_2m_max", 0),
        tom_min=d("temperature_2m_min", 1), tom_max=d("temperature_2m_max", 1),
        tom_code=d("weather_code", 1), tom_rain_prob=d("precipitation_probability_max", 1),
        tom_rain_mm=d("precipitation_sum", 1),
        fetched_at=time.strftime("%H:%M"),
    )


DUMMY = Weather("Your City", 27.4, 2, 38, 11.0, 15.1, 29.0, 16.2, 24.8, 80, 70, 6.4, "12:30")
