from app.database import engine, Base
from app.models import Region, Area, DivePoint, DiveLog


def init():
    Base.metadata.create_all(bind=engine)
    print("DB 생성 완료")


if __name__ == "__main__":
    init()