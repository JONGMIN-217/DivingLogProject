from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy import and_, func, inspect, or_, text
from sqlalchemy.orm import joinedload
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from datetime import date, datetime
import hashlib

from app.auth import hash_password, verify_password
from app.database import Base, SessionLocal, engine
from app.models import (
    Area,
    Country,
    DivePoint,
    DiveLog,
    DiveTrip,
    Friend,
    Region,
    TripParticipant,
    TripPhoto,
    User,
)

from app.routers import regions
from app.routers import areas
from app.routers import countries
from app.routers import divepoints
from app.routers import divelogs
from app.routers import marine_weather
from app.import_parsers import parse_import_file
from app.import_parsers.base import DivePointSuggestion, ImportPreview, UnsupportedImportFormat
from app.services.import_batch_service import (
    batch_item_to_dive,
    create_import_batch,
    dive_to_batch_item,
    load_import_batch,
    save_import_batch,
)
from app.services.dive_number_service import recalculate_all_dive_numbers, recalculate_dive_numbers
from app.services.import_save_service import save_import_items
from app.services.import_duplicate_service import (
    apply_duplicate_detection_to_batch,
    import_batch_duplicate_summary as build_import_batch_duplicate_summary,
)

from fastapi import UploadFile, File
import uuid
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from starlette.responses import PlainTextResponse
import csv
from urllib.parse import urlencode

from app.config import settings

app = FastAPI()
app.state.settings = settings

UPLOAD_DIR = settings.upload_dir
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
IMPORT_UPLOAD_DIR = UPLOAD_DIR / "imports"
IMPORT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
TRIP_UPLOAD_DIR = UPLOAD_DIR / "trips"
TRIP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_EXTENSIONS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
ALLOWED_IMPORT_EXTENSIONS = {".csv", ".xml", ".uddf", ".db"}
MAX_IMAGE_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_IMPORT_UPLOAD_SIZE = settings.max_import_file_size_mb * 1024 * 1024
IMPORT_PREVIEW_PAGE_SIZE = 20

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
settings.static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

templates = Jinja2Templates(directory=str(settings.templates_dir))

app.include_router(regions.router)
app.include_router(areas.router)
app.include_router(countries.router)
app.include_router(divepoints.router)
app.include_router(divelogs.router)
app.include_router(marine_weather.router)


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")
            expected_origin = f"{request.url.scheme}://{request.url.netloc}"

            if origin and origin != expected_origin:
                return PlainTextResponse("잘못된 요청입니다.", status_code=403)

            if not origin and referer and not referer.startswith(f"{expected_origin}/"):
                return PlainTextResponse("잘못된 요청입니다.", status_code=403)

        return await call_next(request)


class LoginRequiredMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        public_paths = (
            "/login",
            "/register",
            "/static",
            "/favicon.ico",
        )
        if request.url.path.startswith(public_paths):
            return await call_next(request)

        user_id = request.session.get("user_id")
        if not user_id:
            next_url = request.url.path
            return RedirectResponse(url=f"/login?next={next_url}", status_code=303)

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                request.session.clear()
                next_url = request.url.path
                return RedirectResponse(url=f"/login?next={next_url}", status_code=303)

            request.session["username"] = user.username
            request.session["is_admin"] = user.is_admin
        finally:
            db.close()

        return await call_next(request)


app.add_middleware(LoginRequiredMiddleware)
app.add_middleware(CsrfOriginMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=False,
)


