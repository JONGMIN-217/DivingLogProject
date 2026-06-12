from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy import func

from datetime import date

from app.database import SessionLocal
from app.models import DivePoint, DiveLog

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


@app.get("/map")
def map_page(request: Request):
    return templates.TemplateResponse(
        "index.html", {"request": request}
    )

@app.get("/point/{point_id}/logs")
def point_logs(request: Request, point_id: int):

    db = SessionLocal()

    point = db.query(DivePoint).filter(DivePoint.id == point_id).first()

    logs = (
        db.query(DiveLog)
        .filter(DiveLog.dive_point_id == point_id)
        .order_by(DiveLog.id.desc())
        .all()
    )

    db.close()

    return templates.TemplateResponse(
        "point_logs.html",
        {
            "request": request,
            "point": point,
            "logs": logs
        }
    )

@app.get("/logs")
def all_logs(request: Request):

    db = SessionLocal()

    logs = (
        db.query(DiveLog)
        .order_by(DiveLog.id.desc())
        .all()
    )

    return templates.TemplateResponse(
        "all_logs.html",
        {
            "request": request,
            "logs": logs
        }
    )

@app.get("/log/add")
def log_add_page(request: Request):

    db = SessionLocal()

    points = db.query(DivePoint).all()

    last_log = db.query(func.max(DiveLog.id)).scalar()

    if last_log:
        next_dive_number = last_log + 1
    else:
        next_dive_number = 1

    db.close()

    return templates.TemplateResponse(
        "log_add.html",
        {
            "request": request,
            "points": points,
            "next_dive_number": next_dive_number
        }
    )

# New POST API for saving logs
@app.post("/log/add")
async def add_log(
    dive_date: str = Form(...),
    dive_point_id: int = Form(...),
    max_depth: float = Form(None),
    avg_depth: float = Form(None),
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

    db.close()

    return RedirectResponse(
    url=f"/log/success/{saved_log_id}",
    status_code=303
    )

@app.get("/log/success/{log_id}")
def log_success(request: Request, log_id: int):

    db = SessionLocal()

    log = db.query(DiveLog).filter(DiveLog.id == log_id).first()

    db.close()

    return templates.TemplateResponse(
        "log_success.html",
        {
            "request": request,
            "log": log
        }
    )

@app.get("/log/{log_id}/edit")
def edit_log_page(request: Request, log_id: int):

    db = SessionLocal()

    log = db.query(DiveLog).filter(DiveLog.id == log_id).first()
    points = db.query(DivePoint).all()

    db.close()

    return templates.TemplateResponse(
        "log_edit.html",
        {
            "request": request,
            "log": log,
            "points": points
        }
    )

@app.post("/log/{log_id}/edit")
def edit_log(
    log_id: int,
    dive_point_id: int = Form(...),
    dive_date: str = Form(...),
    max_depth: float = Form(...),
    avg_depth: float = Form(...),
    dive_time: int = Form(...),
    water_temp: float = Form(...),
    visibility: float = Form(...),
    note: str = Form("")
):

    db = SessionLocal()

    log = db.query(DiveLog).filter(DiveLog.id == log_id).first()

    log.dive_point_id = dive_point_id
    log.dive_date = date.fromisoformat(dive_date)
    log.max_depth = max_depth
    log.avg_depth = avg_depth
    log.dive_time = dive_time
    log.water_temp = water_temp
    log.visibility = visibility
    log.note = note

    db.commit()
    db.commit()

    point_id = log.dive_point_id

    db.close()

    return RedirectResponse(
    url=f"/point/{point_id}/logs?updated=1",
    status_code=303
)

@app.post("/log/{log_id}/delete")
def delete_log(log_id: int):

    db = SessionLocal()

    log = db.query(DiveLog).filter(DiveLog.id == log_id).first()

    point_id = log.dive_point_id

    db.delete(log)
    db.commit()
    db.close()

    return RedirectResponse(
        url=f"/point/{point_id}/logs?deleted=1",
        status_code=303
    )
    
@app.get("/stats")
def stats_page(request: Request):

    db = SessionLocal()

    total_dives = db.query(func.count(DiveLog.id)).scalar()

    max_depth = db.query(func.max(DiveLog.max_depth)).scalar()

    avg_depth = db.query(func.avg(DiveLog.avg_depth)).scalar()

    total_dive_time = db.query(func.sum(DiveLog.dive_time)).scalar()

    db.close()

    return templates.TemplateResponse(
        "stats.html",
        {
            "request": request,
            "total_dives": total_dives,
            "max_depth": max_depth,
            "avg_depth": avg_depth,
            "total_dive_time": total_dive_time
        }
    )
@app.get("/log/{log_id}")
def log_detail(request: Request, log_id: int):

    db = SessionLocal()

    log = db.query(DiveLog).filter(DiveLog.id == log_id).first()

    point = db.query(DivePoint).filter(DivePoint.id == log.dive_point_id).first()

    db.close()

    return templates.TemplateResponse(
        "log_detail.html",
        {
            "request": request,
            "log": log,
            "point": point
        }
    )

@app.get("/")
def home(request: Request):

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request
        }
    )

@app.get("/logs")
def all_logs(request: Request):

    db = SessionLocal()

    logs = (
        db.query(DiveLog)
        .order_by(DiveLog.id.desc())
        .all()
    )

    db.close()

    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "logs": logs
        }
    )
