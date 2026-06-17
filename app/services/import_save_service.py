import re
import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import inspect

from app.importers.common import ImportDive
from app.models import Area, Country, DiveLog, DivePoint, Region
from app.services.dive_number_service import recalculate_dive_numbers
from app.services.import_batch_service import batch_item_to_dive


EMPTY_VALUES = {"", "확인 필요", "none", "None", "NULL", "null", "-"}


def save_import_items(db, items: list[dict | ImportDive], user_id: int | None):
    total_count = len(items)
    selected_items = [
        item
        for item in items
        if not isinstance(item, dict) or item.get("selected", True)
    ]
    result = {
        "result_id": uuid.uuid4().hex,
        "total_count": total_count,
        "selected_count": len(selected_items),
        "saved_count": 0,
        "duplicate_count": 0,
        "failed_count": 0,
        "total_log_count": 0,
        "failures": [],
    }

    for position, item in enumerate(selected_items, start=1):
        item_index = item.get("index", position - 1) if isinstance(item, dict) else position - 1
        dive = batch_item_to_dive(item) if isinstance(item, dict) else item
        selected_point_id = _selected_point_id(item, dive)
        try:
            normalized = normalize_import_dive(dive)
            if not normalized["dive_date"]:
                _add_failure(result, item_index, dive, selected_point_id, "날짜가 없어 저장에서 제외했습니다.")
                continue

            point = _resolve_import_point(db, item, dive, selected_point_id)
            if not point:
                point = _get_or_create_unconfirmed_point(db)

            from app.services.import_duplicate_service import find_import_duplicate

            existing_logs = _existing_logs_for_user(db, user_id)
            duplicate_log = find_import_duplicate(existing_logs, dive, point.id)
            user_included_preview_duplicate = isinstance(item, dict) and bool(item.get("duplicate_match"))
            if duplicate_log and not user_included_preview_duplicate:
                result["duplicate_count"] += 1
                continue

            with db.begin_nested():
                db.add(
                    DiveLog(
                        user_id=user_id,
                        dive_point_id=point.id,
                        dive_date=normalized["dive_date"],
                        entry_time=normalized["entry_time"],
                        exit_time=normalized["exit_time"],
                        max_depth=normalized["max_depth"],
                        avg_depth=normalized["avg_depth"],
                        dive_time=normalized["dive_time"],
                        water_temp=normalized["water_temp"],
                        visibility=normalized["visibility"],
                        start_pressure=normalized["start_pressure"],
                        end_pressure=normalized["end_pressure"],
                        buddy=normalized["buddy"],
                        note=normalized["note"],
                    )
                )
                db.flush()
            result["saved_count"] += 1
        except Exception as exc:
            _add_failure(result, item_index, dive, selected_point_id, _human_error(exc))

    result["failed_count"] = len(result["failures"])
    result["total_log_count"] = recalculate_dive_numbers(db, user_id)
    return result


def normalize_import_dive(dive: ImportDive):
    entry_time = normalize_time(dive.entry_time, DiveLog.entry_time)
    exit_time = normalize_time(dive.exit_time, DiveLog.exit_time)
    dive_time = normalize_minutes(dive.dive_time)
    entry_time, exit_time, dive_time = _apply_auto_time_calculation(entry_time, exit_time, dive_time)

    return {
        "dive_date": normalize_date(dive.dive_date),
        "entry_time": entry_time,
        "exit_time": exit_time,
        "dive_time": dive_time,
        "max_depth": normalize_float(dive.max_depth),
        "avg_depth": normalize_float(dive.avg_depth),
        "water_temp": normalize_temperature(dive.water_temp),
        "visibility": normalize_int(getattr(dive, "visibility", None)),
        "start_pressure": normalize_int(dive.start_pressure),
        "end_pressure": normalize_int(dive.end_pressure),
        "buddy": _safe_text(dive.buddy),
        "note": _safe_text(dive.note),
    }


def normalize_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if text in EMPTY_VALUES:
        return None
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(candidate[:19]).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def normalize_time(value, column):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value.time().replace(microsecond=0)
    elif isinstance(value, time):
        parsed = value.replace(microsecond=0)
    else:
        text = str(value).strip()
        if text in EMPTY_VALUES:
            return None
        parsed = _parse_time_text(text)
        if parsed is None:
            return None

    try:
        column_type = inspect(column).type
        if column_type.python_type is str:
            return parsed.strftime("%H:%M")
    except (AttributeError, NotImplementedError):
        pass
    return parsed


def normalize_minutes(value):
    number = _number_from_value(value)
    if number is None:
        return None
    if number > 300:
        number = number / 60
    return int(round(number))


def _apply_auto_time_calculation(entry_time, exit_time, dive_time):
    if exit_time is None and entry_time is not None and dive_time is not None:
        base = datetime.combine(datetime.today().date(), entry_time)
        exit_time = (base + timedelta(minutes=dive_time)).time().replace(microsecond=0)

    if dive_time is None and entry_time is not None and exit_time is not None:
        entry_dt = datetime.combine(datetime.today().date(), entry_time)
        exit_dt = datetime.combine(datetime.today().date(), exit_time)
        if exit_dt < entry_dt:
            exit_dt += timedelta(days=1)
        dive_time = max(int(round((exit_dt - entry_dt).total_seconds() / 60)), 0)

    return entry_time, exit_time, dive_time


