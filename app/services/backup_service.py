import json
import zipfile
from datetime import date, datetime, time
from io import BytesIO
from pathlib import Path
from typing import Any

from sqlalchemy import inspect

from app.models import (
    Area,
    Country,
    DiveLog,
    DivePoint,
    DiveProfileSample,
    DiveTrip,
    Friend,
    Region,
    TripParticipant,
    TripPhoto,
    User,
    UserSettings,
)


BACKUP_VERSION = 1

MODEL_TABLES = {
    "users": User,
    "user_settings": UserSettings,
    "friends": Friend,
    "countries": Country,
    "regions": Region,
    "areas": Area,
    "dive_points": DivePoint,
    "dive_logs": DiveLog,
    "dive_profile_samples": DiveProfileSample,
    "dive_trips": DiveTrip,
    "trip_participants": TripParticipant,
    "trip_photos": TripPhoto,
}

RESTORE_ORDER = [
    "users",
    "user_settings",
    "friends",
    "countries",
    "regions",
    "areas",
    "dive_points",
    "dive_logs",
    "dive_profile_samples",
    "dive_trips",
    "trip_participants",
    "trip_photos",
]

DATE_COLUMNS = {
    "users": {"created_at": "datetime"},
    "dive_logs": {"dive_date": "date", "entry_time": "time", "exit_time": "time"},
    "dive_trips": {"start_date": "date", "end_date": "date"},
}


def _json_value(value: Any):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def _coerce_value(table_name: str, column_name: str, value: Any):
    if value in ("", None):
        return value

    column_type = DATE_COLUMNS.get(table_name, {}).get(column_name)
    try:
        if column_type == "datetime":
            return datetime.fromisoformat(value)
        if column_type == "date":
            return date.fromisoformat(value)
        if column_type == "time":
            return time.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return value


def _model_columns(model):
    return [column.key for column in inspect(model).mapper.column_attrs]


def _serialize_rows(rows, model):
    columns = _model_columns(model)
    return [
        {column: _json_value(getattr(row, column)) for column in columns}
        for row in rows
    ]


def _query_for_scope(db, model, table_name: str, user, include_all: bool):
    query = db.query(model)
    if include_all:
        return query

    if table_name == "users":
        return query.filter(User.id == user.id)
    if table_name == "user_settings":
        return query.filter(UserSettings.user_id == user.id)
    if table_name == "friends":
        return query.filter((Friend.requester_id == user.id) | (Friend.addressee_id == user.id))
    if table_name == "dive_logs":
        return query.filter(DiveLog.user_id == user.id)
    if table_name == "dive_profile_samples":
        return query.join(DiveLog, DiveLog.id == DiveProfileSample.dive_log_id).filter(DiveLog.user_id == user.id)
    if table_name == "dive_trips":
        return query.filter(DiveTrip.owner_id == user.id)
    if table_name == "trip_participants":
        return query.join(DiveTrip, DiveTrip.id == TripParticipant.trip_id).filter(DiveTrip.owner_id == user.id)
    if table_name == "trip_photos":
        return query.join(DiveTrip, DiveTrip.id == TripPhoto.trip_id).filter(DiveTrip.owner_id == user.id)
    if table_name == "dive_points":
        point_ids = [
            row[0]
            for row in db.query(DiveLog.dive_point_id)
            .filter(DiveLog.user_id == user.id, DiveLog.dive_point_id.isnot(None))
            .distinct()
            .all()
        ]
        return query.filter(DivePoint.id.in_(point_ids or [-1]))
    if table_name == "areas":
        area_ids = [
            row[0]
            for row in db.query(DivePoint.area_id)
            .join(DiveLog, DiveLog.dive_point_id == DivePoint.id)
            .filter(DiveLog.user_id == user.id, DivePoint.area_id.isnot(None))
            .distinct()
            .all()
        ]
        return query.filter(Area.id.in_(area_ids or [-1]))
    if table_name == "regions":
        region_ids = [
            row[0]
            for row in db.query(Area.region_id)
            .join(DivePoint, DivePoint.area_id == Area.id)
            .join(DiveLog, DiveLog.dive_point_id == DivePoint.id)
            .filter(DiveLog.user_id == user.id, Area.region_id.isnot(None))
            .distinct()
            .all()
        ]
        return query.filter(Region.id.in_(region_ids or [-1]))
    if table_name == "countries":
        country_ids = [
            row[0]
            for row in db.query(Region.country_id)
            .join(Area, Area.region_id == Region.id)
            .join(DivePoint, DivePoint.area_id == Area.id)
            .join(DiveLog, DiveLog.dive_point_id == DivePoint.id)
            .filter(DiveLog.user_id == user.id, Region.country_id.isnot(None))
            .distinct()
            .all()
        ]
        return query.filter(Country.id.in_(country_ids or [-1]))

    return query.filter(False)


