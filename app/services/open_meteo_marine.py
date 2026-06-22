from datetime import datetime, timedelta
import json
from urllib.parse import urlencode
from urllib.request import urlopen


CACHE_TTL = timedelta(hours=1)
ENDPOINT = "https://marine-api.open-meteo.com/v1/marine"
CURRENT_VARIABLES = (
    "wave_height",
    "wave_period",
    "wave_direction",
    "sea_surface_temperature",
)

_cache: dict[tuple[float, float], tuple[datetime, dict]] = {}


def _cache_key(latitude: float, longitude: float):
    return (round(float(latitude), 4), round(float(longitude), 4))


def _format_number(value, digits: int = 1):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def get_current_marine_conditions(latitude: float, longitude: float):
    key = _cache_key(latitude, longitude)
    cached = _cache.get(key)
    now = datetime.now()
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join(CURRENT_VARIABLES),
        "timezone": "auto",
        "cell_selection": "sea",
    }
    url = f"{ENDPOINT}?{urlencode(params)}"
    with urlopen(url, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("error"):
        raise RuntimeError(payload.get("reason") or "Open-Meteo Marine API 오류")

    current = payload.get("current") or {}
    result = {
        "source": "Open-Meteo Marine",
        "fetched_at": now.isoformat(timespec="seconds"),
        "observed_at": current.get("time"),
        "wave_height": _format_number(current.get("wave_height")),
        "wave_period": _format_number(current.get("wave_period")),
        "wave_direction": _format_number(current.get("wave_direction"), 0),
        "sea_surface_temperature": _format_number(current.get("sea_surface_temperature")),
    }
    _cache[key] = (now, result)
    return result