def parse_time(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%H:%M").time()


def parse_int_filter(value: str):
    if not value:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_date_filter(value: str):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_optional_time(value: str):
    try:
        return parse_time(value)
    except ValueError:
        return None


def nullable_filter(column, value):
    if value is None:
        return column.is_(None)

    return column == value


def monthly_dive_date_expression(db):
    dialect_name = db.bind.dialect.name
    if dialect_name == "postgresql":
        return func.to_char(DiveLog.dive_date, "YYYY-MM")

    return func.strftime("%Y-%m", DiveLog.dive_date)


def get_current_user(request: Request, db):
    user_id = request.session.get("user_id")
    if not user_id:
        return None

    return db.query(User).filter(User.id == user_id).first()


def admin_required_redirect(request: Request, db):
    current_user = get_current_user(request, db)
    if not current_user:
        return None, login_required_redirect(request)

    if not current_user.is_admin:
        return current_user, RedirectResponse(url="/", status_code=303)

    return current_user, None


def login_required_redirect(request: Request):
    next_url = request.url.path
    return RedirectResponse(url=f"/login?next={next_url}", status_code=303)


def visible_log_condition(user):
    if user:
        return or_(DiveLog.user_id == user.id, DiveLog.user_id.is_(None))

    return DiveLog.user_id.is_(None)


def apply_visible_logs(query, user):
    return query.filter(visible_log_condition(user))


def owned_log_condition(user):
    return DiveLog.user_id == user.id


def editable_log_query(db, current_user):
    query = db.query(DiveLog)
    if current_user and current_user.is_admin:
        return query
    return query.filter(owned_log_condition(current_user))


def recalculate_after_log_changes(db, current_user, affected_user_ids: set[int | None] | None = None):
    if current_user and current_user.is_admin and affected_user_ids is None:
        return recalculate_all_dive_numbers(db)

    if affected_user_ids:
        total = 0
        for user_id in affected_user_ids:
            if user_id is None:
                total = recalculate_all_dive_numbers(db)
                break
            total += recalculate_dive_numbers(db, user_id)
        return total

    return recalculate_dive_numbers(db, current_user.id if current_user else None)


def friendship_between(db, user_id: int, other_user_id: int):
    return (
        db.query(Friend)
        .filter(
            or_(
                and_(
                    Friend.requester_id == user_id,
                    Friend.addressee_id == other_user_id,
                ),
                and_(
                    Friend.requester_id == other_user_id,
                    Friend.addressee_id == user_id,
                ),
            )
        )
        .first()
    )


def get_accepted_friends(db, user_id: int):
    friendships = (
        db.query(Friend)
        .filter(
            Friend.status == "accepted",
            or_(
                Friend.requester_id == user_id,
                Friend.addressee_id == user_id,
            ),
        )
        .all()
    )

    friend_ids = [
        friendship.addressee_id
        if friendship.requester_id == user_id
        else friendship.requester_id
        for friendship in friendships
    ]
    if not friend_ids:
        return []

    return db.query(User).filter(User.id.in_(friend_ids)).order_by(User.username.asc()).all()


def get_accepted_friend_ids(db, user_id: int):
    return [friend.id for friend in get_accepted_friends(db, user_id)]


def is_trip_participant(db, trip_id: int, user_id: int):
    return (
        db.query(TripParticipant)
        .filter(
            TripParticipant.trip_id == trip_id,
            TripParticipant.user_id == user_id,
        )
        .first()
        is not None
    )


def format_file_size(size_bytes: int):
    size_mb = size_bytes / (1024 * 1024)
    if size_mb.is_integer():
        return f"{int(size_mb)}MB"

    return f"{size_mb:.1f}MB"


def validate_upload(upload_file: UploadFile, allowed_extensions: set[str], allowed_content_types: set[str], max_size: int):
    original_filename = Path(upload_file.filename or "").name
    suffix = Path(original_filename).suffix.lower()
    if suffix not in allowed_extensions:
        return None, "허용되지 않는 파일 형식입니다."

    if upload_file.content_type and upload_file.content_type not in allowed_content_types:
        return None, "허용되지 않는 파일 형식입니다."

    upload_size = getattr(upload_file, "size", None)
    if upload_size is not None and upload_size > max_size:
        return None, f"파일 크기는 {format_file_size(max_size)} 이하만 허용됩니다."

    return suffix, None


def save_upload_file(upload_file: UploadFile, destination: Path, max_size: int):
    bytes_written = 0
    with open(destination, "wb") as buffer:
        while True:
            chunk = upload_file.file.read(1024 * 1024)
            if not chunk:
                break

            bytes_written += len(chunk)
            if bytes_written > max_size:
                destination.unlink(missing_ok=True)
                return f"파일 크기는 {format_file_size(max_size)} 이하만 허용됩니다."

            buffer.write(chunk)

    return None


def file_sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_float_value(value: str | None):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    value = value.strip()
    if value == "":
        return None

    try:
        return float(value)
    except ValueError:
        return None


def valid_coordinate(latitude: float | None, longitude: float | None):
    return (
        latitude is not None
        and longitude is not None
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


def get_or_create_dive_point(
    db,
    country_name: str,
    region_name: str,
    area_name: str,
    point_name: str,
    latitude: float,
    longitude: float,
    memo: str | None = None,
):
    country_name = country_name.strip()
    region_name = region_name.strip()
    area_name = area_name.strip()
    point_name = point_name.strip()

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
    elif region.country_id is None:
        region.country_id = country.id

    area = (
        db.query(Area)
        .filter(
            Area.region_id == region.id,
            Area.name == area_name,
        )
        .first()
    )
    if not area:
        area = Area(name=area_name, region_id=region.id)
        db.add(area)
        db.flush()

    point = (
        db.query(DivePoint)
        .filter(
            DivePoint.area_id == area.id,
            DivePoint.name == point_name,
        )
        .first()
    )
    if point:
        point.latitude = latitude
        point.longitude = longitude
        point.memo = memo.strip() if memo else None
        return point, False

    point = DivePoint(
        name=point_name,
        area_id=area.id,
        latitude=latitude,
        longitude=longitude,
        memo=memo.strip() if memo else None,
    )
    db.add(point)
    db.flush()
    return point, True


def csv_value(row, *names):
    for name in names:
        value = row.get(name)
        if value is not None:
            return value.strip()
    return ""


def admin_divepoints_url(**params):
    if not params:
        return "/admin/divepoints"

    return f"/admin/divepoints?{urlencode(params)}"


DIVEPOINT_GPS_CSV_COLUMNS = ("country", "region", "area", "point_name", "latitude", "longitude", "memo")


def parse_divepoint_gps_csv(path: Path):
    valid_rows: list[dict[str, str]] = []
    error_rows: list[dict[str, str]] = []

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames or []
        missing_columns = [
            column for column in DIVEPOINT_GPS_CSV_COLUMNS
            if column not in fieldnames
        ]
        if missing_columns:
            return valid_rows, [
                {
                    "row_number": "-",
                    "point_name": "",
                    "reason": f"필수 컬럼이 없습니다: {', '.join(missing_columns)}",
                }
            ]

        for row_number, row in enumerate(reader, start=2):
            parsed = {
                column: (row.get(column) or "").strip()
                for column in DIVEPOINT_GPS_CSV_COLUMNS
            }
            latitude_value = parse_float_value(parsed["latitude"])
            longitude_value = parse_float_value(parsed["longitude"])

            if not parsed["latitude"] or not parsed["longitude"]:
                error_rows.append(
                    {
                        "row_number": str(row_number),
                        "point_name": parsed["point_name"],
                        "reason": "위도 또는 경도 값이 없습니다.",
                    }
                )
                continue

            if not valid_coordinate(latitude_value, longitude_value):
                error_rows.append(
                    {
                        "row_number": str(row_number),
                        "point_name": parsed["point_name"],
                        "reason": "위도 또는 경도 값이 올바르지 않습니다.",
                    }
                )
                continue

            if not all([parsed["country"], parsed["region"], parsed["area"], parsed["point_name"]]):
                error_rows.append(
                    {
                        "row_number": str(row_number),
                        "point_name": parsed["point_name"],
                        "reason": "국가, 지역, 세부지역, 포인트명은 필수입니다.",
                    }
                )
                continue

            parsed["latitude"] = str(latitude_value)
            parsed["longitude"] = str(longitude_value)
            valid_rows.append(parsed)

    return valid_rows, error_rows


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float):
    from math import asin, cos, radians, sin, sqrt

    radius_km = 6371.0
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)
    a = (
        sin(delta_lat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(delta_lon / 2) ** 2
    )
    return 2 * radius_km * asin(sqrt(a))


def import_raw_value(raw: dict[str, str], *names: str):
    normalized = {
        key.lower().replace(" ", "").replace("_", "").replace("-", "").replace(".", ""): value
        for key, value in raw.items()
    }
    for name in names:
        value = normalized.get(name.lower().replace(" ", "").replace("_", "").replace("-", "").replace(".", ""))
        if value:
            return value.strip()
    return ""


def fill_new_point_candidate(dive):
    dive.is_new_point_candidate = True
    dive.candidate_country = import_raw_value(dive.raw, "country", "country_name", "국가")
    dive.candidate_region = import_raw_value(dive.raw, "region", "region_name", "지역")
    dive.candidate_area = import_raw_value(dive.raw, "area", "area_name", "site_area", "세부지역")
    dive.candidate_point_name = (
        import_raw_value(dive.raw, "point_name", "point", "site", "site_name", "divesite", "name", "포인트명")
        or dive.source_label
        or "새 포인트"
    )


def attach_point_suggestions(db, preview):
    points = (
        db.query(DivePoint)
        .filter(DivePoint.latitude.isnot(None), DivePoint.longitude.isnot(None))
        .all()
    )
    if not points:
        preview.messages.append("좌표가 등록된 다이빙 포인트가 없어 자동 추천을 건너뛰었습니다.")
        for dive in preview.dives:
            if dive.latitude is not None and dive.longitude is not None:
                fill_new_point_candidate(dive)
        return

    for dive in preview.dives:
        if dive.latitude is None or dive.longitude is None:
            continue

        nearest = min(
            points,
            key=lambda point: distance_km(dive.latitude, dive.longitude, point.latitude, point.longitude),
        )
        distance = distance_km(dive.latitude, dive.longitude, nearest.latitude, nearest.longitude)
        if distance <= 0.2:
            dive.suggested_point_id = nearest.id
            dive.suggested_point = DivePointSuggestion(
                point_id=nearest.id,
                point_name=nearest.name,
                distance_km=round(distance, 2),
            )
        else:
            fill_new_point_candidate(dive)


def find_duplicate_dive_log(
    db,
    user_id: int | None,
    dive_date,
    entry_time,
    exit_time,
    dive_time,
    max_depth,
    avg_depth,
    dive_point_id,
):
    if not dive_date or not dive_point_id:
        return None

    return (
        db.query(DiveLog)
        .filter(nullable_filter(DiveLog.user_id, user_id))
        .filter(DiveLog.dive_date == dive_date)
        .filter(nullable_filter(DiveLog.entry_time, entry_time))
        .filter(nullable_filter(DiveLog.exit_time, exit_time))
        .filter(nullable_filter(DiveLog.dive_time, dive_time))
        .filter(nullable_filter(DiveLog.max_depth, max_depth))
        .filter(nullable_filter(DiveLog.avg_depth, avg_depth))
        .filter(DiveLog.dive_point_id == dive_point_id)
        .first()
    )


def import_duplicate_summary(db, preview, user_id: int | None):
    duplicate_rows = []
    duplicate_indexes = set()

    for index, dive in enumerate(preview.dives):
        duplicate_log = find_duplicate_dive_log(
            db,
            user_id,
            dive.dive_date,
            dive.entry_time,
            dive.exit_time,
            dive.dive_time,
            dive.max_depth,
            dive.avg_depth,
            dive.suggested_point_id,
        )
        if not duplicate_log:
            continue

        duplicate_indexes.add(index)
        point = duplicate_log.dive_point
        duplicate_rows.append(
            {
                "index": index,
                "dive_date": dive.dive_date,
                "entry_time": dive.entry_time,
                "point_name": point.name if point else "-",
                "dive_time": dive.dive_time,
                "max_depth": dive.max_depth,
            }
        )

    total_count = len(preview.dives)
    duplicate_count = len(duplicate_rows)
    return {
        "total_count": total_count,
        "duplicate_count": duplicate_count,
        "saveable_count": total_count - duplicate_count,
        "duplicate_rows": duplicate_rows,
        "duplicate_indexes": duplicate_indexes,
    }


def import_batch_duplicate_summary(db, batch: dict, user_id: int | None):
    return build_import_batch_duplicate_summary(db, batch, user_id)


def build_import_batch_preview(batch: dict, page: int):
    total_count = len(batch.get("dives", []))
    total_pages = max((total_count + IMPORT_PREVIEW_PAGE_SIZE - 1) // IMPORT_PREVIEW_PAGE_SIZE, 1)
    page = min(max(page, 1), total_pages)
    start = (page - 1) * IMPORT_PREVIEW_PAGE_SIZE
    end = start + IMPORT_PREVIEW_PAGE_SIZE
    page_items = batch.get("dives", [])[start:end]
    dives = [batch_item_to_dive(item) for item in page_items]

    preview = ImportPreview(
        parser_name=batch["parser_name"],
        source_path=Path(batch["source_path"]),
        original_filename=batch["original_filename"],
        columns=batch["columns"],
        rows=[dive.preview_row() for dive in dives],
        messages=batch.get("messages", []),
        dives=dives,
    )
    pagination = {
        "page": page,
        "page_size": IMPORT_PREVIEW_PAGE_SIZE,
        "total_count": total_count,
        "total_pages": total_pages,
        "start_number": start + 1 if total_count else 0,
        "end_number": min(end, total_count),
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_page": page - 1,
        "next_page": page + 1,
        "selected_count": sum(1 for item in batch.get("dives", []) if item.get("selected", True)),
    }
    return preview, pagination


def apply_import_batch_page_form(
    batch: dict,
    row_indexes: list[str],
    selected_import_indexes: list[str],
    dive_dates: list[str],
    entry_times: list[str],
    exit_times: list[str],
    max_depths: list[str],
    avg_depths: list[str],
    dive_times: list[str],
    water_temps: list[str],
    selected_point_ids: list[str],
    candidate_country_names: list[str],
    candidate_region_names: list[str],
    candidate_area_names: list[str],
    candidate_point_names: list[str],
):
    selected_set = {int(index) for index in selected_import_indexes if str(index).isdigit()}
    items = batch.get("dives", [])

    for position, row_index_text in enumerate(row_indexes):
        if not str(row_index_text).isdigit():
            continue

        row_index = int(row_index_text)
        if row_index < 0 or row_index >= len(items):
            continue

        item = items[row_index]
        item["selected"] = row_index in selected_set
        if position < len(dive_dates):
            item["dive_date"] = dive_dates[position]
        if position < len(entry_times):
            item["entry_time"] = entry_times[position]
        if position < len(exit_times):
            item["exit_time"] = exit_times[position]
        if position < len(max_depths):
            item["max_depth"] = parse_float_value(max_depths[position])
        if position < len(avg_depths):
            item["avg_depth"] = parse_float_value(avg_depths[position])
        if position < len(dive_times):
            item["dive_time"] = parse_int_filter(dive_times[position])
        if position < len(water_temps):
            item["water_temp"] = parse_float_value(water_temps[position])
        if position < len(selected_point_ids):
            item["selected_point_id"] = parse_int_filter(selected_point_ids[position])
        if position < len(candidate_country_names):
            item["candidate_country"] = candidate_country_names[position].strip()
        if position < len(candidate_region_names):
            item["candidate_region"] = candidate_region_names[position].strip()
        if position < len(candidate_area_names):
            item["candidate_area"] = candidate_area_names[position].strip()
        if position < len(candidate_point_names):
            item["candidate_point_name"] = candidate_point_names[position].strip()


def select_import_batch_page(batch: dict, row_indexes: list[str]):
    row_index_set = {int(index) for index in row_indexes if str(index).isdigit()}
    for item in batch.get("dives", []):
        if item.get("index") in row_index_set:
            item["selected"] = True


def set_import_batch_selection(batch: dict, selected: bool):
    for item in batch.get("dives", []):
        item["selected"] = selected


def save_import_batch_logs(db, batch: dict, user_id: int | None):
    return save_import_items(db, batch.get("dives", []), user_id)


def import_source_path_from_form(source_path: str):
    path = Path(source_path).resolve()
    upload_root = IMPORT_UPLOAD_DIR.resolve()
    if path.parent != upload_root or not path.exists():
        return None

    return path


def ensure_dive_log_time_columns():
    inspector = inspect(engine)
    if not inspector.has_table("dive_logs"):
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("dive_logs")
    }
    required_columns = {
        "entry_time": "TIME",
        "exit_time": "TIME",
        "user_id": "INTEGER",
        "buddy_user_id": "INTEGER",
        "dive_number": "INTEGER",
        "import_source": "VARCHAR",
        "import_external_id": "VARCHAR",
        "import_source_file_hash": "VARCHAR",
    }

    with engine.begin() as connection:
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE dive_logs ADD COLUMN {column_name} {column_type}")
                )


