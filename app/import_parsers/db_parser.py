from abc import abstractmethod
from datetime import datetime, timedelta
import json
import logging
import re
import sqlite3
from pathlib import Path

from app.importers.common import ImportDive
from app.importers.shearwater_profile_parser import parse_shearwater_data_bytes_1

from .base import ImportParser, ImportPreview, preview_from_dives
from .utils import parse_date_value, parse_float_value, parse_int_value, parse_time_value


SQLITE_HEADER = b"SQLite format 3\x00"
logger = logging.getLogger(__name__)

SHEARWATER_DIVE_DETAILS_FIELDS = {
    "DiveId",
    "DiveDate",
    "DiveLengthTime",
    "Depth",
    "AverageDepth",
    "MinTemp",
    "AverageTemp",
    "Location",
    "Site",
    "Buddy",
    "DiveNumber",
    "Notes",
    "GnssEntryLocation",
    "GnssExitLocation",
    "Tank1PressureStart",
    "Tank1PressureEnd",
}
SHEARWATER_CALCULATED_FIELDS = {
    "calculated_values_from_samples",
    "log_id",
    "dive_id",
    "DiveId",
    "file_name",
}
SHEARWATER_LOG_MATCH_KEYS = (
    "dive_id",
    "DiveId",
    "diveid",
    "log_id",
    "logId",
    "logid",
    "id",
    "file_name",
    "FileName",
    "filename",
)


class DiveComputerDbImportParser(ImportParser):
    parser_name = "Dive Computer DB"
    supported_extensions = (".db", ".sqlite", ".sqlite3")

    def parse(self, path: Path, original_filename: str) -> ImportPreview:
        if not _has_sqlite_header(path):
            return ImportPreview(
                parser_name=self.parser_name,
                source_path=path,
                original_filename=original_filename,
                columns=[],
                rows=[],
                messages=["SQLite DB 파일이 아닙니다. 제조사별 전용 파서가 필요할 수 있습니다."],
            )

        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
        except sqlite3.Error:
            return ImportPreview(
                parser_name=self.parser_name,
                source_path=path,
                original_filename=original_filename,
                columns=[],
                rows=[],
                messages=["SQLite DB 파일을 열 수 없습니다."],
            )

        try:
            tables = _read_table_previews(connection)
        finally:
            connection.close()

        messages = [
            "SQLite DB로 확인되었습니다.",
            "제조사와 앱마다 테이블 구조가 달라 자동 저장하지 않습니다.",
            "아래 테이블 목록을 확인한 뒤, 추후 전용 파서에서 사용할 테이블을 결정하세요.",
        ]
        if not tables:
            messages.append("읽을 수 있는 사용자 테이블을 찾지 못했습니다.")

        return ImportPreview(
            parser_name=self.parser_name,
            source_path=path,
            original_filename=original_filename,
            columns=["테이블명", "행 수", "컬럼"],
            rows=[
                {
                    "테이블명": table["name"],
                    "행 수": str(table["row_count"]),
                    "컬럼": ", ".join(table["columns"]),
                }
                for table in tables
            ],
            messages=messages,
            db_tables=tables,
        )


class VendorSpecificDbImportParser(ImportParser):
    supported_extensions = (".db", ".sqlite", ".sqlite3")

    def can_parse(self, path: Path) -> bool:
        return super().can_parse(path) and _has_sqlite_header(path) and self.matches_schema(path)

    def parse(self, path: Path, original_filename: str) -> ImportPreview:
        return preview_from_dives(
            parser_name=self.parser_name,
            source_path=path,
            original_filename=original_filename,
            dives=self.parse_dives(path, original_filename),
        )

    @abstractmethod
    def matches_schema(self, path: Path) -> bool:
        raise NotImplementedError

    @abstractmethod
    def parse_dives(self, path: Path, original_filename: str) -> list[ImportDive]:
        raise NotImplementedError


