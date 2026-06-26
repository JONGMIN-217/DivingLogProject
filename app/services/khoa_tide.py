from datetime import date
import json
from math import asin, cos, radians, sin, sqrt
from urllib.parse import urlencode
from urllib.request import urlopen

from app.config import settings


STATION_ENDPOINT = "https://www.khoa.go.kr/api/oceangrid/tideObsStation/search.do"
TIDE_ENDPOINT = "https://www.khoa.go.kr/api/oceangrid/tideObsPreTab/search.do"


def get_khoa_tide_summary(latitude: float, longitude: float):
    if not settings.khoa_service_key:
        raise RuntimeError("KHOA 서비스 키가 설정되지 않았습니다.")

    stations = _request_json(
        STATION_ENDPOINT,
        {
            "ServiceKey": settings.khoa_service_key,
            "ResultType": "json",
        },
    )
    station_rows = _result_rows(stations)
    candidates = []
    for row in station_rows:
        station_lat = _float_value(row.get("obs_lat") or row.get("lat"))
        station_lon = _float_value(row.get("obs_lon") or row.get("lon"))
        station_id = row.get("obs_post_id") or row.get("obs_code")
        if station_id and station_lat is not None and station_lon is not None:
            candidates.append(
                (
                    _distance_km(latitude, longitude, station_lat, station_lon),
                    station_id,
                    row.get("obs_post_name") or row.get("obs_name") or station_id,
                )
            )
    if not candidates:
        raise RuntimeError("KHOA 조위관측소 정보를 찾을 수 없습니다.")

    distance, station_id, station_name = min(candidates, key=lambda item: item[0])
    tide_payload = _request_json(
        TIDE_ENDPOINT,
        {
            "ServiceKey": settings.khoa_service_key,
            "ObsCode": station_id,
            "Date": date.today().strftime("%Y%m%d"),
            "ResultType": "json",
        },
    )
    tide_rows = _result_rows(tide_payload)
    return {
        "station_id": station_id,
        "station_name": station_name,
        "distance_km": round(distance, 1),
        "prediction_count": len(tide_rows),
        "date": date.today().isoformat(),
    }


def _request_json(endpoint: str, params: dict):
    url = f"{endpoint}?{urlencode(params)}"
    with urlopen(url, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("KHOA API 응답 형식이 올바르지 않습니다.")
    return payload


def _result_rows(payload: dict):
    result = payload.get("result") or payload
    if isinstance(result, dict):
        error = result.get("error") or result.get("error_msg")
        if error:
            raise RuntimeError(f"KHOA API 오류: {error}")
        data = result.get("data") or result.get("list") or []
    else:
        data = []
    return data if isinstance(data, list) else []


def _float_value(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float):
    radius_km = 6371.0
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)
    a = (
        sin(delta_lat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(delta_lon / 2) ** 2
    )
    return 2 * radius_km * asin(sqrt(a))