def ensure_user_columns():
    inspector = inspect(engine)
    if not inspector.has_table("users"):
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("users")
    }
    required_columns = {
        "is_admin": "BOOLEAN NOT NULL DEFAULT 0",
    }

    with engine.begin() as connection:
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
                )


def ensure_region_country_columns():
    inspector = inspect(engine)
    if not inspector.has_table("regions"):
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("regions")
    }

    with engine.begin() as connection:
        if "country_id" not in existing_columns:
            connection.execute(text("ALTER TABLE regions ADD COLUMN country_id INTEGER"))


def ensure_dive_point_columns():
    inspector = inspect(engine)
    if not inspector.has_table("dive_points"):
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("dive_points")
    }

    with engine.begin() as connection:
        if "memo" not in existing_columns:
            connection.execute(text("ALTER TABLE dive_points ADD COLUMN memo VARCHAR"))


def ensure_marine_weather_columns():
    inspector = inspect(engine)
    if not inspector.has_table("marine_weather"):
        return

    existing_columns = {
        column["name"] for column in inspector.get_columns("marine_weather")
    }
    required_columns = {
        "wave_period": "FLOAT",
        "wave_direction": "FLOAT",
    }

    with engine.begin() as connection:
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE marine_weather ADD COLUMN {column_name} {column_type}")
                )


def ensure_default_country():
    db = SessionLocal()
    try:
        default_country = db.query(Country).filter(Country.name == "대한민국").first()
        if not default_country:
            default_country = Country(name="대한민국")
            db.add(default_country)
            db.commit()
            db.refresh(default_country)

        regions_without_country = db.query(Region).filter(Region.country_id.is_(None)).all()
        for region in regions_without_country:
            region.country_id = default_country.id

        if regions_without_country:
            db.commit()
    finally:
        db.close()


def ensure_initial_admin():
    db = SessionLocal()
    try:
        has_admin = db.query(User).filter(User.is_admin.is_(True)).first()
        if has_admin:
            return

        first_user = db.query(User).order_by(User.id.asc()).first()
        if first_user:
            first_user.is_admin = True
            db.commit()
    finally:
        db.close()


Base.metadata.create_all(bind=engine)
ensure_dive_log_time_columns()
ensure_user_columns()
ensure_region_country_columns()
ensure_dive_point_columns()
ensure_marine_weather_columns()
ensure_default_country()
ensure_initial_admin()
with SessionLocal() as db:
    recalculate_all_dive_numbers(db)
    db.commit()


@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse(
        "register.html",
        {
            "request": request,
            "error": None,
            "username": "",
        },
    )


@app.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    username = username.strip()

    db = SessionLocal()
    try:
        if not username or not password:
            return templates.TemplateResponse(
                "register.html",
                {
                    "request": request,
                    "error": "아이디와 비밀번호를 입력하세요.",
                    "username": username,
                },
                status_code=400,
            )

        if password != password_confirm:
            return templates.TemplateResponse(
                "register.html",
                {
                    "request": request,
                    "error": "비밀번호 확인이 일치하지 않습니다.",
                    "username": username,
                },
                status_code=400,
            )

        existing_user = db.query(User).filter(User.username == username).first()
        if existing_user:
            return templates.TemplateResponse(
                "register.html",
                {
                    "request": request,
                    "error": "이미 사용 중인 아이디입니다.",
                    "username": username,
                },
                status_code=400,
            )

        is_first_user = db.query(func.count(User.id)).scalar() == 0
        user = User(
            username=username,
            password_hash=hash_password(password),
            is_admin=is_first_user,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        request.session["user_id"] = user.id
        request.session["username"] = user.username
        request.session["is_admin"] = user.is_admin

        return RedirectResponse(url="/", status_code=303)
    finally:
        db.close()


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
            "username": "",
            "next_url": request.query_params.get("next", "/"),
        },
    )


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next_url: str = Form("/"),
):
    username = username.strip()

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user or not verify_password(password, user.password_hash):
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "아이디 또는 비밀번호가 올바르지 않습니다.",
                    "username": username,
                    "next_url": next_url or "/",
                },
                status_code=400,
            )

        request.session["user_id"] = user.id
        request.session["username"] = user.username
        request.session["is_admin"] = user.is_admin

        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"

        return RedirectResponse(url=next_url or "/", status_code=303)
    finally:
        db.close()


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@app.get("/friends")
def friends_page(request: Request):
    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        query = request.query_params.get("q", "").strip()
        search_results = []

        if query:
            candidates = (
                db.query(User)
                .filter(User.id != current_user.id)
                .filter(User.username.ilike(f"%{query}%"))
                .order_by(User.username.asc())
                .limit(20)
                .all()
            )
            for candidate in candidates:
                search_results.append(
                    {
                        "user": candidate,
                        "friendship": friendship_between(db, current_user.id, candidate.id),
                    }
                )

        friends = get_accepted_friends(db, current_user.id)
        received_requests = (
            db.query(Friend)
            .options(joinedload(Friend.requester))
            .filter(
                Friend.addressee_id == current_user.id,
                Friend.status == "pending",
            )
            .order_by(Friend.id.desc())
            .all()
        )
        sent_requests = (
            db.query(Friend)
            .options(joinedload(Friend.addressee))
            .filter(
                Friend.requester_id == current_user.id,
                Friend.status == "pending",
            )
            .order_by(Friend.id.desc())
            .all()
        )

        return templates.TemplateResponse(
            "friends.html",
            {
                "request": request,
                "query": query,
                "search_results": search_results,
                "friends": friends,
                "received_requests": received_requests,
                "sent_requests": sent_requests,
                "error": request.query_params.get("error"),
                "message": request.query_params.get("message"),
            },
        )
    finally:
        db.close()