class ShearwaterDbImportParser(VendorSpecificDbImportParser):
    parser_name = "Shearwater DB"

    def matches_schema(self, path: Path) -> bool:
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
        except sqlite3.Error:
            return False

        try:
            candidates = _candidate_log_tables(connection)
            return bool(candidates and candidates[0]["score"] >= 10)
        finally:
            connection.close()

    def parse_dives(self, path: Path, original_filename: str) -> list[ImportDive]:
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
        except sqlite3.Error:
            return []

        try:
            dives, _stats = _parse_shearwater_dives(connection, original_filename, self.parser_name)
            return dives
        except sqlite3.Error:
            logger.exception("Shearwater DB 파싱 실패")
            return []
        finally:
            connection.close()

    def parse(self, path: Path, original_filename: str) -> ImportPreview:
        if not _has_sqlite_header(path):
            return ImportPreview(
                parser_name=self.parser_name,
                source_path=path,
                original_filename=original_filename,
                columns=[],
                rows=[],
                messages=["SQLite DB 파일이 아닙니다."],
            )

        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
        except sqlite3.Error:
            return ImportPreview(
                parser_name=self.parser_name,
                source_path=path,
                original_filename=original_filename,
                columns=[],
                rows=[],
                messages=["SQLite DB 파일을 열 수 없습니다."],
            )

        try:
            candidates = _candidate_log_tables(connection)
            tables = _read_table_previews(connection)
            table = candidates[0] if candidates else None
            mapping, mapping_warnings = _map_columns(table["columns"]) if table else ({}, [])
            dives, stats = _parse_shearwater_dives(connection, original_filename, self.parser_name)
            diagnostics = _build_shearwater_diagnostics(tables, candidates, mapping, mapping_warnings, stats)
        finally:
            connection.close()

        if not candidates or candidates[0]["score"] < 10:
            return ImportPreview(
                parser_name=self.parser_name,
                source_path=path,
                original_filename=original_filename,
                columns=["테이블명", "행 수", "컬럼"],
                rows=[
                    {
                        "테이블명": table["name"],
                        "행 수": str(table["row_count"]),
                        "컬럼": ", ".join(table["columns"]),
                    }
                    for table in tables
                ],
                messages=[
                    "SQLite DB로 확인되었지만 로그 테이블을 확정하지 못했습니다.",
                    "테이블과 컬럼 진단 정보를 확인하세요.",
                    *diagnostics,
                ],
                db_tables=tables,
            )

        messages = [
            f"로그 후보 테이블로 '{candidates[0]['name']}'을 사용했습니다.",
            "테이블명보다 실제 행 수와 컬럼 구성을 우선해서 로그 테이블을 선택했습니다.",
            *diagnostics,
        ]
        if not dives:
            messages.append("후보 테이블에서 저장 가능한 로그 값을 찾지 못했습니다.")

        return preview_from_dives(
            parser_name=self.parser_name,
            source_path=path,
            original_filename=original_filename,
            dives=dives,
            messages=messages,
        )


