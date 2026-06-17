from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path


@dataclass
class DivePointSuggestion:
    point_id: int
    point_name: str
    distance_km: float


@dataclass
class ImportDive:
    source: str = ""
    source_file: str = ""
    external_id: str | None = None
    dive_date: date | None = None
    entry_time: time | None = None
    exit_time: time | None = None
    dive_time: int | None = None
    max_depth: float | None = None
    avg_depth: float | None = None
    water_temp: float | None = None
    start_pressure: int | None = None
    end_pressure: int | None = None
    buddy: str | None = None
    note: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    site_name: str | None = None
    suggested_point_id: int | None = None
    confidence: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    raw: dict[str, str] = field(default_factory=dict)
    suggested_point: DivePointSuggestion | None = None
    is_new_point_candidate: bool = False
    candidate_country: str = ""
    candidate_region: str = ""
    candidate_area: str = ""
    candidate_point_name: str = ""

    @property
    def source_label(self) -> str | None:
        return self.site_name

    @source_label.setter
    def source_label(self, value: str | None):
        self.site_name = value

    def preview_row(self) -> dict[str, str]:
        return {
            "날짜": self.dive_date.isoformat() if self.dive_date else "",
            "입수시각": self.entry_time.strftime("%H:%M") if self.entry_time else "",
            "출수시각": self.exit_time.strftime("%H:%M") if self.exit_time else "",
            "최대 수심": _format_number(self.max_depth),
            "평균 수심": _format_number(self.avg_depth),
            "다이브타임": str(self.dive_time) if self.dive_time is not None else "",
            "수온": _format_number(self.water_temp),
            "시작 압력": str(self.start_pressure) if self.start_pressure is not None else "",
            "종료 압력": str(self.end_pressure) if self.end_pressure is not None else "",
            "버디": self.buddy or "",
            "위도": _format_number(self.latitude, precision=6),
            "경도": _format_number(self.longitude, precision=6),
            "사이트명": self.site_name or "",
            "신뢰도": _format_confidence(self.confidence),
            "메모": self.note or "",
            "경고": " / ".join(self.warnings),
        }


@dataclass
class ImportParserResult:
    source: str
    source_file: str
    dives: list[ImportDive] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    db_tables: list[dict[str, object]] = field(default_factory=list)


def _format_number(value: float | None, precision: int = 1) -> str:
    if value is None:
        return ""

    formatted = f"{value:.{precision}f}"
    return formatted.rstrip("0").rstrip(".")


def _format_confidence(confidence: dict[str, str]) -> str:
    if not confidence:
        return ""

    return " / ".join(
        f"{key}:{value}"
        for key, value in confidence.items()
    )
