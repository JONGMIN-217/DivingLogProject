from fastapi import APIRouter
from app.database import SessionLocal
from app.models import Area

router = APIRouter()


@router.get("/areas")
def get_areas(region_id: int | None = None):
    db = SessionLocal()
    try:
        if region_id:
            return db.query(Area).filter(Area.region_id == region_id).all()

        return db.query(Area).all()
    finally:
        db.close()
