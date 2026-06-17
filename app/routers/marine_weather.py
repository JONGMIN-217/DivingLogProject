from datetime import date, time

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import joinedload

from app.config import settings
from app.database import SessionLocal
from app.models import Country, MarineWeather, Region
from app.services.marine_weather import get_marine_weather, get_region_id_for_point

router = APIRouter()
templates = Jinja2Templates(directory=str(settings.templates_dir))


def weather_to_dict(weather: MarineWeather | None):
    if not weather:
        return None

    return {
        "id": weather.id,
        "date": weather.weather_date.isoformat(),
        "region": weather.region.name if weather.region else None,
        "country": weather.region.country.name if weather.region and weather.region.country else None,
        "water_temp": weather.water_temp,
        "wave_height": weather.wave_height,
        "wave_period": weather.wave_period,
        "wave_direction": weather.wave_direction,
        "wind_speed": weather.wind_speed,
        "wind_direction": weather.wind_direction,
        "tide_level": weather.tide_level,
        "tide_time": weather.tide_time,
        "current_strength": weather.current_strength,
        "memo": weather.memo,
    }


@router.get("/marine-weather")
def marine_weather_page(request: Request):
    db = SessionLocal()
    try:
        records = (
            db.query(MarineWeather)
            .options(joinedload(MarineWeather.region).joinedload(Region.country))
            .order_by(MarineWeather.weather_date.desc(), MarineWeather.id.desc())
            .all()
        )
        countries = db.query(Country).order_by(Country.name.asc()).all()
        return templates.TemplateResponse(
            "marine_weather.html",
            {
                "request": request,
                "records": records,
                "countries": countries,
                "error": request.query_params.get("error"),
                "message": request.query_params.get("message"),
            },
        )
    finally:
        db.close()


@router.post("/marine-weather")
def create_marine_weather(
    weather_date: str = Form(...),
    region_id: int = Form(...),
    water_temp: float | None = Form(None),
    wave_height: float | None = Form(None),
    wave_period: float | None = Form(None),
    wave_direction: float | None = Form(None),
    wind_speed: float | None = Form(None),
    wind_direction: str = Form(""),
    tide_level: str = Form(""),
    tide_time: str = Form(""),
    current_strength: str = Form(""),
    memo: str = Form(""),
):
    db = SessionLocal()
    try:
        parsed_date = date.fromisoformat(weather_date)
        weather = (
            db.query(MarineWeather)
            .filter(
                MarineWeather.weather_date == parsed_date,
                MarineWeather.region_id == region_id,
            )
            .first()
        )
        if not weather:
            weather = MarineWeather(weather_date=parsed_date, region_id=region_id)
            db.add(weather)

        weather.water_temp = water_temp
        weather.wave_height = wave_height
        weather.wave_period = wave_period
        weather.wave_direction = wave_direction
        weather.wind_speed = wind_speed
        weather.wind_direction = wind_direction.strip() or None
        weather.tide_level = tide_level.strip() or None
        weather.tide_time = tide_time.strip() or None
        weather.current_strength = current_strength.strip() or None
        weather.memo = memo.strip() or None
        db.commit()

        return RedirectResponse(url="/marine-weather?message=해양·기상정보를 저장했습니다.", status_code=303)
    finally:
        db.close()


@router.get("/api/marine-weather")
def lookup_marine_weather(weather_date: str, region_id: int | None = None, point_id: int | None = None, target_time: str | None = None):
    db = SessionLocal()
    try:
        parsed_date = date.fromisoformat(weather_date)
        parsed_time = time.fromisoformat(target_time) if target_time else None
        lookup_region_id = region_id or get_region_id_for_point(db, point_id)
        if not lookup_region_id:
            return {"weather": None}

        weather = get_marine_weather(db, parsed_date, lookup_region_id, point_id, parsed_time)
        return {"weather": weather_to_dict(weather)}
    finally:
        db.close()
