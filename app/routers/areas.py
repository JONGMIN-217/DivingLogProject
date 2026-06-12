from fastapi import APIRouter
from app.database import SessionLocal
from app.models import Area

router = APIRouter()


@router.get("/areas")
def get_areas(region_id: int | None = None):
    db = SessionLocal()

    if region_id:
        areas = db.query(Area).filter(Area.region_id == region_id).all()
    else:
        areas = db.query(Area).all()

    db.close()
    return areas