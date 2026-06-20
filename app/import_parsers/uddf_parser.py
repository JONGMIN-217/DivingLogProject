import json
import re
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from app.importers.common import ImportProfileSample

from .base import ImportDive, ImportParser, ImportPreview, preview_from_dives
from .utils import parse_date_value, parse_float_value, parse_time_value


class UddfImportParser(ImportParser):
    parser_name = "UDDF"
    supported_extensions = (".uddf", ".xml")

    def can_parse(self, path: Path) -> bool:
        if not super().can_parse(path):
            return False

        try:
            root = ElementTree.parse(path).getroot()
        except ElementTree.ParseError:
            return False

        names = {_local_name(element.tag).lower() for element in root.iter()}
        return _local_name(root.tag).lower() == "uddf" or "uddf" in names

    def parse(self, path: Path, original_filename: str) -> ImportPreview:
        try:
            root = ElementTree.parse(path).getroot()
        except ElementTree.ParseError:
            return preview_from_dives(
                parser_name=self.parser_name,
                source_path=path,
                original_filename=original_filename,
                dives=[],
                messages=["UDDF XML 파일을 해석할 수 없습니다."],
            )

        site_map = _collect_sites(root)
        dives: list[ImportDive] = []

        for dive_element in _find_dive_elements(root):
            dives.append(_parse_dive(dive_element, site_map, original_filename, self.parser_name))

        return preview_from_dives(
            parser_name=self.parser_name,
            source_path=path,
            original_filename=original_filename,
            dives=dives,
            messages=[] if dives else ["UDDF에서 다이빙 로그 항목을 찾지 못했습니다."],
        )


def _parse_dive(dive_element, site_map: dict[str, dict[str, object]], original_filename: str, parser_name: str) -> ImportDive:
    raw = _flatten(dive_element)
    warnings: list[str] = []
    confidence: dict[str, str] = {}

    datetime_text = _first_path_text(
        dive_element,
        ("informationbeforedive", "datetime"),
        ("datetime",),
        ("starttime",),
        ("divestart",),
        ("timestamp",),
    )
    dive_date = parse_date_value(datetime_text)
    entry_time = parse_time_value(datetime_text) or parse_time_value(_first_path_text(dive_element, ("starttime",), ("divestart",)))
    if dive_date:
        confidence["날짜"] = "높음"
    else:
        warnings.append("날짜 확인 필요")

    duration_text = _first_path_text(
        dive_element,
        ("informationafterdive", "diveduration"),
        ("diveduration",),
        ("duration",),
    )
    waypoint_times = _waypoint_numbers(dive_element, "divetime")
    dive_time = _normalize_duration(duration_text, waypoint_times, warnings)
    if dive_time is not None:
        confidence["다이브타임"] = "중간"
    else:
        warnings.append("다이브타임 확인 필요")

    water_temp = _normalize_temperature(
        _first_path_text(
            dive_element,
            ("informationafterdive", "lowesttemperature"),
            ("lowesttemperature",),
            ("temperature",),
        ),
        _waypoint_numbers(dive_element, "temperature"),
        warnings,
    )

    max_depth = _normalize_depth(
        _first_path_text(
            dive_element,
            ("informationafterdive", "greatestdepth"),
            ("greatestdepth",),
            ("maxdepth",),
        ),
        warnings,
    )
    depth_samples = _waypoint_numbers(dive_element, "depth")
    profile_sample_rows = _profile_samples_from_waypoints(dive_element)
    profile_samples = _profile_samples_json(profile_sample_rows)
    if max_depth is None and depth_samples:
        max_depth = max(depth_samples)
        confidence["최대수심"] = "샘플"

    avg_depth = _average_depth(depth_samples, waypoint_times)
    if avg_depth is not None:
        confidence["평균수심"] = "샘플"
    else:
        avg_depth = _normalize_depth(_first_path_text(dive_element, ("averagedepth",), ("avgdepth",), ("meandepth",)), warnings)
        if avg_depth is None:
            warnings.append("평균수심 확인 필요")

    site = _site_for_dive(dive_element, raw, site_map)
    latitude = _coordinate_from_text(_first_any_text(dive_element, "latitude", "lat")) or site.get("latitude")
    longitude = _coordinate_from_text(_first_any_text(dive_element, "longitude", "lon", "lng")) or site.get("longitude")
    waypoint_latitude = _first_waypoint_coordinate(dive_element, "latitude", "lat")
    waypoint_longitude = _first_waypoint_coordinate(dive_element, "longitude", "lon", "lng")
    latitude = latitude if latitude is not None else waypoint_latitude
    longitude = longitude if longitude is not None else waypoint_longitude

    site_name = site.get("name") or _first_any_text(dive_element, "name", "location", "divesite")
    if site_name and _looks_like_auto_id(site_name):
        warnings.append("포인트명이 자동 ID처럼 보여 확인 필요")
        confidence["포인트명"] = "낮음"
    elif site_name:
        confidence["포인트명"] = "중간"

    if latitude is None or longitude is None:
        warnings.append("GPS 좌표 확인 필요")
    else:
        confidence["GPS"] = "중간"

    return ImportDive(
        source=parser_name,
        source_file=original_filename,
        external_id=_first_any_text(dive_element, "id", "uuid", "diveid", "number") or None,
        dive_date=dive_date,
        entry_time=entry_time,
        exit_time=parse_time_value(_first_any_text(dive_element, "exittime", "endtime", "diveend")),
        dive_time=dive_time,
        max_depth=max_depth,
        avg_depth=avg_depth,
        water_temp=water_temp,
        start_pressure=_int_or_none(_first_any_text(dive_element, "startpressure", "pressurestart")),
        end_pressure=_int_or_none(_first_any_text(dive_element, "endpressure", "pressureend")),
        buddy=_first_any_text(dive_element, "buddy", "buddies") or None,
        note=_first_any_text(dive_element, "note", "notes", "comment", "comments") or None,
        latitude=latitude,
        longitude=longitude,
        site_name=site_name if site_name and not _looks_like_auto_id(site_name) else None,
        profile_samples=profile_samples,
        profile_sample_rows=profile_sample_rows,
        confidence=confidence,
        warnings=warnings,
        raw=raw,
    )


