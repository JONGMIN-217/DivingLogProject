from app.services.khoa_tide import get_khoa_tide_summary
from app.services.open_meteo_marine import get_current_marine_conditions
from app.services.open_meteo_weather import get_current_weather


def run_point_api_tests(point):
    tests = [
        (
            "Open-Meteo Marine",
            lambda: get_current_marine_conditions(point.latitude, point.longitude),
            _marine_summary,
        ),
        (
            "기상 API",
            lambda: get_current_weather(point.latitude, point.longitude),
            _weather_summary,
        ),
        (
            "KHOA API",
            lambda: get_khoa_tide_summary(point.latitude, point.longitude),
            _khoa_summary,
        ),
    ]
    results = []
    for name, loader, summarizer in tests:
        try:
            payload = loader()
            results.append(
                {
                    "name": name,
                    "status": "성공",
                    "ok": True,
                    "summary": summarizer(payload),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "name": name,
                    "status": "실패",
                    "ok": False,
                    "summary": _safe_error(exc),
                }
            )
    return results


def _marine_summary(data):
    return (
        f"파고 {value(data.get('wave_height'), 'm')}, "
        f"파주기 {value(data.get('wave_period'), '초')}, "
        f"해수면 수온 {value(data.get('sea_surface_temperature'), '℃')}"
    )


def _weather_summary(data):
    return (
        f"기온 {value(data.get('temperature'), '℃')}, "
        f"체감온도 {value(data.get('apparent_temperature'), '℃')}, "
        f"풍속 {value(data.get('wind_speed'), 'm/s')}"
    )


def _khoa_summary(data):
    return (
        f"최근접 관측소 {data.get('station_name')} "
        f"({data.get('distance_km')}km), 조석예보 {data.get('prediction_count')}건"
    )


def value(raw, unit):
    return "-" if raw is None else f"{raw}{unit}"


def _safe_error(exc):
    message = str(exc).strip()
    return message[:300] if message else "응답을 확인할 수 없습니다."