def _has_sqlite_header(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            return file.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError:
        return False


def _read_table_previews(connection) -> list[dict[str, object]]:
    tables = [
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]

    previews: list[dict[str, object]] = []
    for table in tables:
        columns = [
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
        ]
        row_count = connection.execute(f"SELECT COUNT(*) AS count FROM {_quote_identifier(table)}").fetchone()["count"]
        sample_rows = [
            {
                key: _stringify_value(row[key])
                for key in row.keys()
            }
            for row in connection.execute(f"SELECT * FROM {_quote_identifier(table)} LIMIT 5").fetchall()
        ]
        previews.append(
            {
                "name": table,
                "columns": columns,
                "row_count": row_count,
                "sample_rows": sample_rows,
            }
        )

    return previews


def _candidate_log_tables(connection) -> list[dict[str, object]]:
    tables = _read_table_previews(connection)
    candidates = []
    for table in tables:
        normalized_table = _normalize_key(table["name"])
        columns = table["columns"]
        normalized_columns = {_normalize_key(column) for column in columns}
        score = 0

        if table["row_count"] > 0:
            score += 8
        else:
            score -= 20

        shearwater_matches = {
            _normalize_key(field)
            for field in SHEARWATER_DIVE_DETAILS_FIELDS
        } & normalized_columns
        calculated_matches = {
            _normalize_key(field)
            for field in SHEARWATER_CALCULATED_FIELDS
        } & normalized_columns
        score += len(shearwater_matches) * 2
        score += len(calculated_matches) * 2

        if {"diveid", "divedate", "divelengthtime", "depth"} <= normalized_columns:
            score += 12
        if {"logid", "calculatedvaluesfromsamples"} <= normalized_columns:
            score += 8
        if "tankprofiledata" in normalized_columns:
            score += 3
        if "calculatedvaluesfromsamples" in normalized_columns:
            score += 6
        if "databytes1" in normalized_columns or "data1" in normalized_columns:
            score += 4
        if normalized_columns & {"gnssentrylocation", "gnssexitlocation"}:
            score += 2

        if normalized_table == "divedetails":
            score += 2
        elif "divedetail" in normalized_table:
            score += 1
        elif normalized_table == "logdata":
            score += 1

        if table["row_count"] == 0:
            score -= len(normalized_columns & {"currentdepth", "currenttime", "rawbytes"})

        if score > 0:
            candidates.append({**table, "score": score})

    return sorted(candidates, key=lambda item: (item["score"], item["row_count"]), reverse=True)


def _parse_shearwater_dives(connection, original_filename: str, parser_name: str) -> tuple[list[ImportDive], dict[str, object]]:
    candidates = _candidate_log_tables(connection)
    stats = {
        "selected_table": candidates[0]["name"] if candidates else "",
        "log_data_match_count": 0,
        "calculated_parse_success_count": 0,
        "calculated_parse_failure_count": 0,
        "calculated_keys": set(),
        "profile_decompress_success_count": 0,
        "profile_parse_success_count": 0,
        "profile_parse_failure_count": 0,
    }
    if not candidates:
        return [], stats

    table = candidates[0]
    mapping, mapping_warnings = _map_columns(table["columns"])
    log_data = _read_log_data_calculated_values(connection)
    stats["calculated_parse_success_count"] = log_data["parse_success_count"]
    stats["calculated_parse_failure_count"] = log_data["parse_failure_count"]
    stats["calculated_keys"] = set(log_data["keys"])
    stats["profile_decompress_success_count"] = log_data["profile_decompress_success_count"]
    stats["profile_parse_success_count"] = log_data["profile_parse_success_count"]
    stats["profile_parse_failure_count"] = log_data["profile_parse_failure_count"]

    rows = connection.execute(
        f"SELECT * FROM {_quote_identifier(table['name'])}"
    ).fetchall()
    dives: list[ImportDive] = []
    for row in rows:
        direct_calculated, direct_warning = _calculated_values_from_row(row)
        matched_calculated, matched_warning = _match_calculated_values(row, mapping, log_data)
        matched_profile = _match_profile_values(row, mapping, log_data)
        calculated = {**matched_calculated, **direct_calculated}
        calculated_warning = " / ".join(
            warning for warning in (matched_warning, direct_warning) if warning
        )
        if calculated:
            stats["log_data_match_count"] += 1
        try:
            dive = _row_to_import_dive(
                row,
                mapping,
                mapping_warnings,
                original_filename,
                parser_name,
                table["name"],
                calculated,
                calculated_warning,
                matched_profile,
            )
        except Exception:
            logger.exception("Shearwater DB 행 매핑 실패: table=%s", table["name"])
            continue
        if dive.dive_date or dive.dive_time or dive.max_depth is not None:
            dives.append(dive)

    return dives, stats


def _read_log_data_calculated_values(connection) -> dict[str, object]:
    empty = {
        "by_key": {},
        "parse_success_count": 0,
        "parse_failure_count": 0,
        "keys": set(),
        "profile_decompress_success_count": 0,
        "profile_parse_success_count": 0,
        "profile_parse_failure_count": 0,
    }
    by_key: dict[str, dict[str, object]] = {}
    parse_success_count = 0
    parse_failure_count = 0
    keys: set[str] = set()
    profile_decompress_success_count = 0
    profile_parse_success_count = 0
    profile_parse_failure_count = 0

    for table_name in _calculated_value_table_names(connection):
        rows = connection.execute(f"SELECT * FROM {_quote_identifier(table_name)}").fetchall()
        for row in rows:
            parsed, warning = _calculated_values_from_row(row)
            profile_result = _profile_values_from_row(row)
            if parsed:
                parse_success_count += 1
                keys.update(parsed.keys())
            elif warning:
                parse_failure_count += 1
            if profile_result.get("decompressed"):
                profile_decompress_success_count += 1
            if profile_result.get("samples"):
                profile_parse_success_count += 1
            elif profile_result.get("warning"):
                profile_parse_failure_count += 1

            entry = {
                "values": parsed,
                "warning": warning,
                "profile_samples": profile_result.get("samples") or [],
                "profile_warning": profile_result.get("warning") or "",
                "profile_diagnostic": profile_result.get("diagnostic") or "",
            }
            for key_name in SHEARWATER_LOG_MATCH_KEYS:
                key_value = _row_value_by_normalized(row, key_name)
                key_text = _stringify_value(key_value).strip()
                if key_text:
                    by_key[key_text] = entry

    return {
        "by_key": by_key,
        "parse_success_count": parse_success_count,
        "parse_failure_count": parse_failure_count,
        "keys": keys,
        "profile_decompress_success_count": profile_decompress_success_count,
        "profile_parse_success_count": profile_parse_success_count,
        "profile_parse_failure_count": profile_parse_failure_count,
    }


def _calculated_value_table_names(connection) -> list[str]:
    table_names = [
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    result = []
    for table_name in table_names:
        columns = [
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table_name)})").fetchall()
        ]
        normalized_columns = {_normalize_key(column) for column in columns}
        if (
            "calculatedvaluesfromsamples" in normalized_columns
            or "databytes1" in normalized_columns
            or _normalize_key(table_name) == "logdata"
        ):
            result.append(table_name)
    return result


def _profile_values_from_row(row) -> dict[str, object]:
    value = _row_value_by_normalized(row, "data_bytes_1")
    if value is None:
        value = _row_value_by_normalized(row, "dataBytes1")
    if value is None:
        value = _row_value_by_normalized(row, "data1")
    if value is None:
        return {}

    summary = _summary_values_from_row(row)
    result = parse_shearwater_data_bytes_1(
        value,
        max_depth=summary.get("max_depth"),
        avg_depth=summary.get("avg_depth"),
        duration_seconds=summary.get("duration_seconds"),
    )
    diagnostic = (
        f"data_bytes_1 raw {result.raw_length}바이트, payload {result.payload_length}바이트, "
        f"압축 해제 {'성공' if result.decompressed else '실패'}, "
        f"해제 후 {result.decompressed_length}바이트, "
        f"파서 {result.parser or '없음'}, 샘플 {len(result.samples)}개"
    )
    return {
        "samples": result.samples,
        "warning": result.warning,
        "diagnostic": diagnostic,
        "decompressed": result.decompressed,
    }


