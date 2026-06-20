from sqlalchemy import Boolean, Column, Integer, String, ForeignKey, Float, Date, Time, Text
from sqlalchemy.orm import relationship
from app.database import Base


class Region(Base):
    __tablename__ = "regions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    country_id = Column(Integer, ForeignKey("countries.id"), nullable=True)

    country = relationship("Country", backref="regions")


class Country(Base):
    __tablename__ = "countries"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)

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
    point_type = Column(String, nullable=False, default="OCEAN")
    memo = Column(String, nullable=True)

    area = relationship("Area", backref="dive_points")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)


class Friend(Base):
    __tablename__ = "friends"

    id = Column(Integer, primary_key=True, index=True)
    requester_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    addressee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, default="pending", nullable=False)

    requester = relationship("User", foreign_keys=[requester_id])
    addressee = relationship("User", foreign_keys=[addressee_id])


class DiveTrip(Base):
    __tablename__ = "dive_trips"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    region = Column(String, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    owner = relationship("User", foreign_keys=[owner_id])
    participants = relationship("TripParticipant", back_populates="trip")
    photos = relationship("TripPhoto", back_populates="trip")


class TripParticipant(Base):
    __tablename__ = "trip_participants"

    id = Column(Integer, primary_key=True, index=True)
    trip_id = Column(Integer, ForeignKey("dive_trips.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    trip = relationship("DiveTrip", back_populates="participants")
    user = relationship("User")


class TripPhoto(Base):
    __tablename__ = "trip_photos"

    id = Column(Integer, primary_key=True, index=True)
    trip_id = Column(Integer, ForeignKey("dive_trips.id"), nullable=False)
    uploader_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    image_path = Column(String, nullable=False)
    caption = Column(String, nullable=True)

    trip = relationship("DiveTrip", back_populates="photos")
    uploader = relationship("User")


class MarineWeather(Base):
    __tablename__ = "marine_weather"

    id = Column(Integer, primary_key=True, index=True)
    weather_date = Column(Date, nullable=False, index=True)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False, index=True)
    water_temp = Column(Float, nullable=True)
    wave_height = Column(Float, nullable=True)
    wave_period = Column(Float, nullable=True)
    wave_direction = Column(Float, nullable=True)
    wind_speed = Column(Float, nullable=True)
    wind_direction = Column(String, nullable=True)
    tide_level = Column(String, nullable=True)
    tide_time = Column(String, nullable=True)
    current_strength = Column(String, nullable=True)
    memo = Column(String, nullable=True)

    region = relationship("Region", backref="marine_weather")


class DiveLog(Base):
    __tablename__ = "dive_logs"

    id = Column(Integer, primary_key=True, index=True)
    dive_number = Column(Integer, nullable=True, index=True)
    dive_date = Column(Date, nullable=False)

    dive_point_id = Column(Integer, ForeignKey("dive_points.id"))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    buddy_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    max_depth = Column(Float)
    avg_depth = Column(Float)
    entry_time = Column(Time, nullable=True)
    exit_time = Column(Time, nullable=True)
    dive_time = Column(Integer)  # 분 단위
    water_temp = Column(Float)
    visibility = Column(Integer)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    site_name = Column(String, nullable=True)
    buddy = Column(String)
    start_pressure = Column(Integer)
    end_pressure = Column(Integer)
    image_path = Column(String, nullable=True)
    note = Column(String)
    profile_samples = Column(Text, nullable=True)
    import_source = Column(String, nullable=True, index=True)
    import_external_id = Column(String, nullable=True, index=True)
    import_source_file_hash = Column(String, nullable=True, index=True)

    dive_point = relationship("DivePoint", backref="dive_logs")
    user = relationship("User", foreign_keys=[user_id], backref="dive_logs")
    buddy_user = relationship("User", foreign_keys=[buddy_user_id])
    profile_samples_rel = relationship(
        "DiveProfileSample",
        back_populates="dive_log",
        cascade="all, delete-orphan",
        order_by="DiveProfileSample.elapsed_seconds",
    )


class DiveProfileSample(Base):
    __tablename__ = "dive_profile_samples"

    id = Column(Integer, primary_key=True, index=True)
    dive_log_id = Column(Integer, ForeignKey("dive_logs.id"), nullable=False, index=True)
    elapsed_seconds = Column(Integer, nullable=False, index=True)
    depth = Column(Float, nullable=True)
    temperature = Column(Float, nullable=True)
    pressure = Column(Float, nullable=True)
    source = Column(String, nullable=True)

    dive_log = relationship("DiveLog", back_populates="profile_samples_rel")