def _find_dive_elements(root):
    return [
        element
        for element in root.iter()
        if _local_name(element.tag).lower() == "dive"
    ]


def _collect_sites(root) -> dict[str, dict[str, object]]:
    site_map: dict[str, dict[str, object]] = {}
    fallback_index = 0
    for element in root.iter():
        if _local_name(element.tag).lower() not in {"site", "divesite", "divespot", "location"}:
            continue

        raw = _flatten(element)
        latitude = _coordinate_from_text(_first_any_text(element, "latitude", "lat"))
        longitude = _coordinate_from_text(_first_any_text(element, "longitude", "lon", "lng"))
        name = (
            _first_path_text(element, ("name",))
            or _first_path_text(element, ("geography", "location"))
            or _first_any_text(element, "location", "placename")
        )
        site_id = _first_attr_or_text(element, "id", "uuid", "ref", "siteid", "divesiteid", "locationid")
        if not site_id:
            fallback_index += 1
            site_id = f"__site_{fallback_index}"

        site_map[site_id] = {
            "name": name,
            "latitude": latitude,
            "longitude": longitude,
            "raw": raw,
        }

    return site_map


def _site_for_dive(dive_element, raw: dict[str, str], site_map: dict[str, dict[str, object]]) -> dict[str, object]:
    if not site_map:
        return {}

    refs = set()
    for key in ("ref", "id", "uuid", "siteid", "divesiteid", "locationid"):
        value = _first_attr_or_text(dive_element, key)
        if value:
            refs.add(value)
    for element in dive_element.iter():
        if _local_name(element.tag).lower() in {"link", "ref", "site", "divesite"}:
            text = (element.text or "").strip()
            if text:
                refs.add(text)
            for value in element.attrib.values():
                if value:
                    refs.add(value.strip())

    for ref in refs:
        if ref in site_map:
            return site_map[ref]

    site_name = _first_any_text(dive_element, "site", "divesite", "location", "name")
    if site_name:
        for site in site_map.values():
            if site.get("name") == site_name:
                return site

    if len(site_map) == 1:
        return next(iter(site_map.values()))

    return {}


def _normalize_duration(value: str | None, waypoint_times: list[float], warnings: list[str]) -> int | None:
    number = parse_float_value(value) if value else None
    if number is None and waypoint_times:
        number = max(waypoint_times)
    if number is None:
        return None
    if number >= 300:
        return int(round(number / 60))
    if 5 <= number < 300:
        return int(round(number))
    warnings.append("다이브타임 단위 확인 필요")
    return int(round(number))


def _normalize_temperature(value: str | None, samples: list[float], warnings: list[str]) -> float | None:
    number = parse_float_value(value) if value else None
    if number is None and samples:
        number = min(samples)
    if number is None:
        return None
    if number > 200:
        return round(number - 273.15, 1)
    if 32 <= number <= 110:
        warnings.append("수온이 화씨일 가능성이 있어 확인 필요")
        return round(number, 1)
    if 0 <= number <= 40:
        return round(number, 1)
    warnings.append("수온 단위 확인 필요")
    return round(number, 1)


def _normalize_depth(value: str | None, warnings: list[str]) -> float | None:
    number = parse_float_value(value) if value else None
    if number is None:
        return None
    if number > 300:
        warnings.append("수심 단위 확인 필요")
    return round(number, 1)


