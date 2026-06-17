import csv
from pathlib import Path

from .base import ImportDive, ImportParser, ImportPreview, preview_from_dives
from .utils import build_note, first_value, parse_date_value, parse_float_value, parse_int_value


class CsvImportParser(ImportParser):
    parser_name = "CSV"
    supported_extensions = (".csv",)

    def parse(self, path: Path, original_filename: str) -> ImportPreview:
        messages: list[str] = []

        with path.open("r", encoding="utf-8-sig", newline="") as file:
            sample = file.read(4096)
            file.seek(0)

            try:
                dialect = csv.Sniffer().sniff(sample)
            except csv.Error:
                dialect = csv.excel
                messages.append("CSV 구분자를 자동으로 판단하지 못해 기본 쉼표 형식으로 읽었습니다.")

            reader = csv.DictReader(file, dialect=dialect)
            raw_columns = reader.fieldnames or []
            dives: list[ImportDive] = []

            for row in reader:
                normalized_row = {
                    raw_column.strip(): (row.get(raw_column) or "").strip()
                    for raw_column in raw_columns
                }
                dives.append(_row_to_dive(normalized_row))

        if not raw_columns:
            messages.append("헤더 행을 찾지 못했습니다.")

        return preview_from_dives(
            parser_name=self.parser_name,
            source_path=path,
            original_filename=original_filename,
            messages=messages,
            dives=dives,
        )


def _row_to_dive(row: dict[str, str]) -> ImportDive:
    return ImportDive(
        dive_date=parse_date_value(first_value(row, "날짜", "일자", "date", "dive_date", "datetime", "time")),
        max_depth=parse_float_value(first_value(row, "최대 수심", "최대수심", "max_depth", "max depth", "depth", "depth max")),
        avg_depth=parse_float_value(first_value(row, "평균 수심", "평균수심", "avg_depth", "average_depth", "avg depth")),
        dive_time=parse_int_value(first_value(row, "다이브타임", "다이빙 시간", "dive_time", "duration", "bottom_time", "divetime")),
        water_temp=parse_float_value(first_value(row, "수온", "water_temp", "temperature", "watertemp")),
        buddy=first_value(row, "버디", "buddy", "buddies") or None,
        note=build_note(row, "메모", "note", "notes", "comment", "description"),
        latitude=parse_float_value(first_value(row, "위도", "latitude", "lat", "gps_latitude")),
        longitude=parse_float_value(first_value(row, "경도", "longitude", "lon", "lng", "gps_longitude")),
        raw=row,
    )
