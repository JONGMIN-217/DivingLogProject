from fastapi import APIRouter
from app.database import SessionLocal
from app.models import Region

router = APIRouter()

@router.get("/regions")
def get_regions():
    db = SessionLocal()
    regions = db.query(Region).all()
    db.close()
    return regions