@app.post("/friends/request/{user_id}")
def send_friend_request(request: Request, user_id: int):
    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        if current_user.id == user_id:
            return RedirectResponse(url="/friends?error=자기 자신에게는 친구 요청을 보낼 수 없습니다.", status_code=303)

        target_user = db.query(User).filter(User.id == user_id).first()
        if not target_user:
            return RedirectResponse(url="/friends?error=사용자를 찾을 수 없습니다.", status_code=303)

        friendship = friendship_between(db, current_user.id, target_user.id)
        if friendship:
            if friendship.status == "rejected":
                friendship.requester_id = current_user.id
                friendship.addressee_id = target_user.id
                friendship.status = "pending"
                db.commit()
                return RedirectResponse(url="/friends?message=친구 요청을 보냈습니다.", status_code=303)

            return RedirectResponse(url="/friends?error=이미 친구 요청 또는 친구 관계가 있습니다.", status_code=303)

        db.add(
            Friend(
                requester_id=current_user.id,
                addressee_id=target_user.id,
                status="pending",
            )
        )
        db.commit()
        return RedirectResponse(url="/friends?message=친구 요청을 보냈습니다.", status_code=303)
    finally:
        db.close()


@app.post("/friends/{friendship_id}/accept")
def accept_friend_request(request: Request, friendship_id: int):
    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        friendship = (
            db.query(Friend)
            .filter(
                Friend.id == friendship_id,
                Friend.addressee_id == current_user.id,
                Friend.status == "pending",
            )
            .first()
        )
        if not friendship:
            return RedirectResponse(url="/friends?error=친구 요청을 찾을 수 없습니다.", status_code=303)

        friendship.status = "accepted"
        db.commit()
        return RedirectResponse(url="/friends?message=친구 요청을 수락했습니다.", status_code=303)
    finally:
        db.close()


@app.post("/friends/{friendship_id}/reject")
def reject_friend_request(request: Request, friendship_id: int):
    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        friendship = (
            db.query(Friend)
            .filter(
                Friend.id == friendship_id,
                Friend.addressee_id == current_user.id,
                Friend.status == "pending",
            )
            .first()
        )
        if not friendship:
            return RedirectResponse(url="/friends?error=친구 요청을 찾을 수 없습니다.", status_code=303)

        friendship.status = "rejected"
        db.commit()
        return RedirectResponse(url="/friends?message=친구 요청을 거절했습니다.", status_code=303)
    finally:
        db.close()


@app.get("/trips")
def trips_page(request: Request):
    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        trips = (
            db.query(DiveTrip)
            .join(TripParticipant, TripParticipant.trip_id == DiveTrip.id)
            .filter(TripParticipant.user_id == current_user.id)
            .order_by(DiveTrip.start_date.desc(), DiveTrip.id.desc())
            .all()
        )
        friends = get_accepted_friends(db, current_user.id)

        return templates.TemplateResponse(
            "trips.html",
            {
                "request": request,
                "trips": trips,
                "friends": friends,
                "error": request.query_params.get("error"),
                "message": request.query_params.get("message"),
            },
        )
    finally:
        db.close()


@app.post("/trips")
def create_trip(
    request: Request,
    name: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    region: str = Form(...),
    participant_ids: list[str] = Form([]),
):
    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        friend_ids = set(get_accepted_friend_ids(db, current_user.id))
        selected_ids = {
            participant_id
            for participant_id in (parse_int_filter(value) for value in participant_ids)
            if participant_id
        }

        if selected_ids - friend_ids:
            return RedirectResponse(url="/trips?error=친구만 투어 참가자로 추가할 수 있습니다.", status_code=303)

        trip = DiveTrip(
            name=name.strip(),
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            region=region.strip(),
            owner_id=current_user.id,
        )
        db.add(trip)
        db.commit()
        db.refresh(trip)

        participant_user_ids = {current_user.id, *selected_ids}
        for participant_user_id in participant_user_ids:
            db.add(TripParticipant(trip_id=trip.id, user_id=participant_user_id))
        db.commit()

        return RedirectResponse(url=f"/trips/{trip.id}", status_code=303)
    finally:
        db.close()


@app.get("/trips/{trip_id}")
def trip_detail(request: Request, trip_id: int):
    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        if not is_trip_participant(db, trip_id, current_user.id):
            return RedirectResponse(url="/trips?error=참가 중인 투어만 볼 수 있습니다.", status_code=303)

        trip = (
            db.query(DiveTrip)
            .options(
                joinedload(DiveTrip.participants).joinedload(TripParticipant.user),
                joinedload(DiveTrip.photos).joinedload(TripPhoto.uploader),
            )
            .filter(DiveTrip.id == trip_id)
            .first()
        )
        if not trip:
            return RedirectResponse(url="/trips?error=투어를 찾을 수 없습니다.", status_code=303)

        return templates.TemplateResponse(
            "trip_detail.html",
            {
                "request": request,
                "trip": trip,
                "error": request.query_params.get("error"),
                "message": request.query_params.get("message"),
            },
        )
    finally:
        db.close()


@app.post("/trips/{trip_id}/photos")
async def upload_trip_photo(
    request: Request,
    trip_id: int,
    caption: str = Form(""),
    photo: UploadFile = File(...),
):
    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        if not is_trip_participant(db, trip_id, current_user.id):
            return RedirectResponse(url="/trips?error=참가 중인 투어에만 사진을 올릴 수 있습니다.", status_code=303)

        if not photo or not photo.filename:
            return RedirectResponse(url=f"/trips/{trip_id}?error=업로드할 사진을 선택하세요.", status_code=303)

        suffix, upload_error = validate_upload(
            photo,
            set(ALLOWED_IMAGE_EXTENSIONS.keys()),
            set(ALLOWED_IMAGE_EXTENSIONS.values()),
            MAX_IMAGE_UPLOAD_SIZE,
        )
        if upload_error:
            return RedirectResponse(url=f"/trips/{trip_id}?error={upload_error}", status_code=303)

        filename = f"{uuid.uuid4().hex}{suffix}"
        file_location = TRIP_UPLOAD_DIR / filename

        upload_error = save_upload_file(photo, file_location, MAX_IMAGE_UPLOAD_SIZE)
        if upload_error:
            return RedirectResponse(url=f"/trips/{trip_id}?error={upload_error}", status_code=303)

        trip_photo = TripPhoto(
            trip_id=trip_id,
            uploader_id=current_user.id,
            image_path=f"uploads/trips/{filename}",
            caption=caption.strip() or None,
        )
        db.add(trip_photo)
        db.commit()

        return RedirectResponse(url=f"/trips/{trip_id}?message=사진을 업로드했습니다.", status_code=303)
    finally:
        db.close()


@app.get("/admin/users")
def admin_users(request: Request):
    db = SessionLocal()
    try:
        current_user, redirect = admin_required_redirect(request, db)
        if redirect:
            return redirect

        users = (
            db.query(
                User,
                func.count(DiveLog.id).label("log_count"),
            )
            .outerjoin(DiveLog, DiveLog.user_id == User.id)
            .group_by(User.id)
            .order_by(User.id.asc())
            .all()
        )
        admin_count = db.query(func.count(User.id)).filter(User.is_admin.is_(True)).scalar()

        return templates.TemplateResponse(
            "admin_users.html",
            {
                "request": request,
                "current_user": current_user,
                "users": users,
                "admin_count": admin_count,
                "error": request.query_params.get("error"),
                "message": request.query_params.get("message"),
            },
        )
    finally:
        db.close()