def _average_depth(depths: list[float], times: list[float]) -> float | None:
    if not depths:
        return None
    if len(depths) == len(times) and len(depths) > 1:
        total_weight = 0.0
        weighted_sum = 0.0
        for index in range(1, len(depths)):
            delta = max(times[index] - times[index - 1], 0)
            weighted_sum += depths[index] * delta
            total_weight += delta
        if total_weight > 0:
            return round(weighted_sum / total_weight, 1)
    return round(sum(depths) / len(depths), 1)


def _waypoint_numbers(element, *names: str) -> list[float]:
    values = []
    for waypoint in element.iter():
        if _local_name(waypoint.tag).lower() != "waypoint":
            continue
        value = _first_any_text(waypoint, *names)
        number = parse_float_value(value)
        if number is not None:
            values.append(number)
    return values


def _profile_samples_json(samples: list[ImportProfileSample]) -> str | None:
    if not samples:
        return None
    payload = []
    for sample in samples:
        payload.append(
            {
                "seconds": sample.elapsed_seconds,
                "depth": sample.depth,
                "temperature": sample.temperature,
                "pressure": sample.pressure,
            }
        )
    return json.dumps({"samples": payload}, ensure_ascii=False)


def _profile_samples_from_waypoints(element) -> list[ImportProfileSample]:
    samples = []
    for index, waypoint in enumerate(element.iter()):
        if _local_name(waypoint.tag).lower() != "waypoint":
            continue

        depth = _normalize_depth(_first_any_text(waypoint, "depth"), [])
        temperature = _normalize_temperature(_first_any_text(waypoint, "temperature"), [], [])
        elapsed_seconds = _waypoint_elapsed_seconds(_first_any_text(waypoint, "divetime"), index)
        if depth is None and temperature is None:
            continue
        samples.append(
            ImportProfileSample(
                elapsed_seconds=elapsed_seconds,
                depth=depth,
                temperature=temperature,
                source="UDDF waypoint",
            )
        )
    return samples


def _waypoint_elapsed_seconds(value: str | None, fallback_index: int) -> int:
    number = parse_float_value(value) if value else None
    if number is None:
        return fallback_index
    return max(int(round(number)), 0)


def _first_waypoint_coordinate(element, *names: str) -> float | None:
    for waypoint in element.iter():
        if _local_name(waypoint.tag).lower() != "waypoint":
            continue
        number = _coordinate_from_text(_first_any_text(waypoint, *names))
        if number is not None:
            return number
    return None


def _first_path_text(element, *paths: tuple[str, ...]) -> str:
    for path in paths:
        match = _find_path(element, path)
        if match is not None:
            text = (match.text or "").strip()
            if text:
                return text
            for value in match.attrib.values():
                if value and value.strip():
                    return value.strip()
    return ""


def _find_path(element, path: tuple[str, ...]):
    current = element
    for name in path:
        next_element = None
        for child in list(current):
            if _local_name(child.tag).lower() == name.lower():
                next_element = child
                break
        if next_element is None:
            return None
        current = next_element
    return current


def _first_any_text(element, *names: str) -> str:
    normalized_names = {name.lower() for name in names}
    for key, value in element.attrib.items():
        if _local_name(key).lower() in normalized_names and value.strip():
            return value.strip()
    for child in element.iter():
        local_name = _local_name(child.tag).lower()
        if local_name in normalized_names:
            text = (child.text or "").strip()
            if text:
                return text
            for value in child.attrib.values():
                if value and value.strip():
                    return value.strip()
        for key, value in child.attrib.items():
            if _local_name(key).lower() in normalized_names and value.strip():
                return value.strip()
    return ""


def _first_attr_or_text(element, *names: str) -> str:
    return _first_any_text(element, *names)


def _flatten(element) -> dict[str, str]:
    data: dict[str, str] = {}
    for child in element.iter():
        name = _local_name(child.tag).lower()
        text = (child.text or "").strip()
        if text and name not in data:
            data[name] = text
        for key, value in child.attrib.items():
            data[f"{name}.{_local_name(key).lower()}"] = value.strip()
    return data


def _coordinate_from_text(value: str | None) -> float | None:
    if not value:
        return None
    number = parse_float_value(value)
    if number is None:
        match = re.search(r"-?\d+(?:[\.,]\d+)?", value)
        number = parse_float_value(match.group(0)) if match else None
    return number


def _int_or_none(value: str | None) -> int | None:
    number = parse_float_value(value)
    return int(round(number)) if number is not None else None


def _looks_like_auto_id(value: str) -> bool:
    stripped = value.strip()
    return bool(
        re.fullmatch(r"[0-9a-fA-F-]{16,}", stripped)
        or re.fullmatch(r"\d+", stripped)
        or stripped.lower().startswith(("urn:", "uuid:"))
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
