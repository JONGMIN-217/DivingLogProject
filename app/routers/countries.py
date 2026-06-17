from fastapi import APIRouter
from app.database import SessionLocal
from app.models import Country

router = APIRouter()


@router.get("/countries")
def get_countries():
    db = SessionLocal()
    try:
        return db.query(Country).order_by(Country.name.asc()).all()
    finally:
        db.close()