@app.post("/admin/users/{user_id}/toggle-admin")
def toggle_user_admin(request: Request, user_id: int):
    db = SessionLocal()
    try:
        current_user, redirect = admin_required_redirect(request, db)
        if redirect:
            return redirect

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return RedirectResponse(url="/admin/users?error=사용자를 찾을 수 없습니다.", status_code=303)

        admin_count = db.query(func.count(User.id)).filter(User.is_admin.is_(True)).scalar()
        if user.is_admin and admin_count <= 1:
            return RedirectResponse(url="/admin/users?error=관리자는 최소 1명 이상 필요합니다.", status_code=303)

        user.is_admin = not user.is_admin
        db.commit()

        if current_user.id == user.id:
            request.session["is_admin"] = user.is_admin

        return RedirectResponse(url="/admin/users?message=권한이 변경되었습니다.", status_code=303)
    finally:
        db.close()


@app.post("/admin/users/{user_id}/delete")
def delete_user(request: Request, user_id: int):
    db = SessionLocal()
    try:
        current_user, redirect = admin_required_redirect(request, db)
        if redirect:
            return redirect

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return RedirectResponse(url="/admin/users?error=사용자를 찾을 수 없습니다.", status_code=303)

        if current_user.id == user.id:
            return RedirectResponse(url="/admin/users?error=자기 자신은 삭제할 수 없습니다.", status_code=303)

        admin_count = db.query(func.count(User.id)).filter(User.is_admin.is_(True)).scalar()
        if user.is_admin and admin_count <= 1:
            return RedirectResponse(url="/admin/users?error=관리자는 최소 1명 이상 필요합니다.", status_code=303)

        db.query(DiveLog).filter(DiveLog.user_id == user.id).update({DiveLog.user_id: None})
        db.delete(user)
        db.commit()

        return RedirectResponse(url="/admin/users?message=사용자가 삭제되었습니다.", status_code=303)
    finally:
        db.close()


@app.get("/admin/divepoints")
def admin_divepoints(request: Request):
    db = SessionLocal()
    try:
        current_user, redirect = admin_required_redirect(request, db)
        if redirect:
            return redirect

        points = (
            db.query(DivePoint)
            .options(
                joinedload(DivePoint.area)
                .joinedload(Area.region)
                .joinedload(Region.country)
            )
            .order_by(DivePoint.id.desc())
            .limit(50)
            .all()
        )

        return templates.TemplateResponse(
            "admin_divepoints.html",
            {
                "request": request,
                "points": points,
                "error": request.query_params.get("error"),
                "message": request.query_params.get("message"),
            },
        )
    finally:
        db.close()


@app.post("/admin/divepoints")
def create_admin_divepoint(
    request: Request,
    country_name: str = Form(...),
    region_name: str = Form(...),
    area_name: str = Form(...),
    point_name: str = Form(...),
    latitude: str = Form(...),
    longitude: str = Form(...),
):
    db = SessionLocal()
    try:
        current_user, redirect = admin_required_redirect(request, db)
        if redirect:
            return redirect

        latitude_value = parse_float_value(latitude)
        longitude_value = parse_float_value(longitude)
        required_values = [country_name.strip(), region_name.strip(), area_name.strip(), point_name.strip()]
        if not all(required_values) or not valid_coordinate(latitude_value, longitude_value):
            return RedirectResponse(url=admin_divepoints_url(error="입력값을 확인하세요."), status_code=303)

        point, created = get_or_create_dive_point(
            db,
            country_name,
            region_name,
            area_name,
            point_name,
            latitude_value,
            longitude_value,
        )
        db.commit()

        message = "포인트를 추가했습니다." if created else "기존 포인트의 위치를 업데이트했습니다."
        return RedirectResponse(url=admin_divepoints_url(message=message), status_code=303)
    finally:
        db.close()


@app.post("/admin/divepoints/import")
async def import_admin_divepoints(request: Request, csv_file: UploadFile = File(...)):
    db = SessionLocal()
    try:
        current_user, redirect = admin_required_redirect(request, db)
        if redirect:
            return redirect

        if not csv_file.filename:
            return RedirectResponse(url=admin_divepoints_url(error="업로드할 CSV 파일을 선택하세요."), status_code=303)

        suffix, upload_error = validate_upload(
            csv_file,
            {".csv"},
            {"text/csv", "application/csv", "application/vnd.ms-excel", "application/octet-stream"},
            MAX_IMPORT_UPLOAD_SIZE,
        )
        if upload_error:
            return RedirectResponse(url=admin_divepoints_url(error=upload_error), status_code=303)

        saved_filename = f"divepoints_{uuid.uuid4().hex}{suffix}"
        saved_path = IMPORT_UPLOAD_DIR / saved_filename
        upload_error = save_upload_file(csv_file, saved_path, MAX_IMPORT_UPLOAD_SIZE)
        if upload_error:
            return RedirectResponse(url=admin_divepoints_url(error=upload_error), status_code=303)

        try:
            preview_rows, error_rows = parse_divepoint_gps_csv(saved_path)
        except UnicodeDecodeError:
            return RedirectResponse(url=admin_divepoints_url(error="UTF-8 CSV 파일만 지원합니다."), status_code=303)

        return templates.TemplateResponse(
            "admin_divepoints_preview.html",
            {
                "request": request,
                "source_path": saved_path,
                "original_filename": Path(csv_file.filename).name,
                "preview_rows": preview_rows,
                "error_rows": error_rows,
            },
        )
    finally:
        db.close()