def _summary_values_from_row(row) -> dict[str, object]:
    calculated_values, _ = _calculated_values_from_row(row)
    value = _row_value_by_normalized(row, "data_bytes_3")
    parsed = {}
    if value is not None:
        try:
            parsed = json.loads(_bytes_or_text(value))
        except (TypeError, ValueError, UnicodeDecodeError):
            parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    return {
        "max_depth": parse_float_value(_stringify_value(parsed.get("MaxDepth"))),
        "avg_depth": (
            parse_float_value(_stringify_value(calculated_values.get("AverageDepth")))
            or parse_float_value(_stringify_value(parsed.get("AverageDepth")))
        ),
        "duration_seconds": parse_int_value(_stringify_value(parsed.get("DiveTimeInSeconds"))),
    }


def _bytes_or_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8")
    return _stringify_value(value)


def _calculated_values_from_row(row) -> tuple[dict[str, object], str]:
    value = _row_value_by_normalized(row, "calculated_values_from_samples")
    if value is None:
        return {}, ""
    text = _stringify_value(value).strip()
    if not text:
        return {}, ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}, "calculated_values_from_samples JSON 파싱 실패"
    if not isinstance(parsed, dict):
        return {}, "calculated_values_from_samples 형식이 key-value 객체가 아닙니다."
    return parsed, ""


def _match_calculated_values(row, mapping: dict[str, str], log_data: dict[str, object]) -> tuple[dict[str, object], str]:
    by_key = log_data.get("by_key") or {}
    keys = [
        _row_value(row, mapping.get("external_id")),
        _row_value(row, mapping.get("file_name")),
        *(_row_value_by_normalized(row, key_name) for key_name in SHEARWATER_LOG_MATCH_KEYS),
    ]
    for key in keys:
        text = _stringify_value(key).strip()
        if text and text in by_key:
            entry = by_key[text]
            return entry.get("values") or {}, entry.get("warning") or ""
    return {}, ""


def _match_profile_values(row, mapping: dict[str, str], log_data: dict[str, object]) -> dict[str, object]:
    by_key = log_data.get("by_key") or {}
    keys = [
        _row_value(row, mapping.get("external_id")),
        _row_value(row, mapping.get("file_name")),
        *(_row_value_by_normalized(row, key_name) for key_name in SHEARWATER_LOG_MATCH_KEYS),
    ]
    for key in keys:
        text = _stringify_value(key).strip()
        if text and text in by_key:
            entry = by_key[text]
            return {
                "samples": entry.get("profile_samples") or [],
                "warning": entry.get("profile_warning") or "",
                "diagnostic": entry.get("profile_diagnostic") or "",
            }
    return {}


def _map_columns(columns: list[str]) -> tuple[dict[str, str], list[str]]:
    mapping: dict[str, str] = {}
    warnings: list[str] = []
    column_set = set(columns)
    if SHEARWATER_DIVE_DETAILS_FIELDS & column_set:
        exact_mapping = {
            "external_id": "DiveId",
            "file_name": "FileName",
            "dive_date": "DiveDate",
            "entry_time": "DiveDate",
            "dive_time": "DiveLengthTime",
            "max_depth": "Depth",
            "avg_depth": "AverageDepth",
            "min_temp": "MinTemp",
            "average_temp": "AverageTemp",
            "max_temp": "MaxTemp",
            "location": "Location",
            "site": "Site",
            "buddy": "Buddy",
            "source_dive_number": "DiveNumber",
            "note": "Notes",
            "entry_gps": "GnssEntryLocation",
            "exit_gps": "GnssExitLocation",
            "start_pressure": "Tank1PressureStart",
            "end_pressure": "Tank1PressureEnd",
            "tank_profile_data": "TankProfileData",
        }
        return {
            field: column
            for field, column in exact_mapping.items()
            if column in column_set
        }, warnings

    aliases = {
        "dive_date": {"date", "divedate", "datetime", "timestamp", "start", "divestart", "divestarttime", "dive_start_time"},
        "entry_time": {"start", "starttime", "divestart", "divestarttime", "dive_start_time"},
        "exit_time": {"end", "endtime", "diveend", "diveendtime", "dive_end_time"},
        "dive_time": {"duration", "runtime", "divetime", "bottomtime", "divelengthtime"},
        "max_depth": {"maxdepth", "max_depth", "greatestdepth", "depth"},
        "avg_depth": {"avgdepth", "avg_depth", "averagedepth", "average_depth"},
        "water_temp": {"temp", "temperature", "watertemp", "water_temp", "lowesttemperature", "mintemp", "averagetemp"},
        "min_temp": {"mintemp"},
        "average_temp": {"averagetemp"},
        "max_temp": {"maxtemp"},
        "buddy": {"buddy", "buddies"},
        "note": {"note", "notes", "memo", "comment", "comments"},
        "start_pressure": {"pressurestart", "pressure_start", "tank1pressurestart", "startpressure"},
        "end_pressure": {"pressureend", "pressure_end", "tank1pressureend", "endpressure"},
        "latitude": {"latitude", "lat", "gpslat", "gnsslat"},
        "longitude": {"longitude", "lon", "lng", "gpslon", "gpslng", "gnsslon", "gnsslng"},
        "gps": {"gps", "gnss", "location", "gnssentrylocation", "gnssexitlocation"},
        "entry_gps": {"gnssentrylocation"},
        "exit_gps": {"gnssexitlocation"},
        "site_name": {"site", "sitename", "divesite", "locationname", "location"},
        "location": {"location"},
        "site": {"site"},
        "external_id": {"id", "uuid", "diveid", "logid"},
        "source_dive_number": {"divenumber"},
        "tank_profile_data": {"tankprofiledata"},
    }

    normalized_to_original = {_normalize_key(column): column for column in columns}
    for field, names in aliases.items():
        matches = [
            normalized_to_original[name]
            for name in {_normalize_key(name) for name in names}
            if name in normalized_to_original
        ]
        if len(matches) == 1:
            mapping[field] = matches[0]
        elif len(matches) > 1:
            mapping[field] = matches[0]
            warnings.append(f"{field} 컬럼 후보가 여러 개라 확인 필요")

    return mapping, warnings


