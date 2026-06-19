from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app.importers.common import DivePointSuggestion, ImportDive


@dataclass
class ImportPreview:
    parser_name: str
    source_path: Path
    original_filename: str
    columns: list[str]
    rows: list[dict[str, str]]
    messages: list[str] = field(default_factory=list)
    dives: list[ImportDive] = field(default_factory=list)
    db_tables: list[dict[str, object]] = field(default_factory=list)


class ImportParser:
    parser_name = "알 수 없음"
    supported_extensions: tuple[str, ...] = ()

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in self.supported_extensions

    def parse(self, path: Path, original_filename: str) -> ImportPreview:
        raise NotImplementedError

    def parse_dives(self, path: Path, original_filename: str) -> list[ImportDive]:
        return self.parse(path, original_filename).dives


class UnsupportedImportFormat(ValueError):
    pass


def preview_from_dives(
    parser_name: str,
    source_path: Path,
    original_filename: str,
    dives: list[ImportDive],
    messages: list[str] | None = None,
) -> ImportPreview:
    columns = [
        "날짜",
        "입수시각",
        "출수시각",
        "최대 수심",
        "평균 수심",
        "다이브타임",
        "수온",
        "시작 압력",
        "종료 압력",
        "버디",
        "위도",
        "경도",
        "사이트명",
        "값 출처",
        "메모",
        "경고",
    ]
    for dive in dives:
        if not dive.source:
            dive.source = parser_name
        if not dive.source_file:
            dive.source_file = original_filename
        _apply_auto_time_calculation(dive)
    rows = [dive.preview_row() for dive in dives]
    preview_messages = list(messages or [])

    if not dives:
        preview_messages.append("미리보기로 표시할 다이빙 항목을 찾지 못했습니다.")

    return ImportPreview(
        parser_name=parser_name,
        source_path=source_path,
        original_filename=original_filename,
        columns=columns,
        rows=rows,
        messages=preview_messages,
        dives=dives,
    )


def _apply_auto_time_calculation(dive: ImportDive):
    if dive.exit_time is None and dive.entry_time is not None and dive.dive_time is not None:
        base = datetime.combine(datetime.today().date(), dive.entry_time)
        dive.exit_time = (base + timedelta(minutes=dive.dive_time)).time().replace(microsecond=0)
        _append_warning_once(dive, "출수시각 자동 계산")

    if dive.dive_time is None and dive.entry_time is not None and dive.exit_time is not None:
        entry_dt = datetime.combine(datetime.today().date(), dive.entry_time)
        exit_dt = datetime.combine(datetime.today().date(), dive.exit_time)
        if exit_dt < entry_dt:
            exit_dt += timedelta(days=1)
        dive.dive_time = max(int(round((exit_dt - entry_dt).total_seconds() / 60)), 0)
        _append_warning_once(dive, "다이브타임 자동 계산")


def _append_warning_once(dive: ImportDive, warning: str):
    if warning not in dive.warnings:
        dive.warnings.append(warning)
