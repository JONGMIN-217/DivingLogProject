import logging
import math
import re
from datetime import datetime, time

from app.importers.common import ImportDive
from app.models import DiveLog, DivePoint
from app.services.import_batch_service import batch_item_to_dive
from app.services.import_save_service import normalize_import_dive


logger = logging.getLogger(__name__)
UNCONFIRMED_POINT_NAMES = {"미확정 포인트", "미확정"}


def apply_duplicate_detection_to_batch(db, batch: dict, user_id: int | None):
    summary = import_batch_duplicate_summary(db, batch, user_id)
    duplicate_by_index = {
        row["index"]: row
        for row in summary["duplicate_rows"]
    }
    for item in batch.get("dives", []):
        index = item.get("index", 0)
        duplicate = duplicate_by_index.get(index)
        if duplicate:
            item["selected"] = False
            item["duplicate_match"] = duplicate
        else:
            item.pop("duplicate_match", None)
    return summary


def import_batch_duplicate_summary(db, batch: dict, user_id: int | None):
    duplicate_rows = []
    duplicate_indexes = set()
    existing_logs = _existing_logs_for_user(db, user_id)

    for item in batch.get("dives", []):
        dive = batch_item_to_dive(item)
        point_id = item.get("selected_point_id") or item.get("suggested_point_id")
        duplicate = find_import_duplicate(existing_logs, dive, point_id)
        if not duplicate:
            continue

        index = item.get("index", 0)
        duplicate_indexes.add(index)
        existing = duplicate["log"]
        point = existing.dive_point
        row = {
            "index": index,
            "dive_date": _format_date(dive.dive_date),
            "entry_time": _format_time(dive.entry_time),
            "point_name": point.name if point else "-",
            "dive_time": dive.dive_time,
            "max_depth": dive.max_depth,
            "existing_dive_number": existing.dive_number,
            "existing_date": _format_date(existing.dive_date),
            "existing_entry_time": _format_time(existing.entry_time),
            "reason": duplicate["reason"],
            "matched_fields": duplicate["matched_fields"],
        }
        duplicate_rows.append(row)

    total_count = len(batch.get("dives", []))
    duplicate_count = len(duplicate_rows)
    return {
        "total_count": total_count,
        "duplicate_count": duplicate_count,
        "saveable_count": total_count - duplicate_count,
        "duplicate_rows": duplicate_rows,
        "duplicate_indexes": duplicate_indexes,
    }


def find_import_duplicate(existing_logs: list[DiveLog], dive: ImportDive, point_id=None):
    import_key = normalize_import_key(dive, point_id)
    if not import_key["dive_date"]:
        return None

    best_match = None
    for existing in existing_logs:
        existing_key = normalize_existing_key(existing)
        match = compare_duplicate_keys(import_key, existing_key)
        logger.debug(
            "Import 중복 비교: import=%s existing=%s matched=%s",
            import_key,
            existing_key,
            match["matched_fields"] if match else [],
        )
        if not match:
            continue
        if best_match is None or match["score"] > best_match["score"]:
            best_match = {
                **match,
                "log": existing,
            }

    return best_match


def normalize_import_key(dive: ImportDive, point_id=None):
    normalized = normalize_import_dive(dive)
    return {
        "source": _normalized_text(dive.source),
        "external_id": _normalized_text(dive.external_id),
        "dive_date": normalized["dive_date"],
        "entry_minute": _minute_of_day(normalized["entry_time"]),
        "exit_minute": _minute_of_day(normalized["exit_time"]),
        "dive_time": normalized["dive_time"],
        "max_depth": _round_depth(normalized["max_depth"]),
        "avg_depth": _round_depth(normalized["avg_depth"]),
        "point_id": point_id,
        "site_name": _normalized_text(dive.site_name),
        "latitude": dive.latitude,
        "longitude": dive.longitude,
    }


