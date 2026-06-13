from sqlalchemy import Column, Integer, String, ForeignKey, Float, Date, Time
from datetime import date
from sqlalchemy.orm import relationship
from app.database import Base


class Region(Base):
    __tablename__ = "regions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)

class Area(Base):
    __tablename__ = "areas"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)

    region_id = Column(Integer, ForeignKey("regions.id"))

    region = relationship("Region", backref="areas")

class DivePoint(Base):
    __tablename__ = "dive_points"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)

    area_id = Column(Integer, ForeignKey("areas.id"))

    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    area = relationship("Area", backref="dive_points")
class DiveLog(Base):
    __tablename__ = "dive_logs"

    id = Column(Integer, primary_key=True, index=True)
    dive_date = Column(Date, nullable=False)

    dive_point_id = Column(Integer, ForeignKey("dive_points.id"))

    max_depth = Column(Float)
    avg_depth = Column(Float)
    entry_time = Column(Time, nullable=True)
    exit_time = Column(Time, nullable=True)
    dive_time = Column(Integer)  # 분 단위
    water_temp = Column(Float)
    visibility = Column(Integer)
    buddy = Column(String)
    start_pressure = Column(Integer)
    end_pressure = Column(Integer)
    image_path = Column(String, nullable=True)
    note = Column(String)

    dive_point = relationship("DivePoint", backref="dive_logs")