def normalize_float(value):
    number = _number_from_value(value)
    return float(number) if number is not None else None


def normalize_temperature(value):
    number = normalize_float(value)
    if number is None:
        return None
    if number >= 200:
        return round(number - 273.15, 2)
    return number


def normalize_int(value):
    number = _number_from_value(value)
    return int(round(number)) if number is not None else None


def _parse_time_text(text: str):
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(candidate[:19]).time().replace(microsecond=0)
        except ValueError:
            continue
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text[:8], fmt).time()
        except ValueError:
            continue
    return None


def _number_from_value(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text in EMPTY_VALUES:
        return None
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _safe_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _selected_point_id(item, dive: ImportDive):
    if isinstance(item, dict):
        return item.get("selected_point_id") or item.get("suggested_point_id")
    return getattr(dive, "selected_point_id", None) or dive.suggested_point_id


def _resolve_import_point(db, item, dive: ImportDive, selected_point_id):
    point = db.query(DivePoint).filter(DivePoint.id == selected_point_id).first() if selected_point_id else None
    if point:
        return point

    if dive.latitude is None or dive.longitude is None:
        return None

    if not isinstance(item, dict):
        return None

    country_name = (item.get("candidate_country") or "").strip()
    region_name = (item.get("candidate_region") or "").strip()
    area_name = (item.get("candidate_area") or "").strip()
    point_name = (item.get("candidate_point_name") or "").strip()
    if not all([country_name, region_name, area_name, point_name]):
        return None
    return _get_or_create_point(db, country_name, region_name, area_name, point_name, dive.latitude, dive.longitude, dive.note)


def _get_or_create_unconfirmed_point(db):
    return _get_or_create_point(db, "미확정", "미확정", "미확정", "미확정 포인트", None, None, "Import 중 포인트가 확정되지 않은 로그용 기본 포인트")


def _get_or_create_point(db, country_name, region_name, area_name, point_name, latitude, longitude, memo):
    country = db.query(Country).filter(Country.name == country_name).first()
    if not country:
        country = Country(name=country_name)
        db.add(country)
        db.flush()

    region = db.query(Region).filter(Region.name == region_name).first()
    if not region:
        region = Region(name=region_name, country_id=country.id)
        db.add(region)
        db.flush()

    area = db.query(Area).filter(Area.name == area_name, Area.region_id == region.id).first()
    if not area:
        area = Area(name=area_name, region_id=region.id)
        db.add(area)
        db.flush()

    point = db.query(DivePoint).filter(DivePoint.name == point_name, DivePoint.area_id == area.id).first()
    if not point:
        point = DivePoint(name=point_name, area_id=area.id, latitude=latitude, longitude=longitude, memo=memo)
        db.add(point)
        db.flush()
    return point


def _find_duplicate_dive_log(db, user_id, dive_date, entry_time, exit_time, dive_time, max_depth, avg_depth, dive_point_id):
    return (
        db.query(DiveLog)
        .filter(_nullable_filter(DiveLog.user_id, user_id))
        .filter(DiveLog.dive_date == dive_date)
        .filter(_nullable_filter(DiveLog.entry_time, entry_time))
        .filter(_nullable_filter(DiveLog.exit_time, exit_time))
        .filter(_nullable_filter(DiveLog.dive_time, dive_time))
        .filter(_nullable_filter(DiveLog.max_depth, max_depth))
        .filter(_nullable_filter(DiveLog.avg_depth, avg_depth))
        .filter(DiveLog.dive_point_id == dive_point_id)
        .first()
    )


def _existing_logs_for_user(db, user_id: int | None):
    query = db.query(DiveLog)
    if user_id is not None:
        query = query.filter(DiveLog.user_id == user_id)
    return query.all()


def _nullable_filter(column, value):
    return column.is_(None) if value is None else column == value


def _add_failure(result, item_index, dive: ImportDive, point_id, reason: str):
    result["failures"].append(
        {
            "log_number": int(item_index) + 1,
            "date": _format_date(dive.dive_date),
            "entry_time": _format_time(dive.entry_time),
            "point": _failure_point_label(dive, point_id),
            "reason": reason,
        }
    )


def _format_date(value):
    parsed = normalize_date(value)
    return parsed.isoformat() if parsed else "-"


def _format_time(value):
    parsed = normalize_time(value, DiveLog.entry_time)
    return parsed.strftime("%H:%M") if isinstance(parsed, time) else "-"


def _failure_point_label(dive: ImportDive, point_id):
    if point_id:
        return f"선택 포인트 ID {point_id}"
    return dive.site_name or dive.candidate_point_name or "미확정 포인트"


def _human_error(exc: Exception):
    message = str(exc).strip()
    if not message:
        return "알 수 없는 저장 오류가 발생했습니다."
    return message