def normalize_existing_key(log: DiveLog):
    point = log.dive_point
    return {
        "source": "",
        "external_id": "",
        "dive_date": log.dive_date,
        "entry_minute": _minute_of_day(log.entry_time),
        "exit_minute": _minute_of_day(log.exit_time),
        "dive_time": int(round(log.dive_time)) if log.dive_time is not None else None,
        "max_depth": _round_depth(log.max_depth),
        "avg_depth": _round_depth(log.avg_depth),
        "point_id": log.dive_point_id,
        "site_name": _normalized_text(point.name if point else ""),
        "latitude": point.latitude if point else None,
        "longitude": point.longitude if point else None,
        "dive_number": log.dive_number,
    }


def compare_duplicate_keys(import_key: dict, existing_key: dict):
    matched = []
    score = 0

    if import_key["source"] and import_key["external_id"]:
        if import_key["source"] == existing_key["source"] and import_key["external_id"] == existing_key["external_id"]:
            return {
                "score": 100,
                "matched_fields": ["source", "external_id"],
                "reason": "source와 external_id가 일치",
            }

    if import_key["dive_date"] != existing_key["dive_date"]:
        return None
    matched.append("날짜")
    score += 2

    if not _within(import_key["entry_minute"], existing_key["entry_minute"], 2):
        return None
    matched.append("입수시간")
    score += 3

    if not _within(import_key["dive_time"], existing_key["dive_time"], 1):
        return None
    matched.append("다이브타임")
    score += 3

    if not _within(import_key["max_depth"], existing_key["max_depth"], 0.2):
        return None
    matched.append("최대수심")
    score += 3

    if _within(import_key["exit_minute"], existing_key["exit_minute"], 2):
        matched.append("출수시간")
        score += 1

    if _within(import_key["avg_depth"], existing_key["avg_depth"], 0.2):
        matched.append("평균수심")
        score += 1

    point_match = _point_matches(import_key, existing_key)
    if point_match:
        matched.append(point_match)
        score += 1

    return {
        "score": score,
        "matched_fields": matched,
        "reason": ", ".join(matched) + " 일치",
    }


def _existing_logs_for_user(db, user_id: int | None):
    query = (
        db.query(DiveLog)
        .outerjoin(DivePoint, DiveLog.dive_point_id == DivePoint.id)
    )
    if user_id is not None:
        query = query.filter(DiveLog.user_id == user_id)
    return query.all()


def _point_matches(import_key: dict, existing_key: dict):
    import_point_id = import_key.get("point_id")
    existing_point_id = existing_key.get("point_id")
    if import_point_id and existing_point_id and str(import_point_id) == str(existing_point_id):
        return "포인트"

    if _is_unconfirmed_site(import_key.get("site_name")) or _is_unconfirmed_site(existing_key.get("site_name")):
        return ""

    distance = _distance_km(
        import_key.get("latitude"),
        import_key.get("longitude"),
        existing_key.get("latitude"),
        existing_key.get("longitude"),
    )
    if distance is not None and distance <= 0.1:
        return "GPS 100m 이내"

    if import_key.get("site_name") and import_key.get("site_name") == existing_key.get("site_name"):
        return "포인트명"

    return ""


def _within(left, right, tolerance):
    if left is None or right is None:
        return False
    return abs(left - right) <= tolerance


def _minute_of_day(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    text = str(value).strip()
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _round_depth(value):
    if value is None:
        return None
    return round(float(value), 1)


def _normalized_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value).strip().lower())


def _is_unconfirmed_site(value):
    return value in {_normalized_text(name) for name in UNCONFIRMED_POINT_NAMES}


def _distance_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    radius_km = 6371.0
    dlat = math.radians(float(lat2) - float(lat1))
    dlon = math.radians(float(lon2) - float(lon1))
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(float(lat1)))
        * math.cos(math.radians(float(lat2)))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


def _format_date(value):
    if not value:
        return "-"
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _format_time(value):
    if not value:
        return "-"
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value)[:5]