@app.post("/admin/divepoints/import/confirm")
def confirm_admin_divepoints_import(request: Request, source_path: str = Form(...)):
    db = SessionLocal()
    try:
        current_user, redirect = admin_required_redirect(request, db)
        if redirect:
            return redirect

        import_path = import_source_path_from_form(source_path)
        if not import_path:
            return RedirectResponse(url=admin_divepoints_url(error="CSV 미리보기 파일을 찾을 수 없습니다."), status_code=303)

        preview_rows, error_rows = parse_divepoint_gps_csv(import_path)
        created_count = 0
        updated_count = 0

        for row in preview_rows:
            point, created = get_or_create_dive_point(
                db,
                row["country"],
                row["region"],
                row["area"],
                row["point_name"],
                parse_float_value(row["latitude"]),
                parse_float_value(row["longitude"]),
                row["memo"],
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        db.commit()
        message = f"CSV 등록 완료: 추가 {created_count}개, 업데이트 {updated_count}개, 건너뜀 {len(error_rows)}개"
        return RedirectResponse(url=admin_divepoints_url(message=message), status_code=303)
    finally:
        db.close()


@app.get("/map")
def map_page(request: Request):
    return templates.TemplateResponse(
        "index.html", {"request": request}
    )

@app.get("/import")
def import_page(request: Request):
    return templates.TemplateResponse(
        "import.html",
        {
            "request": request,
            "error": request.query_params.get("error"),
            "max_import_file_size_mb": settings.max_import_file_size_mb,
        }
    )


@app.get("/import/result")
def import_result_page(request: Request):
    result = request.session.get("import_result")
    if not result:
        return RedirectResponse(url="/import?error=가져오기 결과 정보를 찾을 수 없습니다.", status_code=303)
    return templates.TemplateResponse(
        "import_result.html",
        {
            "request": request,
            "result": result,
        },
    )


@app.post("/import/preview")
async def import_preview(request: Request, import_file: UploadFile = File(...)):
    if not import_file.filename:
        return templates.TemplateResponse(
            "import.html",
            {
                "request": request,
                "error": "업로드할 파일을 선택하세요.",
                "max_import_file_size_mb": settings.max_import_file_size_mb,
            },
            status_code=400
        )

    original_filename = Path(import_file.filename).name
    suffix, upload_error = validate_upload(
        import_file,
        ALLOWED_IMPORT_EXTENSIONS,
        {
            "text/csv",
            "application/csv",
            "application/xml",
            "text/xml",
            "application/octet-stream",
            "application/vnd.ms-excel",
            "application/x-sqlite3",
            "application/vnd.sqlite3",
        },
        MAX_IMPORT_UPLOAD_SIZE,
    )
    if upload_error:
        return templates.TemplateResponse(
            "import.html",
            {
                "request": request,
                "error": upload_error,
                "max_import_file_size_mb": settings.max_import_file_size_mb,
            },
            status_code=400
        )

    saved_filename = f"{uuid.uuid4().hex}{suffix}"
    saved_path = IMPORT_UPLOAD_DIR / saved_filename

    upload_error = save_upload_file(import_file, saved_path, MAX_IMPORT_UPLOAD_SIZE)
    if upload_error:
        return templates.TemplateResponse(
            "import.html",
            {
                "request": request,
                "error": upload_error,
                "max_import_file_size_mb": settings.max_import_file_size_mb,
            },
            status_code=400
        )

    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        preview = parse_import_file(saved_path, original_filename)
        attach_point_suggestions(db, preview)
        batch = create_import_batch(IMPORT_UPLOAD_DIR, preview)
        source_file_hash = file_sha256(saved_path)
        batch["source_file_hash"] = source_file_hash
        for item in batch.get("dives", []):
            item["source_file_hash"] = source_file_hash
        apply_duplicate_detection_to_batch(
            db,
            batch,
            current_user.id if current_user else None,
        )
        save_import_batch(IMPORT_UPLOAD_DIR, batch)
    except UnsupportedImportFormat as exc:
        return templates.TemplateResponse(
            "import.html",
            {
                "request": request,
                "error": str(exc),
                "max_import_file_size_mb": settings.max_import_file_size_mb,
            },
            status_code=400
        )
    except Exception:
        return templates.TemplateResponse(
            "import.html",
            {
                "request": request,
                "error": "파일 내용을 읽는 중 오류가 발생했습니다. 지원 형식과 파일 내용을 확인하세요.",
                "max_import_file_size_mb": settings.max_import_file_size_mb,
            },
            status_code=400
        )
    finally:
        db.close()

    return RedirectResponse(url=f"/import/preview/{batch['batch_id']}?page=1", status_code=303)


@app.get("/import/preview/{batch_id}")
def import_preview_batch(request: Request, batch_id: str, page: int = 1):
    batch = load_import_batch(IMPORT_UPLOAD_DIR, batch_id)
    if not batch:
        return RedirectResponse(url="/import?error=가져오기 미리보기 정보를 찾을 수 없습니다.", status_code=303)

    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        preview, pagination = build_import_batch_preview(batch, page)
        duplicate_summary = import_batch_duplicate_summary(
            db,
            batch,
            current_user.id if current_user else None,
        )
        points = (
            db.query(DivePoint)
            .options(joinedload(DivePoint.area).joinedload(Area.region).joinedload(Region.country))
            .order_by(DivePoint.name.asc())
            .all()
        )
        return templates.TemplateResponse(
            "import_preview.html",
            {
                "request": request,
                "preview": preview,
                "points": points,
                "duplicate_summary": duplicate_summary,
                "pagination": pagination,
                "batch_id": batch_id,
            }
        )
    finally:
        db.close()


@app.post("/import/preview/{batch_id}/action")
def import_preview_batch_action(
    request: Request,
    batch_id: str,
    action: str = Form(...),
    page: int = Form(1),
    row_indexes: list[str] = Form([]),
    selected_import_indexes: list[str] = Form([]),
    dive_dates: list[str] = Form([]),
    entry_times: list[str] = Form([]),
    exit_times: list[str] = Form([]),
    max_depths: list[str] = Form([]),
    avg_depths: list[str] = Form([]),
    dive_times: list[str] = Form([]),
    water_temps: list[str] = Form([]),
    selected_point_ids: list[str] = Form([]),
    candidate_country_names: list[str] = Form([]),
    candidate_region_names: list[str] = Form([]),
    candidate_area_names: list[str] = Form([]),
    candidate_point_names: list[str] = Form([]),
):
    batch = load_import_batch(IMPORT_UPLOAD_DIR, batch_id)
    if not batch:
        return RedirectResponse(url="/import?error=가져오기 미리보기 정보를 찾을 수 없습니다.", status_code=303)

    apply_import_batch_page_form(
        batch,
        row_indexes,
        selected_import_indexes,
        dive_dates,
        entry_times,
        exit_times,
        max_depths,
        avg_depths,
        dive_times,
        water_temps,
        selected_point_ids,
        candidate_country_names,
        candidate_region_names,
        candidate_area_names,
        candidate_point_names,
    )

    if action == "select_all":
        set_import_batch_selection(batch, True)
    elif action == "select_page":
        select_import_batch_page(batch, row_indexes)
    elif action == "clear_all":
        set_import_batch_selection(batch, False)

    if action.startswith("page:"):
        page = parse_int_filter(action.split(":", 1)[1]) or page

    if action == "confirm_without_duplicates":
        for item in batch.get("dives", []):
            if item.get("duplicate_match"):
                item["selected"] = False

    if action in {"confirm", "confirm_without_duplicates"}:
        db = SessionLocal()
        try:
            current_user = get_current_user(request, db)
            result = save_import_batch_logs(
                db,
                batch,
                current_user.id if current_user else None,
            )
            db.commit()
            request.session["import_result"] = result
            return RedirectResponse(url="/import/result", status_code=303)
        finally:
            db.close()

    save_import_batch(IMPORT_UPLOAD_DIR, batch)
    return RedirectResponse(url=f"/import/preview/{batch_id}?page={page}", status_code=303)


@app.post("/import/confirm")
def import_confirm(
    request: Request,
    source_path: str = Form(...),
    original_filename: str = Form(...),
    dive_dates: list[str] = Form([]),
    entry_times: list[str] = Form([]),
    exit_times: list[str] = Form([]),
    max_depths: list[str] = Form([]),
    avg_depths: list[str] = Form([]),
    dive_times: list[str] = Form([]),
    water_temps: list[str] = Form([]),
    selected_point_ids: list[str] = Form([]),
    candidate_country_names: list[str] = Form([]),
    candidate_region_names: list[str] = Form([]),
    candidate_area_names: list[str] = Form([]),
    candidate_point_names: list[str] = Form([]),
):
    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        import_path = import_source_path_from_form(source_path)
        if not import_path:
            return RedirectResponse(url="/import?error=가져오기 파일을 찾을 수 없습니다.", status_code=303)

        preview = parse_import_file(import_path, original_filename)
        user_id = current_user.id if current_user else None
        batch = {
            "dives": [
                dive_to_batch_item(dive, index)
                for index, dive in enumerate(preview.dives)
            ]
        }
        source_file_hash = file_sha256(import_path)
        batch["source_file_hash"] = source_file_hash
        for item in batch.get("dives", []):
            item["source_file_hash"] = source_file_hash
        apply_import_batch_page_form(
            batch,
            [str(index) for index in range(len(preview.dives))],
            [str(index) for index in range(len(preview.dives))],
            dive_dates,
            entry_times,
            exit_times,
            max_depths,
            avg_depths,
            dive_times,
            water_temps,
            selected_point_ids,
            candidate_country_names,
            candidate_region_names,
            candidate_area_names,
            candidate_point_names,
        )
        result = save_import_items(db, batch["dives"], user_id)
        db.commit()
        request.session["import_result"] = result
        return RedirectResponse(url="/import/result", status_code=303)
    finally:
        db.close()

@app.get("/point/{point_id}/logs")
def point_logs(request: Request, point_id: int):

    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        point = db.query(DivePoint).filter(DivePoint.id == point_id).first()

        logs = (
            db.query(DiveLog)
            .filter(DiveLog.dive_point_id == point_id)
            .filter(visible_log_condition(current_user))
            .order_by(DiveLog.dive_number.desc(), DiveLog.id.desc())
            .all()
        )

        return templates.TemplateResponse(
            "point_logs.html",
            {
                "request": request,
                "point": point,
                "logs": logs
            }
        )
    finally:
        db.close()

@app.get("/log/add")
def log_add_page(request: Request):

    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        if not current_user:
            return login_required_redirect(request)

        points = db.query(DivePoint).all()
        friends = get_accepted_friends(db, current_user.id)

        last_dive_number = (
            db.query(func.max(DiveLog.dive_number))
            .filter(DiveLog.user_id == current_user.id)
            .scalar()
        )
        next_dive_number = (last_dive_number or 0) + 1

        return templates.TemplateResponse(
            "log_add.html",
            {
                "request": request,
                "points": points,
                "friends": friends,
                "next_dive_number": next_dive_number
            }
        )
    finally:
        db.close()

@app.post("/log/add")
async def add_log(
    request: Request,
    dive_date: str = Form(...),
    dive_point_id: int = Form(...),
    max_depth: float = Form(None),
    avg_depth: float = Form(None),
    entry_time: str = Form(None),
    exit_time: str = Form(None),
    dive_time: int = Form(None),
    water_temp: float = Form(None),
    visibility: int = Form(None),
    buddy: str = Form(None),
    buddy_user_id: str = Form(""),
    start_pressure: int = Form(None),
    end_pressure: int = Form(None),
    note: str = Form(None),
    image: UploadFile = File(None)
):

    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        if not current_user:
            return login_required_redirect(request)

        file_path = None

        if image and image.filename:
            suffix, upload_error = validate_upload(
                image,
                set(ALLOWED_IMAGE_EXTENSIONS.keys()),
                set(ALLOWED_IMAGE_EXTENSIONS.values()),
                MAX_IMAGE_UPLOAD_SIZE,
            )
            if upload_error:
                return RedirectResponse(url=f"/log/add?error={upload_error}", status_code=303)

            filename = f"{uuid.uuid4().hex}{suffix}"
            file_location = UPLOAD_DIR / filename

            upload_error = save_upload_file(image, file_location, MAX_IMAGE_UPLOAD_SIZE)
            if upload_error:
                return RedirectResponse(url=f"/log/add?error={upload_error}", status_code=303)

            file_path = f"uploads/{filename}"

        selected_buddy_user_id = parse_int_filter(buddy_user_id)
        if selected_buddy_user_id:
            buddy_user = friendship_between(db, current_user.id, selected_buddy_user_id)
            if not buddy_user or buddy_user.status != "accepted":
                selected_buddy_user_id = None

        buddy_text = buddy.strip() if buddy else None
        if selected_buddy_user_id and not buddy_text:
            selected_buddy = db.query(User).filter(User.id == selected_buddy_user_id).first()
            buddy_text = selected_buddy.username if selected_buddy else None

        log = DiveLog(
            user_id=current_user.id,
            buddy_user_id=selected_buddy_user_id,
            dive_point_id=dive_point_id,
            dive_date=date.fromisoformat(dive_date),
            max_depth=max_depth,
            avg_depth=avg_depth,
            entry_time=parse_time(entry_time),
            exit_time=parse_time(exit_time),
            dive_time=dive_time,
            water_temp=water_temp,
            visibility=visibility,
            start_pressure=start_pressure,
            end_pressure=end_pressure,
            buddy=buddy_text,
            note=note,
            image_path=file_path,
        )

        db.add(log)
        db.commit()

        saved_log_id = log.id
        recalculate_dive_numbers(db, current_user.id)
        db.commit()

        return RedirectResponse(
            url=f"/log/success/{saved_log_id}",
            status_code=303
        )
    finally:
        db.close()

@app.get("/log/success/{log_id}")
def log_success(request: Request, log_id: int):

    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        log = (
            db.query(DiveLog)
            .options(joinedload(DiveLog.dive_point))
            .filter(DiveLog.id == log_id)
            .filter(visible_log_condition(current_user))
            .first()
        )

        return templates.TemplateResponse(
            "log_success.html",
            {
                "request": request,
                "log": log
            }
        )
    finally:
        db.close()

@app.get("/log/{log_id}/edit")
def edit_log_page(request: Request, log_id: int):

    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        if not current_user:
            return login_required_redirect(request)

        log = (
            db.query(DiveLog)
            .filter(DiveLog.id == log_id)
            .filter(owned_log_condition(current_user))
            .first()
        )
        if not log:
            return RedirectResponse(url="/logs", status_code=303)

        points = db.query(DivePoint).all()

        return templates.TemplateResponse(
            "log_edit.html",
            {
                "request": request,
                "log": log,
                "points": points
            }
        )
    finally:
        db.close()

@app.post("/log/{log_id}/edit")
def edit_log(
    request: Request,
    log_id: int,
    dive_point_id: int = Form(...),
    dive_date: str = Form(...),
    max_depth: float = Form(...),
    avg_depth: float = Form(...),
    entry_time: str = Form(None),
    exit_time: str = Form(None),
    dive_time: int = Form(...),
    water_temp: float = Form(...),
    visibility: float = Form(...),
    buddy: str = Form(""),
    start_pressure: int = Form(None),
    end_pressure: int = Form(None),
    note: str = Form("")
):

    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        if not current_user:
            return login_required_redirect(request)

        log = (
            db.query(DiveLog)
            .filter(DiveLog.id == log_id)
            .filter(owned_log_condition(current_user))
            .first()
        )
        if not log:
            return RedirectResponse(url="/logs", status_code=303)

        log.dive_point_id = dive_point_id
        log.dive_date = date.fromisoformat(dive_date)
        log.max_depth = max_depth
        log.avg_depth = avg_depth
        log.entry_time = parse_time(entry_time)
        log.exit_time = parse_time(exit_time)
        log.dive_time = dive_time
        log.water_temp = water_temp
        log.visibility = visibility
        log.buddy = buddy
        log.start_pressure = start_pressure
        log.end_pressure = end_pressure
        log.note = note

        point_id = log.dive_point_id
        recalculate_dive_numbers(db, current_user.id)
        db.commit()

        return RedirectResponse(
            url=f"/point/{point_id}/logs?updated=1",
            status_code=303
        )
    finally:
        db.close()

@app.post("/log/{log_id}/delete")
def delete_log(request: Request, log_id: int):

    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        if not current_user:
            return login_required_redirect(request)

        log = (
            db.query(DiveLog)
            .filter(DiveLog.id == log_id)
            .filter(owned_log_condition(current_user))
            .first()
        )
        if not log:
            return RedirectResponse(url="/logs", status_code=303)

        point_id = log.dive_point_id

        db.delete(log)
        recalculate_dive_numbers(db, current_user.id)
        db.commit()

        return RedirectResponse(
            url=f"/point/{point_id}/logs?deleted=1",
            status_code=303
        )
    finally:
        db.close()


@app.post("/logs/delete-selected")
def delete_selected_logs(
    request: Request,
    log_ids: list[int] = Form([]),
):
    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        if not current_user:
            return login_required_redirect(request)

        if not log_ids:
            return RedirectResponse(url="/logs?error=삭제할 로그를 선택하세요.", status_code=303)

        logs = (
            editable_log_query(db, current_user)
            .filter(DiveLog.id.in_(log_ids))
            .all()
        )
        allowed_ids = {log.id for log in logs}
        denied_ids = [log_id for log_id in log_ids if log_id not in allowed_ids]
        affected_user_ids = {log.user_id for log in logs}

        for log in logs:
            db.delete(log)

        recalculate_after_log_changes(db, current_user, affected_user_ids)
        db.commit()

        message = f"선택삭제 완료: 삭제된 로그 {len(logs)}개"
        if denied_ids:
            message += f", 권한 없음 또는 찾을 수 없는 로그 {len(denied_ids)}개"
        remaining_count = editable_log_query(db, current_user).count()
        message += f", 남은 로그 {remaining_count}개"
        return RedirectResponse(url=f"/logs?{urlencode({'message': message})}", status_code=303)
    finally:
        db.close()


@app.get("/logs/delete-all-confirm")
def delete_all_logs_confirm(request: Request):
    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        if not current_user:
            return login_required_redirect(request)

        delete_count = editable_log_query(db, current_user).count()
        return templates.TemplateResponse(
            "logs_delete_all_confirm.html",
            {
                "request": request,
                "delete_count": delete_count,
                "is_admin": current_user.is_admin,
            },
        )
    finally:
        db.close()


@app.post("/logs/delete-all")
def delete_all_logs(request: Request):
    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        if not current_user:
            return login_required_redirect(request)

        logs = editable_log_query(db, current_user).all()
        affected_user_ids = {log.user_id for log in logs}
        delete_count = len(logs)
        for log in logs:
            db.delete(log)

        recalculate_after_log_changes(db, current_user, affected_user_ids)
        db.commit()

        message = f"전체 삭제 완료: 삭제된 로그 {delete_count}개, 남은 로그 0개"
        return RedirectResponse(url=f"/logs?{urlencode({'message': message})}", status_code=303)
    finally:
        db.close()


@app.get("/admin/recalculate-dive-numbers")
def admin_recalculate_dive_numbers(request: Request):
    db = SessionLocal()
    try:
        current_user, redirect = admin_required_redirect(request, db)
        if redirect:
            return redirect

        total_count = recalculate_all_dive_numbers(db)
        db.commit()
        message = f"다이브 번호 재정렬 완료: 대상 로그 {total_count}개"
        return RedirectResponse(url=f"/logs?{urlencode({'message': message})}", status_code=303)
    finally:
        db.close()
    
@app.get("/stats")
def stats_page(request: Request):

    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        visible_logs_query = db.query(DiveLog).filter(visible_log_condition(current_user))

        total_dives = visible_logs_query.with_entities(func.count(DiveLog.id)).scalar()

        max_depth = visible_logs_query.with_entities(func.max(DiveLog.max_depth)).scalar()

        avg_depth = visible_logs_query.with_entities(func.avg(DiveLog.avg_depth)).scalar()

        total_dive_time = visible_logs_query.with_entities(func.sum(DiveLog.dive_time)).scalar()

        avg_water_temp = visible_logs_query.with_entities(func.avg(DiveLog.water_temp)).scalar()

        monthly_dive_date = monthly_dive_date_expression(db).label("month")
        monthly_dives = (
            db.query(
                monthly_dive_date,
                func.count(DiveLog.id).label("dive_count"),
            )
            .filter(visible_log_condition(current_user))
            .group_by(monthly_dive_date)
            .order_by(monthly_dive_date)
            .all()
        )

        region_dives = (
            db.query(
                Region.name.label("name"),
                func.count(DiveLog.id).label("dive_count"),
            )
            .select_from(DiveLog)
            .join(DivePoint, DiveLog.dive_point_id == DivePoint.id)
            .join(Area, DivePoint.area_id == Area.id)
            .join(Region, Area.region_id == Region.id)
            .filter(visible_log_condition(current_user))
            .group_by(Region.id, Region.name)
            .order_by(func.count(DiveLog.id).desc(), Region.name.asc())
            .all()
        )

        point_dives = (
            db.query(
                DivePoint.name.label("name"),
                func.count(DiveLog.id).label("dive_count"),
            )
            .select_from(DiveLog)
            .join(DivePoint, DiveLog.dive_point_id == DivePoint.id)
            .filter(visible_log_condition(current_user))
            .group_by(DivePoint.id, DivePoint.name)
            .order_by(func.count(DiveLog.id).desc(), DivePoint.name.asc())
            .all()
        )

        most_visited_point = point_dives[0] if point_dives else None
        max_monthly_dives = max([row.dive_count for row in monthly_dives], default=0)
        max_region_dives = max([row.dive_count for row in region_dives], default=0)
        max_point_dives = max([row.dive_count for row in point_dives], default=0)

        return templates.TemplateResponse(
            "stats.html",
            {
                "request": request,
                "total_dives": total_dives,
                "max_depth": max_depth,
                "avg_depth": avg_depth,
                "total_dive_time": total_dive_time,
                "avg_water_temp": avg_water_temp,
                "monthly_dives": monthly_dives,
                "region_dives": region_dives,
                "point_dives": point_dives,
                "most_visited_point": most_visited_point,
                "max_monthly_dives": max_monthly_dives,
                "max_region_dives": max_region_dives,
                "max_point_dives": max_point_dives
            }
        )
    finally:
        db.close()

@app.get("/log/{log_id}")
def log_detail(request: Request, log_id: int):

    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        log = (
            db.query(DiveLog)
            .options(
                joinedload(DiveLog.dive_point)
                .joinedload(DivePoint.area)
                .joinedload(Area.region)
            )
            .filter(DiveLog.id == log_id)
            .filter(visible_log_condition(current_user))
            .first()
        )

        return templates.TemplateResponse(
            "log_detail.html",
            {
                "request": request,
                "log": log
            }
        )
    finally:
        db.close()

@app.get("/")
def home(request: Request):

    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        visible_logs_query = db.query(DiveLog).filter(visible_log_condition(current_user))

        total_dives = visible_logs_query.with_entities(func.count(DiveLog.id)).scalar()
        total_dive_time = visible_logs_query.with_entities(func.sum(DiveLog.dive_time)).scalar()
        recent_logs = (
            db.query(DiveLog)
            .options(
                joinedload(DiveLog.dive_point)
            )
            .filter(visible_log_condition(current_user))
            .order_by(DiveLog.dive_number.desc(), DiveLog.id.desc())
            .limit(3)
            .all()
        )

        return templates.TemplateResponse(
            "home.html",
            {
                "request": request,
                "total_dives": total_dives,
                "total_dive_time": total_dive_time,
                "recent_logs": recent_logs
            }
        )
    finally:
        db.close()

@app.get("/logs")
def all_logs(request: Request):

    db = SessionLocal()
    try:
        current_user = get_current_user(request, db)
        filters = {
            "dive_date": request.query_params.get("dive_date", "").strip(),
            "country_id": request.query_params.get("country_id", "").strip(),
            "region_id": request.query_params.get("region_id", "").strip(),
            "area_id": request.query_params.get("area_id", "").strip(),
            "point_id": request.query_params.get("point_id", "").strip(),
            "buddy": request.query_params.get("buddy", "").strip(),
            "sort": request.query_params.get("sort", "number").strip(),
            "direction": request.query_params.get("direction", "asc").strip(),
        }
        recalculate_after_log_changes(db, current_user)
        db.commit()

        query = (
            db.query(DiveLog)
            .options(
                joinedload(DiveLog.dive_point)
                .joinedload(DivePoint.area)
                .joinedload(Area.region)
                .joinedload(Region.country)
            )
            .outerjoin(DivePoint, DiveLog.dive_point_id == DivePoint.id)
            .outerjoin(Area, DivePoint.area_id == Area.id)
            .outerjoin(Region, Area.region_id == Region.id)
            .outerjoin(Country, Region.country_id == Country.id)
        )
        query = apply_visible_logs(query, current_user)

        dive_date_filter = parse_date_filter(filters["dive_date"])
        country_id_filter = parse_int_filter(filters["country_id"])
        region_id_filter = parse_int_filter(filters["region_id"])
        area_id_filter = parse_int_filter(filters["area_id"])
        point_id_filter = parse_int_filter(filters["point_id"])

        if dive_date_filter:
            query = query.filter(DiveLog.dive_date == dive_date_filter)

        if country_id_filter:
            query = query.filter(Country.id == country_id_filter)

        if region_id_filter:
            query = query.filter(Region.id == region_id_filter)

        if area_id_filter:
            query = query.filter(Area.id == area_id_filter)

        if point_id_filter:
            query = query.filter(DivePoint.id == point_id_filter)

        if filters["buddy"]:
            query = query.filter(DiveLog.buddy.ilike(f"%{filters['buddy']}%"))

        sort_columns = {
            "date": DiveLog.dive_date,
            "country": Country.name,
            "region": Region.name,
            "area": Area.name,
            "point": DivePoint.name,
            "buddy": DiveLog.buddy,
            "number": DiveLog.dive_number,
            "id": DiveLog.dive_number,
        }
        sort_column = sort_columns.get(filters["sort"], DiveLog.dive_date)
        direction = filters["direction"] if filters["direction"] in ("asc", "desc") else "asc"
        if filters["sort"] == "date":
            order_expressions = [
                DiveLog.dive_date.asc() if direction == "asc" else DiveLog.dive_date.desc(),
                DiveLog.entry_time.asc() if direction == "asc" else DiveLog.entry_time.desc(),
                DiveLog.exit_time.asc() if direction == "asc" else DiveLog.exit_time.desc(),
                DiveLog.id.asc() if direction == "asc" else DiveLog.id.desc(),
            ]
        else:
            order_expression = sort_column.asc() if direction == "asc" else sort_column.desc()
            order_expressions = [order_expression, DiveLog.dive_date.asc(), DiveLog.entry_time.asc(), DiveLog.id.asc()]

        logs = (
            query
            .order_by(*order_expressions)
            .all()
        )

        countries = db.query(Country).order_by(Country.name.asc()).all()
        return templates.TemplateResponse(
            "logs.html",
            {
                "request": request,
                "logs": logs,
                "filters": filters,
                "countries": countries,
                "message": request.query_params.get("message"),
                "error": request.query_params.get("error"),
            }
        )
    finally:
        db.close()
