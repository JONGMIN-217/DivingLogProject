from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import or_
from app.database import SessionLocal
from app.models import DiveLog, Friend
from app.schemas import DiveLogCreate

router = APIRouter()

@router.get("/divelogs")
def get_divelogs(request: Request, point_id: int | None = None):
    db = SessionLocal()
    try:
        user_id = request.session.get("user_id")
        visible_logs = (
            or_(DiveLog.user_id == user_id, DiveLog.user_id.is_(None))
            if user_id
            else DiveLog.user_id.is_(None)
        )
        query = db.query(DiveLog).filter(visible_logs)

        if point_id:
            return query.filter(DiveLog.dive_point_id == point_id).all()

        return query.all()
    finally:
        db.close()

@router.post("/divelogs")
def create_divelog(request: Request, log: DiveLogCreate):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    db = SessionLocal()
    try:
        payload = log.dict()
        buddy_user_id = payload.get("buddy_user_id")
        if buddy_user_id:
            friendship = (
                db.query(Friend)
                .filter(
                    Friend.status == "accepted",
                    or_(
                        (Friend.requester_id == user_id) & (Friend.addressee_id == buddy_user_id),
                        (Friend.requester_id == buddy_user_id) & (Friend.addressee_id == user_id),
                    ),
                )
                .first()
            )
            if not friendship:
                raise HTTPException(status_code=400, detail="친구만 버디로 선택할 수 있습니다.")

        new_log = DiveLog(**payload, user_id=user_id)

        db.add(new_log)
        db.commit()
        db.refresh(new_log)

        return new_log
    finally:
        db.close()