def build_backup_payload(db, user, include_all: bool, import_upload_dir: Path):
    tables = {}
    for table_name, model in MODEL_TABLES.items():
        rows = _query_for_scope(db, model, table_name, user, include_all).order_by(model.id.asc()).all()
        tables[table_name] = _serialize_rows(rows, model)

    import_records = _build_import_records(tables["dive_logs"])
    import_batches = []
    if include_all and import_upload_dir.exists():
        for path in sorted(import_upload_dir.glob("import_batch_*.json")):
            try:
                import_batches.append({"name": path.name, "data": json.loads(path.read_text(encoding="utf-8"))})
            except (OSError, json.JSONDecodeError):
                import_batches.append({"name": path.name, "warning": "Import 배치 파일을 읽을 수 없습니다."})

    return {
        "metadata": {
            "version": BACKUP_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "scope": "all" if include_all else "user",
            "owner_user_id": user.id,
        },
        "tables": tables,
        "import_records": import_records,
        "import_batches": import_batches,
    }


def _build_import_records(dive_logs):
    records = []
    seen = set()
    for log in dive_logs:
        key = (
            log.get("import_source"),
            log.get("import_external_id"),
            log.get("import_source_file_hash"),
        )
        if key == (None, None, None) or key in seen:
            continue
        seen.add(key)
        records.append(
            {
                "import_source": key[0],
                "import_external_id": key[1],
                "import_source_file_hash": key[2],
            }
        )
    return records


def backup_json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _safe_upload_path(upload_dir: Path, relative_path: str | None):
    if not relative_path:
        return None
    candidate = (upload_dir / relative_path).resolve()
    try:
        candidate.relative_to(upload_dir.resolve())
    except ValueError:
        return None
    return candidate if candidate.exists() and candidate.is_file() else None


def _photo_paths(payload):
    paths = set()
    for log in payload.get("tables", {}).get("dive_logs", []):
        if log.get("image_path"):
            paths.add(log["image_path"])
    for photo in payload.get("tables", {}).get("trip_photos", []):
        if photo.get("image_path"):
            paths.add(photo["image_path"])
    return sorted(paths)


def payload_photo_paths(payload):
    return set(_photo_paths(payload))


def backup_zip_bytes(payload, upload_dir: Path, include_photos: bool):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("backup.json", backup_json_bytes(payload))
        if include_photos:
            for relative_path in _photo_paths(payload):
                source_path = _safe_upload_path(upload_dir, relative_path)
                if source_path:
                    zip_file.write(source_path, arcname=f"photos/{relative_path}")
    buffer.seek(0)
    return buffer.getvalue()


