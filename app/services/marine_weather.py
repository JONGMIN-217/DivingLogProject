from datetime import date, time

from sqlalchemy.orm import joinedload

from app.models import Area, DivePoint, MarineWeather, Region
from app.services.marine_weather_providers import OpenMeteoMarineProvider


def get_region_id_for_point(db, point_id: int | None):
    if not point_id:
        return None

    point = (
        db.query(DivePoint)
        .join(Area, DivePoint.area_id == Area.id)
        .filter(DivePoint.id == point_id)
        .first()
    )
    if not point or not point.area:
        return None

    return point.area.region_id


def get_manual_marine_weather(db, weather_date: date, region_id: int):
    return (
        db.query(MarineWeather)
        .options(joinedload(MarineWeather.region).joinedload(Region.country))
        .filter(
            MarineWeather.weather_date == weather_date,
            MarineWeather.region_id == region_id,
        )
        .first()
    )


def fetch_external_marine_weather(db, weather_date: date, region_id: int, point_id: int | None = None, target_time: time | None = None):
    point = None
    if point_id:
        point = db.query(DivePoint).filter(DivePoint.id == point_id).first()

    if not point or point.latitude is None or point.longitude is None:
        return None

    providers = [
        OpenMeteoMarineProvider(),
        # KHOA, 기상청 Provider를 이 목록에 추가하면 됩니다.
    ]

    for provider in providers:
        try:
            result = provider.fetch(point.latitude, point.longitude, weather_date, target_time)
        except Exception:
            continue

        if not result:
            continue

        weather = (
            db.query(MarineWeather)
            .filter(
                MarineWeather.weather_date == weather_date,
                MarineWeather.region_id == region_id,
            )
            .first()
        )
        if not weather:
            weather = MarineWeather(weather_date=weather_date, region_id=region_id)
            db.add(weather)

        weather.water_temp = result.water_temp
        weather.wave_height = result.wave_height
        weather.wave_period = result.wave_period
        weather.wave_direction = result.wave_direction
        weather.memo = f"{result.provider} 자동 조회"
        db.commit()
        db.refresh(weather)
        return weather


def get_marine_weather(db, weather_date: date, region_id: int, point_id: int | None = None, target_time: time | None = None):
    manual_weather = get_manual_marine_weather(db, weather_date, region_id)
    if manual_weather:
        return manual_weather

    return fetch_external_marine_weather(db, weather_date, region_id, point_id, target_time)
