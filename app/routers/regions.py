from fastapi import APIRouter
from app.database import SessionLocal
from app.models import Region

router = APIRouter()

@router.get("/regions")
def get_regions(country_id: int | None = None):
    db = SessionLocal()
    try:
        query = db.query(Region)
        if country_id:
            query = query.filter(Region.country_id == country_id)

        return query.order_by(Region.name.asc()).all()
    finally:
        db.close()
