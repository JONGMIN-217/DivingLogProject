from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import joinedload

from datetime import date, datetime

from app.database import SessionLocal, engine
from app.models import Area, DivePoint, DiveLog, Region

from app.routers import regions
from app.routers import areas
from app.routers import divepoints
from app.routers import divelogs

from fastapi import UploadFile, File
import shutil
import uuid
from pathlib import Path
from fastapi.staticfiles import StaticFiles

app = FastAPI()

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

templates = Jinja2Templates(directory="templates")

app.include_router(regions.router)
app.include_router(areas.router)
app.include_router(divepoints.router)
app.include_router(divelogs.router)


def parse_time(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%H:%M").time()


def parse_int_filter(value: str):
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_date_filter(value: str):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


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
    }

    with engine.begin() as connection:
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE dive_logs ADD COLUMN {column_name} {column_type}")
                )


ensure_dive_log_time_columns()


@app.get("/map")
def map_page(request: Request):
    return templates.TemplateResponse(
        "index.html", {"request": request}
    )

@app.get("/point/{point_id}/logs")
def point_logs(request: Request, point_id: int):

    db = SessionLocal()
    try:
        point = db.query(DivePoint).filter(DivePoint.id == point_id).first()

        logs = (
            db.query(DiveLog)
            .filter(DiveLog.dive_point_id == point_id)
            .order_by(DiveLog.id.desc())
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
        points = db.query(DivePoint).all()

        last_log = db.query(func.max(DiveLog.id)).scalar()

        if last_log:
            next_dive_number = last_log + 1
        else:
            next_dive_number = 1

        return templates.TemplateResponse(
            "log_add.html",
            {
                "request": request,
                "points": points,
                "next_dive_number": next_dive_number
            }
        )
    finally:
        db.close()

@app.post("/log/add")
async def add_log(
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
    start_pressure: int = Form(None),
    end_pressure: int = Form(None),
    note: str = Form(None),
    image: UploadFile = File(None)
):

    db = SessionLocal()
    try:
        file_path = None

        if image and image.filename:
            suffix = Path(image.filename).suffix
            filename = f"{uuid.uuid4().hex}{suffix}"
            file_location = UPLOAD_DIR / filename

            with open(file_location, "wb") as buffer:
                shutil.copyfileobj(image.file, buffer)

            file_path = str(file_location)

        log = DiveLog(
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
            buddy=buddy,
            note=note,
            image_path=file_path,
        )

        db.add(log)
        db.commit()

        saved_log_id = log.id

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
        log = db.query(DiveLog).filter(DiveLog.id == log_id).first()

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
        log = db.query(DiveLog).filter(DiveLog.id == log_id).first()
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
        log = db.query(DiveLog).filter(DiveLog.id == log_id).first()

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

        db.commit()

        point_id = log.dive_point_id

        return RedirectResponse(
            url=f"/point/{point_id}/logs?updated=1",
            status_code=303
        )
    finally:
        db.close()

@app.post("/log/{log_id}/delete")
def delete_log(log_id: int):

    db = SessionLocal()
    try:
        log = db.query(DiveLog).filter(DiveLog.id == log_id).first()

        point_id = log.dive_point_id

        db.delete(log)
        db.commit()

        return RedirectResponse(
            url=f"/point/{point_id}/logs?deleted=1",
            status_code=303
        )
    finally:
        db.close()
    
@app.get("/stats")
def stats_page(request: Request):

    db = SessionLocal()
    try:
        total_dives = db.query(func.count(DiveLog.id)).scalar()

        max_depth = db.query(func.max(DiveLog.max_depth)).scalar()

        avg_depth = db.query(func.avg(DiveLog.avg_depth)).scalar()

        total_dive_time = db.query(func.sum(DiveLog.dive_time)).scalar()

        avg_water_temp = db.query(func.avg(DiveLog.water_temp)).scalar()

        monthly_dives = (
            db.query(
                func.strftime("%Y-%m", DiveLog.dive_date).label("month"),
                func.count(DiveLog.id).label("dive_count"),
            )
            .group_by("month")
            .order_by("month")
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
        log = db.query(DiveLog).filter(DiveLog.id == log_id).first()

        point = db.query(DivePoint).filter(DivePoint.id == log.dive_point_id).first()

        return templates.TemplateResponse(
            "log_detail.html",
            {
                "request": request,
                "log": log,
                "point": point
            }
        )
    finally:
        db.close()

@app.get("/")
def home(request: Request):

    db = SessionLocal()
    try:
        total_dives = db.query(func.count(DiveLog.id)).scalar()
        total_dive_time = db.query(func.sum(DiveLog.dive_time)).scalar()
        recent_logs = (
            db.query(DiveLog)
            .options(
                joinedload(DiveLog.dive_point)
            )
            .order_by(DiveLog.dive_date.desc(), DiveLog.id.desc())
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
        filters = {
            "dive_date": request.query_params.get("dive_date", "").strip(),
            "region_id": request.query_params.get("region_id", "").strip(),
            "area_id": request.query_params.get("area_id", "").strip(),
            "point_id": request.query_params.get("point_id", "").strip(),
            "buddy": request.query_params.get("buddy", "").strip(),
            "sort": request.query_params.get("sort", "date").strip(),
            "direction": request.query_params.get("direction", "desc").strip(),
        }

        query = (
            db.query(DiveLog)
            .options(
                joinedload(DiveLog.dive_point)
                .joinedload(DivePoint.area)
                .joinedload(Area.region)
            )
            .outerjoin(DivePoint, DiveLog.dive_point_id == DivePoint.id)
            .outerjoin(Area, DivePoint.area_id == Area.id)
            .outerjoin(Region, Area.region_id == Region.id)
        )

        dive_date_filter = parse_date_filter(filters["dive_date"])
        region_id_filter = parse_int_filter(filters["region_id"])
        area_id_filter = parse_int_filter(filters["area_id"])
        point_id_filter = parse_int_filter(filters["point_id"])

        if dive_date_filter:
            query = query.filter(DiveLog.dive_date == dive_date_filter)

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
            "region": Region.name,
            "area": Area.name,
            "point": DivePoint.name,
            "buddy": DiveLog.buddy,
            "id": DiveLog.id,
        }
        sort_column = sort_columns.get(filters["sort"], DiveLog.dive_date)
        direction = filters["direction"] if filters["direction"] in ("asc", "desc") else "desc"
        order_expression = sort_column.asc() if direction == "asc" else sort_column.desc()

        logs = (
            query
            .order_by(order_expression, DiveLog.id.desc())
            .all()
        )

        regions = db.query(Region).order_by(Region.name.asc()).all()
        areas = db.query(Area).order_by(Area.name.asc()).all()
        points = db.query(DivePoint).order_by(DivePoint.name.asc()).all()

        return templates.TemplateResponse(
            "logs.html",
            {
                "request": request,
                "logs": logs,
                "filters": filters,
                "regions": regions,
                "areas": areas,
                "points": points,
            }
        )
    finally:
        db.close()
