from pydantic import BaseModel
from datetime import date, time


class DiveLogCreate(BaseModel):
    dive_date: date
    dive_point_id: int
    max_depth: float
    avg_depth: float
    entry_time: time | None = None
    exit_time: time | None = None
    dive_time: int
    water_temp: float
    visibility: float
    buddy: str | None = None
    buddy_user_id: int | None = None
    note: str | None = None
