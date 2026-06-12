from fastapi import APIRouter
from app.database import SessionLocal
from app.models import DiveLog
from app.schemas import DiveLogCreate

router = APIRouter()

@router.get("/divelogs")
def get_divelogs(point_id: int | None = None):
    db = SessionLocal()

    if point_id:
        logs = db.query(DiveLog).filter(DiveLog.dive_point_id == point_id).all()
    else:
        logs = db.query(DiveLog).all()

    db.close()
    return logs

@router.post("/divelogs")
def create_divelog(log: DiveLogCreate):
    db = SessionLocal()

    new_log = DiveLog(**log.dict())

    db.add(new_log)
    db.commit()
    db.refresh(new_log)
    db.close()

    return new_log