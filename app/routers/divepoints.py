from fastapi import APIRouter
from app.database import SessionLocal
from app.models import DivePoint

router = APIRouter()


@router.get("/divepoints")
def get_divepoints(area_id: int | None = None):
    db = SessionLocal()

    if area_id:
        points = db.query(DivePoint).filter(DivePoint.area_id == area_id).all()
    else:
        points = db.query(DivePoint).all()

    db.close()
    return points