def _row_to_import_dive(
    row,
    mapping: dict[str, str],
    mapping_warnings: list[str],
    original_filename: str,
    parser_name: str,
    table_name: str,
    calculated_values: dict[str, object] | None = None,
    calculated_warning: str = "",
    profile_values: dict[str, object] | None = None,
):
    warnings = list(mapping_warnings)
    confidence = {}
    calculated_values = calculated_values or {}
    profile_values = profile_values or {}
    if calculated_warning:
        warnings.append(calculated_warning)
    profile_sample_rows = profile_values.get("samples") or []
    if profile_values.get("warning"):
        warnings.append(f"Shearwater 수심 프로파일 파싱 실패: {profile_values['warning']}")
    if profile_values.get("diagnostic"):
        warnings.append(f"관리자 진단: {profile_values['diagnostic']}")

    start_value = _row_value(row, mapping.get("entry_time") or mapping.get("dive_date"))
    end_value = _row_value(row, mapping.get("exit_time"))
    date_value = _row_value(row, mapping.get("dive_date")) or start_value
    gps_value = _row_value(row, mapping.get("entry_gps")) or _row_value(row, mapping.get("exit_gps")) or _row_value(row, mapping.get("gps"))
    gps_latitude, gps_longitude = _parse_gps(gps_value)
    latitude = parse_float_value(_stringify_value(_row_value(row, mapping.get("latitude")))) or gps_latitude
    longitude = parse_float_value(_stringify_value(_row_value(row, mapping.get("longitude")))) or gps_longitude
    dive_time, dive_time_source = _resolve_dive_time(row, mapping, table_name, calculated_values, warnings)
    max_depth, max_depth_source = _resolve_depth(
        calculated_values,
        ("max_depth", "maximum_depth", "maxdepth", "depth"),
        _row_value(row, mapping.get("max_depth")),
        "명시 컬럼(dive_details)",
        warnings,
    )
    avg_depth, avg_depth_source = _resolve_depth(
        calculated_values,
        ("avg_depth", "average_depth", "averagedepth"),
        _row_value(row, mapping.get("avg_depth")),
        "명시 컬럼(dive_details)",
        warnings,
    )
    if avg_depth is None:
        avg_depth = _average_depth_from_tank_profile(_row_value(row, mapping.get("tank_profile_data"))) or None
        if avg_depth is not None:
            avg_depth_source = "profile/sample 기반 계산"
    water_temp, water_temp_source = _resolve_water_temp(row, mapping, calculated_values, warnings)
    start_pressure, start_pressure_source = _resolve_pressure(
        calculated_values,
        ("start_pressure", "tank_start_pressure", "tank1pressurestart"),
        _row_value(row, mapping.get("start_pressure")),
        warnings,
    )
    end_pressure, end_pressure_source = _resolve_pressure(
        calculated_values,
        ("end_pressure", "tank_end_pressure", "tank1pressureend"),
        _row_value(row, mapping.get("end_pressure")),
        warnings,
    )
    extra_note = _calculated_note(calculated_values)
    site_name = _first_non_empty(
        _row_value(row, mapping.get("location")),
        _row_value(row, mapping.get("site")),
        _row_value(row, mapping.get("site_name")),
    )

    if mapping.get("dive_date"):
        confidence["날짜"] = "중간"
    else:
        warnings.append("날짜 컬럼 확인 필요")
    _set_confidence_source(confidence, "다이브타임", dive_time_source)
    _set_confidence_source(confidence, "최대 수심", max_depth_source)
    _set_confidence_source(confidence, "평균 수심", avg_depth_source)
    _set_confidence_source(confidence, "수온", water_temp_source)
    _set_confidence_source(confidence, "시작 압력", start_pressure_source)
    _set_confidence_source(confidence, "종료 압력", end_pressure_source)
    if gps_value and (latitude is None or longitude is None):
        warnings.append("GPS 컬럼 형식 확인 필요")
    elif latitude is None or longitude is None:
        warnings.append("GPS 정보 없음")
    if not site_name:
        warnings.append("포인트 미확정")

    source_dive_number = _stringify_value(_row_value(row, mapping.get("source_dive_number")))
    if source_dive_number:
        warnings.append(f"Shearwater 다이브 번호: {source_dive_number}")

    unknown_columns = [
        column for column in row.keys()
        if column not in set(mapping.values())
    ]
    if unknown_columns and table_name != "dive_details":
        warnings.append("일부 컬럼은 자동 매핑하지 않았습니다.")

    dive_date = _parse_date_any(date_value)
    entry_time = _parse_time_any(start_value)
    exit_time = _parse_time_any(end_value)
    if exit_time is None and entry_time is not None and dive_time is not None:
        exit_time = _calculate_exit_time(entry_time, dive_time)
        warnings.append("출수시각 자동 계산")
    profile_samples = _stringify_value(_row_value(row, mapping.get("tank_profile_data"))) or None

    return ImportDive(
        source=parser_name,
        source_file=original_filename,
        external_id=_stringify_value(_row_value(row, mapping.get("external_id"))) or None,
        dive_date=dive_date,
        entry_time=entry_time,
        exit_time=exit_time,
        dive_time=dive_time,
        max_depth=max_depth,
        avg_depth=avg_depth,
        water_temp=water_temp,
        start_pressure=start_pressure,
        end_pressure=end_pressure,
        buddy=_stringify_value(_row_value(row, mapping.get("buddy"))) or None,
        note=_join_note(_stringify_value(_row_value(row, mapping.get("note"))), extra_note),
        latitude=latitude,
        longitude=longitude,
        site_name=site_name or None,
        profile_samples=profile_samples,
        profile_sample_rows=profile_sample_rows,
        confidence=confidence,
        warnings=warnings,
        raw={key: _stringify_value(row[key]) for key in row.keys()} | {
            "source_table": table_name,
            "source_dive_number": source_dive_number,
            "calculated_values_from_samples_keys": ", ".join(sorted(calculated_values.keys())),
        },
    )


