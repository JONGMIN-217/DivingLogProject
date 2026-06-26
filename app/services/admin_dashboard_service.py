import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, or_

from app.models import DiveLog, DivePoint, ImportRun, User


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def build_admin_dashboard(db, upload_dir: Path):
    import_runs = db.query(ImportRun).order_by(ImportRun.created_at.desc()).all()
    saved_total = sum(run.saved_count or 0 for run in import_runs)
    failed_total = sum(run.failed_count or 0 for run in import_runs)
    attempted_total = saved_total + failed_total

    if attempted_total:
        import_success_rate = round(saved_total / attempted_total * 100, 1)
        import_attempt_count = attempted_total
    else:
        import_success_rate = None
        import_attempt_count = 0

    stats = {
        "user_count": db.query(func.count(User.id)).scalar() or 0,
        "log_count": db.query(func.count(DiveLog.id)).scalar() or 0,
        "point_count": db.query(func.count(DivePoint.id)).scalar() or 0,
        "ghost_log_count": (
            db.query(func.count(DiveLog.id))
            .filter(or_(DiveLog.dive_time.is_(None), DiveLog.dive_time <= 0))
            .scalar()
            or 0
        ),
        "import_success_rate": import_success_rate,
        "import_attempt_count": import_attempt_count,
    }

    month_keys = _recent_month_keys(12)
    charts = {
        "labels": [_month_label(key) for key in month_keys],
        "log_counts": _monthly_counts(
            month_keys,
            db.query(DiveLog.dive_date).filter(DiveLog.dive_date.isnot(None)).all(),
        ),
        "user_counts": _monthly_counts(
            month_keys,
            db.query(User.created_at).filter(User.created_at.isnot(None)).all(),
        ),
        "point_counts": _monthly_counts(
            month_keys,
            db.query(DivePoint.created_at).filter(DivePoint.created_at.isnot(None)).all(),
        ),
    }

    return {
        "stats": stats,
        "charts": charts,
        "recent_uploads": recent_uploads(upload_dir),
        "recent_imports": import_runs[:5],
    }


def record_import_run(db, batch: dict, result: dict, user_id: int | None):
    dives = batch.get("dives", [])
    run = ImportRun(
        user_id=user_id,
        parser_name=batch.get("parser_name"),
        original_filename=batch.get("original_filename"),
        total_count=result.get("total_count", len(dives)) or len(dives),
        selected_count=result.get("selected_count", 0) or 0,
        saved_count=result.get("saved_count", 0) or 0,
        duplicate_count=result.get("duplicate_count", 0) or 0,
        failed_count=result.get("failed_count", 0) or 0,
    )
    db.add(run)
    return run


def recent_uploads(upload_dir: Path, limit: int = 8):
    items = []
    if not upload_dir.exists():
        return items

    for path in upload_dir.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in IMAGE_SUFFIXES | {".db", ".uddf", ".xml", ".csv", ".zip", ".json"}:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append(
            {
                "name": _upload_display_name(path),
                "category": _upload_category(path, upload_dir),
                "size": _format_file_size(stat.st_size),
                "uploaded_at": datetime.fromtimestamp(stat.st_mtime),
            }
        )

    items.sort(key=lambda item: item["uploaded_at"], reverse=True)
    return items[:limit]


def _recent_month_keys(count: int):
    now = datetime.now()
    keys = []
    year, month = now.year, now.month
    for _ in range(count):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    return list(reversed(keys))


def _monthly_counts(month_keys: list[str], rows):
    counts = {key: 0 for key in month_keys}
    for row in rows:
        value = row[0]
        if not value:
            continue
        key = value.strftime("%Y-%m")
        if key in counts:
            counts[key] += 1
    return [counts[key] for key in month_keys]


def _month_label(key: str):
    year, month = key.split("-")
    return f"{year[2:]}년 {int(month)}월"


def _upload_category(path: Path, upload_dir: Path):
    try:
        relative = path.relative_to(upload_dir)
    except ValueError:
        return "기타"
    first = relative.parts[0] if len(relative.parts) > 1 else ""
    return {
        "albums": "공유 사진첩",
        "trips": "투어 사진",
        "imports": "Import",
        "backups": "백업/복구",
    }.get(first, "로그 사진")


def _upload_display_name(path: Path):
    if path.name.startswith("import_batch_") and path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload.get("original_filename") or path.name
        except (OSError, json.JSONDecodeError):
            return path.name
    return path.name


def _format_file_size(size: int):
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
