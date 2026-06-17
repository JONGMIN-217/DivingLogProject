from dataclasses import dataclass
from datetime import date, datetime, time
import json
from urllib.parse import urlencode
from urllib.request import urlopen


@dataclass
class MarineWeatherResult:
    provider: str
    observed_at: datetime | None = None
    water_temp: float | None = None
    wave_height: float | None = None
    wave_period: float | None = None
    wave_direction: float | None = None


class MarineWeatherProvider:
    name = "unknown"

    def fetch(self, latitude: float, longitude: float, weather_date: date, target_time: time | None = None) -> MarineWeatherResult | None:
        raise NotImplementedError


class OpenMeteoMarineProvider(MarineWeatherProvider):
    name = "Open-Meteo Marine"
    endpoint = "https://marine-api.open-meteo.com/v1/marine"
    hourly_variables = (
        "wave_height",
        "wave_period",
        "wave_direction",
        "sea_surface_temperature",
    )

    def fetch(self, latitude: float, longitude: float, weather_date: date, target_time: time | None = None) -> MarineWeatherResult | None:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(self.hourly_variables),
            "timezone": "auto",
            "start_date": weather_date.isoformat(),
            "end_date": weather_date.isoformat(),
            "cell_selection": "sea",
        }
        url = f"{self.endpoint}?{urlencode(params)}"

        with urlopen(url, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))

        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            return None

        target_datetime = datetime.combine(weather_date, target_time or time(12, 0))
        closest_index = min(
            range(len(times)),
            key=lambda index: abs(_parse_hour(times[index]) - target_datetime),
        )

        return MarineWeatherResult(
            provider=self.name,
            observed_at=_parse_hour(times[closest_index]),
            water_temp=_value_at(hourly, "sea_surface_temperature", closest_index),
            wave_height=_value_at(hourly, "wave_height", closest_index),
            wave_period=_value_at(hourly, "wave_period", closest_index),
            wave_direction=_value_at(hourly, "wave_direction", closest_index),
        )


def _parse_hour(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _value_at(hourly: dict, key: str, index: int):
    values = hourly.get(key) or []
    if index >= len(values):
        return None

    return values[index]
