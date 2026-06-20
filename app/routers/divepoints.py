from fastapi import APIRouter
from sqlalchemy import func
from app.database import SessionLocal
from app.models import DiveLog, DivePoint

router = APIRouter()


@router.get("/divepoints")
def get_divepoints(area_id: int | None = None):
    db = SessionLocal()
    try:
        query = (
            db.query(
                DivePoint,
                func.count(DiveLog.id).label("log_count"),
                func.max(DiveLog.dive_date).label("latest_dive_date"),
            )
            .outerjoin(DiveLog, DiveLog.dive_point_id == DivePoint.id)
            .group_by(DivePoint.id)
        )

        if area_id:
            query = query.filter(DivePoint.area_id == area_id)

        return [
            {
                "id": point.id,
                "name": point.name,
                "area_id": point.area_id,
                "region_id": point.area.region_id if point.area else None,
                "latitude": point.latitude,
                "longitude": point.longitude,
                "point_type": point.point_type or "OCEAN",
                "point_type_label": "수영장" if point.point_type == "POOL" else "해양",
                "log_count": log_count,
                "latest_dive_date": latest_dive_date,
            }
            for point, log_count, latest_dive_date in query.all()
        ]
    finally:
        db.close()
