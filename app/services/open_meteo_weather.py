from datetime import date, datetime, time, timedelta
import json
from urllib.parse import urlencode
from urllib.request import urlopen


CACHE_TTL = timedelta(hours=1)
FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_VARIABLES = (
    "temperature_2m",
    "apparent_temperature",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
)

_cache: dict[tuple, tuple[datetime, dict]] = {}


def _cache_get(key):
    cached = _cache.get(key)
    now = datetime.now()
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]
    return None


def _cache_set(key, value):
    _cache[key] = (datetime.now(), value)
    return value


def _format_number(value, digits: int = 1):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _parse_hour(value: str):
    return datetime.fromisoformat(value)


def _value_at(hourly: dict, key: str, index: int):
    values = hourly.get(key) or []
    if index >= len(values):
        return None
    return values[index]


def _result(source: str, observed_at: str | None, values: dict):
    return {
        "source": source,
        "observed_at": observed_at,
        "temperature": _format_number(values.get("temperature_2m")),
        "apparent_temperature": _format_number(values.get("apparent_temperature")),
        "wind_speed": _format_number(values.get("wind_speed_10m")),
        "wind_direction": _format_number(values.get("wind_direction_10m"), 0),
        "precipitation": _format_number(values.get("precipitation")),
    }


def get_current_weather(latitude: float, longitude: float):
    key = ("current", round(float(latitude), 4), round(float(longitude), 4))
    cached = _cache_get(key)
    if cached:
        return cached

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join(WEATHER_VARIABLES),
        "temperature_unit": "celsius",
        "wind_speed_unit": "ms",
        "precipitation_unit": "mm",
        "timezone": "auto",
    }
    url = f"{FORECAST_ENDPOINT}?{urlencode(params)}"
    with urlopen(url, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError(payload.get("reason") or "Open-Meteo 기상 API 오류")

    current = payload.get("current") or {}
    return _cache_set(
        key,
        _result("Open-Meteo Weather", current.get("time"), current),
    )


def get_historical_weather(latitude: float, longitude: float, weather_date: date, target_time: time | None = None):
    lookup_time = target_time or time(12, 0)
    key = (
        "historical",
        round(float(latitude), 4),
        round(float(longitude), 4),
        weather_date.isoformat(),
        lookup_time.strftime("%H:%M"),
    )
    cached = _cache_get(key)
    if cached:
        return cached

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": weather_date.isoformat(),
        "end_date": weather_date.isoformat(),
        "hourly": ",".join(WEATHER_VARIABLES),
        "temperature_unit": "celsius",
        "wind_speed_unit": "ms",
        "precipitation_unit": "mm",
        "timezone": "auto",
    }
    url = f"{ARCHIVE_ENDPOINT}?{urlencode(params)}"
    with urlopen(url, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError(payload.get("reason") or "Open-Meteo 과거 기상 API 오류")

    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        raise RuntimeError("과거 기상 데이터가 없습니다.")

    target_datetime = datetime.combine(weather_date, lookup_time)
    closest_index = min(
        range(len(times)),
        key=lambda index: abs(_parse_hour(times[index]) - target_datetime),
    )
    values = {key: _value_at(hourly, key, closest_index) for key in WEATHER_VARIABLES}
    return _cache_set(
        key,
        _result("Open-Meteo Historical Weather", times[closest_index], values),
    )
