import json
import uuid
from datetime import date, time
from pathlib import Path

from app.importers.common import DivePointSuggestion, ImportDive


def _date_to_text(value: date | None):
    return value.isoformat() if value else ""


def _time_to_text(value: time | None):
    return value.strftime("%H:%M") if value else ""


def _parse_date(value: str | None):
    if not value:
        return None
    return date.fromisoformat(value)


def _parse_time(value: str | None):
    if not value:
        return None
    return time.fromisoformat(value)


def dive_to_batch_item(dive: ImportDive, index: int):
    return {
        "index": index,
        "selected": True,
        "source": dive.source,
        "source_file": dive.source_file,
        "external_id": dive.external_id,
        "source_file_hash": getattr(dive, "source_file_hash", None),
        "dive_date": _date_to_text(dive.dive_date),
        "entry_time": _time_to_text(dive.entry_time),
        "exit_time": _time_to_text(dive.exit_time),
        "dive_time": dive.dive_time,
        "max_depth": dive.max_depth,
        "avg_depth": dive.avg_depth,
        "water_temp": dive.water_temp,
        "start_pressure": dive.start_pressure,
        "end_pressure": dive.end_pressure,
        "buddy": dive.buddy,
        "note": dive.note,
        "latitude": dive.latitude,
        "longitude": dive.longitude,
        "site_name": dive.site_name,
        "profile_samples": dive.profile_samples,
        "suggested_point_id": dive.suggested_point_id,
        "selected_point_id": dive.suggested_point_id,
        "confidence": dive.confidence,
        "warnings": dive.warnings,
        "raw": dive.raw,
        "suggested_point": {
            "point_id": dive.suggested_point.point_id,
            "point_name": dive.suggested_point.point_name,
            "distance_km": dive.suggested_point.distance_km,
        } if dive.suggested_point else None,
        "is_new_point_candidate": dive.is_new_point_candidate,
        "candidate_country": dive.candidate_country,
        "candidate_region": dive.candidate_region,
        "candidate_area": dive.candidate_area,
        "candidate_point_name": dive.candidate_point_name,
    }


def batch_item_to_dive(item: dict):
    dive = ImportDive(
        source=item.get("source") or "",
        source_file=item.get("source_file") or "",
        external_id=item.get("external_id"),
        dive_date=_parse_date(item.get("dive_date")),
        entry_time=_parse_time(item.get("entry_time")),
        exit_time=_parse_time(item.get("exit_time")),
        dive_time=item.get("dive_time"),
        max_depth=item.get("max_depth"),
        avg_depth=item.get("avg_depth"),
        water_temp=item.get("water_temp"),
        start_pressure=item.get("start_pressure"),
        end_pressure=item.get("end_pressure"),
        buddy=item.get("buddy"),
        note=item.get("note"),
        latitude=item.get("latitude"),
        longitude=item.get("longitude"),
        site_name=item.get("site_name"),
        profile_samples=item.get("profile_samples"),
        suggested_point_id=item.get("suggested_point_id"),
        confidence=item.get("confidence") or {},
        warnings=item.get("warnings") or [],
        raw=item.get("raw") or {},
    )
    dive.source_file_hash = item.get("source_file_hash")
    dive.is_new_point_candidate = bool(item.get("is_new_point_candidate"))
    dive.candidate_country = item.get("candidate_country") or ""
    dive.candidate_region = item.get("candidate_region") or ""
    dive.candidate_area = item.get("candidate_area") or ""
    dive.candidate_point_name = item.get("candidate_point_name") or ""
    suggestion = item.get("suggested_point")
    if suggestion:
        dive.suggested_point = DivePointSuggestion(
            point_id=suggestion["point_id"],
            point_name=suggestion["point_name"],
            distance_km=suggestion["distance_km"],
        )
    dive.batch_index = item.get("index", 0)
    dive.is_selected = bool(item.get("selected", True))
    dive.selected_point_id = item.get("selected_point_id") or item.get("suggested_point_id")
    return dive


def create_import_batch(directory: Path, preview):
    batch_id = uuid.uuid4().hex
    batch = {
        "batch_id": batch_id,
        "parser_name": preview.parser_name,
        "source_path": str(preview.source_path),
        "original_filename": preview.original_filename,
        "columns": preview.columns,
        "messages": preview.messages,
        "dives": [
            dive_to_batch_item(dive, index)
            for index, dive in enumerate(preview.dives)
        ],
    }
    save_import_batch(directory, batch)
    return batch


def import_batch_path(directory: Path, batch_id: str):
    return directory / f"import_batch_{batch_id}.json"


def load_import_batch(directory: Path, batch_id: str):
    path = import_batch_path(directory, batch_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_import_batch(directory: Path, batch: dict):
    path = import_batch_path(directory, batch["batch_id"])
    path.write_text(
        json.dumps(batch, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