def _row_value(row, column: str | None):
    if not column:
        return None
    return row[column]


def _row_value_by_normalized(row, column_name: str):
    target = _normalize_key(column_name)
    for key in row.keys():
        if _normalize_key(key) == target:
            return row[key]
    return None


def _parse_date_any(value) -> object:
    text = _stringify_value(value)
    parsed = parse_date_value(text)
    if parsed:
        return parsed
    parsed_datetime = _parse_datetime_text(text)
    if parsed_datetime:
        return parsed_datetime.date()
    timestamp = _parse_timestamp(value)
    return timestamp.date() if timestamp else None


def _parse_time_any(value):
    text = _stringify_value(value)
    parsed = parse_time_value(text)
    if parsed:
        return parsed
    parsed_datetime = _parse_datetime_text(text)
    if parsed_datetime:
        return parsed_datetime.time()
    timestamp = _parse_timestamp(value)
    return timestamp.time() if timestamp else None


def _parse_datetime_text(value: str):
    if not value:
        return None
    for candidate in (value.strip(), value.strip().replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(candidate[:19])
        except ValueError:
            continue
    return None


def _calculate_exit_time(entry_time, dive_time_minutes: int):
    base = datetime.combine(datetime.today().date(), entry_time)
    return (base + timedelta(minutes=dive_time_minutes)).time().replace(microsecond=0)


def _parse_timestamp(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 10_000_000_000:
        number = number / 1000
    if number < 1_000_000_000:
        return None
    try:
        return datetime.fromtimestamp(number)
    except (OSError, ValueError):
        return None


def _parse_gps(value) -> tuple[float | None, float | None]:
    text = _stringify_value(value).strip()
    if not text:
        return None, None

    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = None

    if isinstance(parsed, dict):
        latitude = _first_json_number(parsed, "latitude", "lat", "Latitude", "Lat")
        longitude = _first_json_number(parsed, "longitude", "lon", "lng", "Longitude", "Lon", "Lng")
        if latitude is not None and longitude is not None:
            return latitude, longitude

    numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
    if len(numbers) < 2:
        return None, None
    return parse_float_value(numbers[0]), parse_float_value(numbers[1])


def _parse_duration_minutes(value, table_name: str) -> int | None:
    seconds = parse_float_value(_stringify_value(value))
    if seconds is None:
        return None
    if table_name == "dive_details":
        return max(int(round(seconds / 60)), 1) if seconds > 0 else 0
    return parse_int_value(_stringify_value(value))


def _resolve_dive_time(row, mapping: dict[str, str], table_name: str, calculated_values: dict[str, object], warnings: list[str]) -> tuple[int | None, str]:
    calc_key, calc_value = _calculated_first_value(
        calculated_values,
        "dive_time",
        "duration",
        "total_time",
        "divetime",
        "divetimeinseconds",
        "footerdivetimeinseconds",
    )
    if calc_value is not None:
        minutes = _duration_value_to_minutes(calc_value, calc_key)
        if minutes is not None:
            if minutes == 0:
                warnings.append("다이브타임 계산값이 0이라 비워 두었습니다.")
                return None, "계산값 확인 필요"
            return minutes, "계산값(calculated_values_from_samples)"

    value = _parse_duration_minutes(_row_value(row, mapping.get("dive_time")), table_name)
    if value == 0:
        warnings.append("다이브타임이 0이라 비워 두었습니다.")
        return None, "명시 컬럼(dive_details) 확인 필요"
    if value is not None:
        return value, "명시 컬럼(dive_details)"
    warnings.append("다이브타임 정보 없음")
    return None, ""


def _duration_value_to_minutes(value, key: str) -> int | None:
    number = parse_float_value(_stringify_value(value))
    if number is None:
        return None
    normalized_key = _normalize_key(key)
    if "second" in normalized_key or number > 300:
        return max(int(round(number / 60)), 1) if number > 0 else 0
    return int(round(number))


def _resolve_depth(
    calculated_values: dict[str, object],
    calculated_keys: tuple[str, ...],
    detail_value,
    detail_source: str,
    warnings: list[str],
) -> tuple[float | None, str]:
    calc_key, calc_value = _calculated_first_value(calculated_values, *calculated_keys)
    if calc_value is not None:
        value = _depth_value_to_meters(calc_value, calc_key)
        if value == 0.0:
            warnings.append(f"{_display_calculated_key(calc_key)} 계산값이 0이라 비워 두었습니다.")
            return None, "계산값 확인 필요"
        if value is not None:
            return value, "계산값(calculated_values_from_samples)"

    value = _depth_value_to_meters(detail_value, "")
    if value == 0.0:
        warnings.append("수심 값이 0이라 비워 두었습니다.")
        return None, f"{detail_source} 확인 필요"
    if value is not None:
        return value, detail_source
    return None, ""


def _depth_value_to_meters(value, key: str) -> float | None:
    number = parse_float_value(_stringify_value(value))
    if number is None:
        return None
    normalized_key = _normalize_key(key)
    if "feet" in normalized_key or normalized_key.endswith("ft"):
        return round(number * 0.3048, 2)
    return number


def _resolve_water_temp(row, mapping: dict[str, str], calculated_values: dict[str, object], warnings: list[str]) -> tuple[float | None, str]:
    calc_key, calc_value = _calculated_first_value(
        calculated_values,
        "min_temp",
        "lowest_temperature",
        "water_temp",
        "mintemp",
        "averagetemp",
        "average_temp",
        "maxtemp",
    )
    if calc_value is not None:
        value = _temperature_value_to_celsius(calc_value)
        if value == 0.0:
            warnings.append("수온 계산값이 0이라 비워 두었습니다.")
            return None, "계산값 확인 필요"
        if value is not None:
            return value, "계산값(calculated_values_from_samples)"

    temperature_values = [
        _temperature_value_to_celsius(_row_value(row, mapping.get("min_temp"))),
        _temperature_value_to_celsius(_row_value(row, mapping.get("average_temp"))),
        _temperature_value_to_celsius(_row_value(row, mapping.get("max_temp"))),
        _temperature_value_to_celsius(_row_value(row, mapping.get("water_temp"))),
    ]
    valid_values = [value for value in temperature_values if value not in (None, 0.0)]
    if valid_values:
        return valid_values[0], "명시 컬럼(dive_details)"

    if any(value == 0.0 for value in temperature_values):
        warnings.append("수온 정보 없음")
        return None, "dive_details 확인 필요"
    warnings.append("수온 정보 없음")
    return None, ""


def _temperature_value_to_celsius(value) -> float | None:
    number = parse_float_value(_stringify_value(value))
    if number is None:
        return None
    if number >= 200:
        return round(number - 273.15, 2)
    return number


def _resolve_pressure(
    calculated_values: dict[str, object],
    calculated_keys: tuple[str, ...],
    detail_value,
    warnings: list[str],
) -> tuple[int | None, str]:
    calc_key, calc_value = _calculated_first_value(calculated_values, *calculated_keys)
    if calc_value is not None:
        value = parse_int_value(_stringify_value(calc_value))
        if value == 0:
            warnings.append(f"{_display_calculated_key(calc_key)} 계산값이 0이라 비워 두었습니다.")
            return None, "계산값 확인 필요"
        if value is not None:
            return value, "계산값(calculated_values_from_samples)"

    value = parse_int_value(_stringify_value(detail_value))
    if value == 0:
        warnings.append("탱크 압력 값이 0이라 비워 두었습니다.")
        return None, "명시 컬럼(dive_details) 확인 필요"
    if value is not None:
        return value, "명시 컬럼(dive_details)"
    return None, ""


def _calculated_first_value(calculated_values: dict[str, object], *names: str) -> tuple[str, object | None]:
    normalized = {
        _normalize_key(key): (key, value)
        for key, value in calculated_values.items()
    }
    for name in names:
        match = normalized.get(_normalize_key(name))
        if match and _stringify_value(match[1]).strip():
            return match
    return "", None


def _calculated_note(calculated_values: dict[str, object]) -> str:
    note_parts = []
    for label, names in (
        ("SAC", ("sac", "average_sac", "averagesac")),
        ("가스", ("gas", "gas_mix", "gasmix")),
    ):
        key, value = _calculated_first_value(calculated_values, *names)
        if value is not None:
            note_parts.append(f"{label}: {_stringify_value(value)}")
    return " / ".join(note_parts)


def _join_note(*values: str) -> str | None:
    text = " / ".join(value.strip() for value in values if value and value.strip())
    return text or None


def _set_confidence_source(confidence: dict[str, str], label: str, source: str):
    if source:
        confidence[label] = source


def _display_calculated_key(key: str) -> str:
    return key or "계산값"


def _average_depth_from_tank_profile(value) -> float | None:
    text = _stringify_value(value)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None

    gas_profiles = parsed.get("GasProfiles") if isinstance(parsed, dict) else None
    if not isinstance(gas_profiles, list) or not gas_profiles:
        return None
    first_profile = gas_profiles[0]
    if not isinstance(first_profile, dict):
        return None
    return parse_float_value(_stringify_value(first_profile.get("AverageDepthInMeters")))


def _parse_shearwater_water_temp(row, mapping: dict[str, str], warnings: list[str]) -> float | None:
    temperature_values = [
        parse_float_value(_stringify_value(_row_value(row, mapping.get("min_temp")))),
        parse_float_value(_stringify_value(_row_value(row, mapping.get("average_temp")))),
        parse_float_value(_stringify_value(_row_value(row, mapping.get("max_temp")))),
        parse_float_value(_stringify_value(_row_value(row, mapping.get("water_temp")))),
    ]
    valid_values = [value for value in temperature_values if value not in (None, 0.0)]
    if valid_values:
        return valid_values[0]

    if any(value == 0.0 for value in temperature_values):
        warnings.append("수온 정보 없음")
    return None


def _first_non_empty(*values) -> str:
    for value in values:
        text = _stringify_value(value).strip()
        if text:
            return text
    return ""


def _first_json_number(data: dict, *keys: str) -> float | None:
    for key in keys:
        if key in data:
            value = parse_float_value(_stringify_value(data[key]))
            if value is not None:
                return value
    return None


def _build_shearwater_diagnostics(
    tables: list[dict[str, object]],
    candidates: list[dict[str, object]],
    mapping: dict[str, str],
    mapping_warnings: list[str],
    stats: dict[str, object] | None = None,
) -> list[str]:
    stats = stats or {}
    row_counts = ", ".join(f"{table['name']}={table['row_count']}행" for table in tables)
    messages = [f"관리자 진단: 테이블별 행 수: {row_counts or '없음'}"]
    if candidates:
        selected = candidates[0]
        messages.append(
            f"관리자 진단: 선택 테이블 '{selected['name']}', 점수 {selected['score']}, 컬럼 {len(selected['columns'])}개"
        )
    if stats:
        calculated_keys = sorted(stats.get("calculated_keys") or [])
        messages.append(
            "관리자 진단: "
            f"log_data 매칭 성공 {stats.get('log_data_match_count', 0)}개, "
            f"calculated_values_from_samples 파싱 성공 {stats.get('calculated_parse_success_count', 0)}개, "
            f"파싱 실패 {stats.get('calculated_parse_failure_count', 0)}개"
        )
        messages.append(
            "관리자 진단: Shearwater 프로파일 "
            f"압축 해제 성공 {stats.get('profile_decompress_success_count', 0)}개, "
            f"샘플 파싱 성공 {stats.get('profile_parse_success_count', 0)}개, "
            f"샘플 파싱 실패 {stats.get('profile_parse_failure_count', 0)}개"
        )
        messages.append(
            "관리자 진단: calculated_values_from_samples 주요 키: "
            + (", ".join(calculated_keys) if calculated_keys else "없음")
        )
    if mapping:
        mapped_fields = ", ".join(f"{field}->{column}" for field, column in sorted(mapping.items()))
        messages.append(f"관리자 진단: 매핑 필드: {mapped_fields}")
    if mapping_warnings:
        messages.append("관리자 진단: " + " / ".join(mapping_warnings))
    return messages


def _normalize_key(value: str) -> str:
    return value.lower().replace(" ", "").replace("_", "").replace("-", "").replace(".", "")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _stringify_value(value) -> str:
    if value is None:
        return ""

    return str(value)