def parse_backup_file(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if suffix == ".zip":
        with zipfile.ZipFile(path) as zip_file:
            with zip_file.open("backup.json") as backup_file:
                return json.loads(backup_file.read().decode("utf-8"))
    raise ValueError("JSON 또는 ZIP 백업 파일만 복구할 수 있습니다.")


def restore_photos_from_zip(path: Path, upload_dir: Path, allowed_paths: set[str] | None = None):
    if path.suffix.lower() != ".zip":
        return 0

    restored_count = 0
    upload_root = upload_dir.resolve()
    with zipfile.ZipFile(path) as zip_file:
        for member in zip_file.infolist():
            if member.is_dir() or not member.filename.startswith("photos/"):
                continue
            relative_name = member.filename.removeprefix("photos/")
            if not relative_name:
                continue
            if allowed_paths is not None and relative_name not in allowed_paths:
                continue
            destination = (upload_root / relative_name).resolve()
            try:
                destination.relative_to(upload_root)
            except ValueError:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zip_file.open(member) as source, destination.open("wb") as target:
                target.write(source.read())
            restored_count += 1
    return restored_count


def restore_import_batches(payload, import_upload_dir: Path):
    restored_count = 0
    import_upload_dir.mkdir(parents=True, exist_ok=True)
    for item in payload.get("import_batches", []):
        name = Path(item.get("name") or "").name
        data = item.get("data")
        if not name.startswith("import_batch_") or not name.endswith(".json") or data is None:
            continue
        (import_upload_dir / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        restored_count += 1
    return restored_count


def filter_payload_for_restore(payload, user, restore_all: bool):
    if restore_all:
        return payload

    filtered = dict(payload)
    tables = {}
    source_tables = payload.get("tables", {})
    user_id = user.id
    log_ids = {row.get("id") for row in source_tables.get("dive_logs", []) if row.get("user_id") == user_id}
    trip_ids = {row.get("id") for row in source_tables.get("dive_trips", []) if row.get("owner_id") == user_id}
    point_ids = {row.get("dive_point_id") for row in source_tables.get("dive_logs", []) if row.get("user_id") == user_id and row.get("dive_point_id")}
    area_ids = {row.get("area_id") for row in source_tables.get("dive_points", []) if row.get("id") in point_ids and row.get("area_id")}
    region_ids = {row.get("region_id") for row in source_tables.get("areas", []) if row.get("id") in area_ids and row.get("region_id")}
    country_ids = {row.get("country_id") for row in source_tables.get("regions", []) if row.get("id") in region_ids and row.get("country_id")}

    for table_name, rows in source_tables.items():
        if table_name == "users":
            tables[table_name] = [row for row in rows if row.get("id") == user_id]
        elif table_name == "user_settings":
            tables[table_name] = [row for row in rows if row.get("user_id") == user_id]
        elif table_name == "friends":
            tables[table_name] = [row for row in rows if row.get("requester_id") == user_id or row.get("addressee_id") == user_id]
        elif table_name == "dive_logs":
            tables[table_name] = [row for row in rows if row.get("user_id") == user_id]
        elif table_name == "dive_profile_samples":
            tables[table_name] = [row for row in rows if row.get("dive_log_id") in log_ids]
        elif table_name == "dive_trips":
            tables[table_name] = [row for row in rows if row.get("owner_id") == user_id]
        elif table_name in {"trip_participants", "trip_photos"}:
            tables[table_name] = [row for row in rows if row.get("trip_id") in trip_ids]
        elif table_name == "dive_points":
            tables[table_name] = [row for row in rows if row.get("id") in point_ids]
        elif table_name == "areas":
            tables[table_name] = [row for row in rows if row.get("id") in area_ids]
        elif table_name == "regions":
            tables[table_name] = [row for row in rows if row.get("id") in region_ids]
        elif table_name == "countries":
            tables[table_name] = [row for row in rows if row.get("id") in country_ids]
        else:
            tables[table_name] = []

    filtered["tables"] = tables
    filtered["import_batches"] = [] if not restore_all else payload.get("import_batches", [])
    filtered["import_records"] = _build_import_records(tables.get("dive_logs", []))
    return filtered


def preview_restore(db, payload):
    summary = {}
    for table_name, rows in payload.get("tables", {}).items():
        model = MODEL_TABLES.get(table_name)
        if not model:
            continue
        existing_ids = {
            row[0]
            for row in db.query(model.id)
            .filter(model.id.in_([item.get("id") for item in rows if item.get("id") is not None] or [-1]))
            .all()
        }
        update_count = sum(1 for row in rows if row.get("id") in existing_ids)
        insert_count = len(rows) - update_count
        summary[table_name] = {
            "label": table_label(table_name),
            "total": len(rows),
            "insert": insert_count,
            "update": update_count,
        }
    return summary


def apply_restore(db, payload):
    summary = preview_restore(db, payload)
    for table_name in RESTORE_ORDER:
        model = MODEL_TABLES[table_name]
        columns = set(_model_columns(model))
        for row in payload.get("tables", {}).get(table_name, []):
            values = {
                key: _coerce_value(table_name, key, value)
                for key, value in row.items()
                if key in columns
            }
            existing = db.get(model, values.get("id")) if values.get("id") is not None else None
            if existing:
                for key, value in values.items():
                    setattr(existing, key, value)
            else:
                db.add(model(**values))
    db.flush()
    return summary


def table_label(table_name: str):
    return {
        "users": "사용자",
        "user_settings": "사용자 설정",
        "friends": "친구",
        "countries": "국가",
        "regions": "지역",
        "areas": "세부지역",
        "dive_points": "다이빙 포인트",
        "dive_logs": "다이빙 로그",
        "dive_profile_samples": "수심 프로파일",
        "dive_trips": "투어",
        "trip_participants": "투어 참가자",
        "trip_photos": "투어 사진",
    }.get(table_name, table_name)
