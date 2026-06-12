from datetime import date
from app.database import SessionLocal
from app.models import Region, Area, DivePoint, DiveLog


def seed():
    db = SessionLocal()

    # Region
    jeju = Region(name="제주 서귀포")
    east = Region(name="동해 강릉")

    db.add_all([jeju, east])
    db.commit()

    # Area
    munseom = Area(name="문섬", region=jeju)
    beomseom = Area(name="범섬", region=jeju)
    sacheonjin = Area(name="사천진", region=east)

    db.add_all([munseom, beomseom, sacheonjin])
    db.commit()

    # DivePoint
    point1 = DivePoint(name="한개창", area=munseom)
    point2 = DivePoint(name="새끼섬", area=munseom)
    point3 = DivePoint(name="사천진포인트", area=sacheonjin)

    db.add_all([point1, point2, point3])
    db.commit()

    # DiveLog
    log1 = DiveLog(
        dive_date=date(2026, 3, 5),
        dive_point=point1,
        max_depth=28.5,
        avg_depth=18.2,
        dive_time=42,
        water_temp=15.5,
        visibility=10.0,
        note="조류 약함, 시야 양호"
    )

    db.add(log1)
    db.commit()

    db.close()
    print("Seed 데이터 입력 완료")


if __name__ == "__main__":
    seed()