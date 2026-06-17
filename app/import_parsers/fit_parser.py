from pathlib import Path

from .base import ImportDive, ImportParser, ImportPreview, preview_from_dives
from .utils import parse_date_value, parse_float_value, parse_int_value


class GarminFitImportParser(ImportParser):
    parser_name = "Garmin FIT"
    supported_extensions = (".fit",)

    def parse(self, path: Path, original_filename: str) -> ImportPreview:
        try:
            from fitparse import FitFile
        except ImportError:
            return preview_from_dives(
                parser_name=self.parser_name,
                source_path=path,
                original_filename=original_filename,
                dives=[],
                messages=["Garmin FIT 파싱에는 fitparse 패키지가 필요합니다. 패키지 설치 후 같은 파서로 처리할 수 있습니다."],
            )

        fit_file = FitFile(str(path))
        dives: list[ImportDive] = []

        for message in fit_file.get_messages(("session", "lap")):
            values = {
                field.name: field.value
                for field in message
            }
            latitude = _semicircles_to_degrees(values.get("start_position_lat") or values.get("position_lat"))
            longitude = _semicircles_to_degrees(values.get("start_position_long") or values.get("position_long"))
            timestamp = values.get("start_time") or values.get("timestamp")

            dives.append(
                ImportDive(
                    dive_date=parse_date_value(str(timestamp)) if timestamp else None,
                    max_depth=parse_float_value(str(values.get("max_depth") or values.get("enhanced_max_depth") or "")),
                    avg_depth=parse_float_value(str(values.get("avg_depth") or "")),
                    dive_time=parse_int_value(str(values.get("total_elapsed_time") or values.get("total_timer_time") or "")),
                    water_temp=parse_float_value(str(values.get("avg_temperature") or values.get("min_temperature") or "")),
                    latitude=latitude,
                    longitude=longitude,
                    raw={key: str(value) for key, value in values.items() if value is not None},
                )
            )

        return preview_from_dives(
            parser_name=self.parser_name,
            source_path=path,
            original_filename=original_filename,
            dives=dives,
        )


def _semicircles_to_degrees(value) -> float | None:
    if value is None:
        return None

    try:
        return float(value) * 180 / 2**31
    except (TypeError, ValueError):
        return None
