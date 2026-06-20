from dataclasses import dataclass, field
import gzip
import json
import logging
import re
import struct
import zlib

from app.importers.common import ImportProfileSample


logger = logging.getLogger(__name__)


@dataclass
class ShearwaterProfileParseResult:
    samples: list[ImportProfileSample] = field(default_factory=list)
    decompressed: bool = False
    raw_length: int = 0
    payload_length: int = 0
    decompressed_length: int = 0
    parser: str = ""
    warning: str = ""


def parse_shearwater_data_bytes_1(
    value,
    *,
    max_depth: float | None = None,
    avg_depth: float | None = None,
    duration_seconds: int | None = None,
) -> ShearwaterProfileParseResult:
    raw = _bytes_value(value)
    result = ShearwaterProfileParseResult(raw_length=len(raw))
    if not raw:
        result.warning = "data_bytes_1 값이 비어 있습니다."
        return result

    payload = raw[4:] if len(raw) > 4 else raw
    result.payload_length = len(payload)
    decompressed, warning = _decompress_payload(payload)
    if decompressed is None:
        result.warning = warning
        logger.info(
            "Shearwater 프로파일 압축 해제 실패: raw=%s payload=%s reason=%s",
            result.raw_length,
            result.payload_length,
            warning,
        )
        return result

    result.decompressed = True
    result.decompressed_length = len(decompressed)

    samples, parser_name, parse_warning = _parse_decompressed_profile(
        decompressed,
        max_depth=max_depth,
        avg_depth=avg_depth,
        duration_seconds=duration_seconds,
    )
    result.samples = samples
    result.parser = parser_name
    result.warning = parse_warning
    if parse_warning:
        logger.info(
            "Shearwater 프로파일 파싱 실패: raw=%s payload=%s decompressed=%s reason=%s preview=%r",
            result.raw_length,
            result.payload_length,
            result.decompressed_length,
            parse_warning,
            decompressed[:120],
        )
    return result


def _bytes_value(value) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytearray):
        return bytes(value)
    return b""


def _decompress_payload(payload: bytes) -> tuple[bytes | None, str]:
    attempts = (
        ("gzip", lambda data: gzip.decompress(data)),
        ("zlib", lambda data: zlib.decompress(data)),
    )
    errors = []
    for name, decompressor in attempts:
        try:
            return decompressor(payload), ""
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return None, "data_bytes_1 압축 해제 실패: " + " / ".join(errors)


def _parse_decompressed_profile(
    data: bytes,
    *,
    max_depth: float | None = None,
    avg_depth: float | None = None,
    duration_seconds: int | None = None,
) -> tuple[list[ImportProfileSample], str, str]:
    text = _decode_text(data)
    if text:
        samples = _parse_json_profile(text)
        if samples:
            return samples, "json", ""
        samples = _parse_text_rows(text)
        if samples:
            return samples, "text", ""

    samples = _parse_float_triplets(data)
    if samples:
        return samples, "binary-float-triplet", ""

    samples = _parse_depth_decimeter_series(
        data,
        max_depth=max_depth,
        avg_depth=avg_depth,
        duration_seconds=duration_seconds,
    )
    if samples:
        return samples, "binary-u16-depth-decimeter", ""

    return [], "", "압축 해제는 성공했지만 샘플 구조를 확인하지 못했습니다."


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            text = data.decode(encoding).strip("\x00 \r\n\t")
        except UnicodeDecodeError:
            continue
        if text and _looks_like_text(text):
            return text
    return ""


def _looks_like_text(text: str) -> bool:
    if not text:
        return False
    printable = sum(1 for char in text[:500] if char.isprintable() or char in "\r\n\t")
    return printable / max(len(text[:500]), 1) > 0.85


def _parse_json_profile(text: str) -> list[ImportProfileSample]:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return []
    rows = _extract_sample_rows(parsed)
    samples = [_sample_from_mapping(row, "Shearwater data_bytes_1") for row in rows if isinstance(row, dict)]
    return _valid_sample_sequence([sample for sample in samples if sample is not None])


def _extract_sample_rows(parsed) -> list[dict]:
    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, dict)]
    if not isinstance(parsed, dict):
        return []
    for key in ("samples", "profile", "profile_samples", "dive_profile", "waypoints", "data"):
        value = parsed.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    for value in parsed.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    return []


def _parse_text_rows(text: str) -> list[ImportProfileSample]:
    samples = []
    for index, line in enumerate(text.splitlines()):
        numbers = [float(value) for value in re.findall(r"[-+]?\d+(?:[.,]\d+)?", line.replace(",", "."))]
        if len(numbers) < 2:
            continue
        elapsed_seconds = _normalize_elapsed_seconds(numbers[0], index)
        depth = _normalize_depth(numbers[1])
        temperature = _normalize_temperature(numbers[2]) if len(numbers) >= 3 else None
        if depth is None:
            continue
        samples.append(
            ImportProfileSample(
                elapsed_seconds=elapsed_seconds,
                depth=depth,
                temperature=temperature,
                source="Shearwater data_bytes_1",
            )
        )
    return _valid_sample_sequence(samples)


def _parse_float_triplets(data: bytes) -> list[ImportProfileSample]:
    # 보수적 휴리스틱: 압축 해제 결과가 3개 float32(time/depth/temp) 반복인 경우만 수용한다.
    if len(data) < 36 or len(data) % 12 != 0:
        return []
    samples = []
    try:
        values = struct.unpack("<" + "f" * (len(data) // 4), data)
    except struct.error:
        return []
    for index in range(0, len(values), 3):
        elapsed = values[index]
        depth = _normalize_depth(values[index + 1])
        temperature = _normalize_temperature(values[index + 2])
        if depth is None:
            return []
        samples.append(
            ImportProfileSample(
                elapsed_seconds=_normalize_elapsed_seconds(elapsed, index // 3),
                depth=depth,
                temperature=temperature,
                source="Shearwater data_bytes_1",
            )
        )
    return _valid_sample_sequence(samples)


def _parse_depth_decimeter_series(
    data: bytes,
    *,
    max_depth: float | None = None,
    avg_depth: float | None = None,
    duration_seconds: int | None = None,
) -> list[ImportProfileSample]:
    if len(data) < 256:
        return []

    target_deci = max_depth * 10 if max_depth else None
    candidates = []
    for stride in range(32, 97):
        for start in range(0, min(512, stride * 8)):
            values = []
            for offset in range(start, len(data) - 1, stride):
                value = struct.unpack_from("<H", data, offset)[0]
                if value in (0, 1, 255, 256, 257):
                    continue
                if 0 < value <= 400:
                    values.append(value)
                elif values:
                    # A short metadata spike can appear before/after the profile block.
                    continue

            if len(values) < 20:
                continue
            max_value = max(values)
            if target_deci is not None and abs(max_value - target_deci) > max(20, target_deci * 0.35):
                continue
            smoothness = sum(abs(left - right) for left, right in zip(values, values[1:])) / max(len(values) - 1, 1)
            average_depth = sum(values) / len(values) / 10
            if not (1 <= average_depth <= 45):
                continue
            if max_depth and average_depth < max_depth * 0.2:
                continue
            if avg_depth and abs(average_depth - avg_depth) > max(5, avg_depth * 0.8):
                continue
            score = len(values) * 2 - smoothness
            if target_deci is not None:
                score -= abs(max_value - target_deci) * 2
            if avg_depth:
                score -= abs(average_depth - avg_depth) * 10
            candidates.append((score, stride, start, values))

    if not candidates:
        return []

    _, _, _, values = max(candidates, key=lambda item: item[0])
    if duration_seconds is None or duration_seconds <= 0:
        duration_seconds = max((len(values) - 1) * 10, 1)
    interval = duration_seconds / max(len(values) - 1, 1)
    samples = [
        ImportProfileSample(
            elapsed_seconds=int(round(index * interval)),
            depth=round(value / 10, 2),
            source="Shearwater data_bytes_1",
        )
        for index, value in enumerate(values)
    ]
    return _valid_sample_sequence(samples)


def _sample_from_mapping(row: dict, source: str) -> ImportProfileSample | None:
    elapsed = _first_number(row, "elapsed_seconds", "seconds", "time", "divetime", "runtime")
    depth = _first_number(row, "depth", "depth_m", "depthInMeters", "current_depth")
    temperature = _first_number(row, "temperature", "water_temp", "temp")
    pressure = _first_number(row, "pressure", "tank_pressure")
    if depth is None:
        return None
    return ImportProfileSample(
        elapsed_seconds=_normalize_elapsed_seconds(elapsed, 0),
        depth=_normalize_depth(depth),
        temperature=_normalize_temperature(temperature),
        pressure=pressure,
        source=source,
    )


def _first_number(row: dict, *keys: str) -> float | None:
    normalized = {_normalize_key(key): value for key, value in row.items()}
    for key in keys:
        value = normalized.get(_normalize_key(key))
        if value is None:
            continue
        try:
            return float(str(value).replace(",", "."))
        except ValueError:
            continue
    return None


def _normalize_key(value: str) -> str:
    return "".join(char for char in str(value).lower() if char.isalnum())


def _normalize_elapsed_seconds(value, fallback_index: int) -> int:
    if value is None:
        return fallback_index
    seconds = float(value)
    if 0 <= seconds < 300 and fallback_index > 0 and seconds <= fallback_index + 1:
        return int(round(seconds * 60))
    return max(int(round(seconds)), 0)


def _normalize_depth(value) -> float | None:
    if value is None:
        return None
    depth = float(value)
    if depth < 0 or depth > 300:
        return None
    return round(depth, 2)


def _normalize_temperature(value) -> float | None:
    if value is None:
        return None
    temperature = float(value)
    if temperature >= 200:
        temperature = temperature - 273.15
    if temperature < -5 or temperature > 60:
        return None
    return round(temperature, 2)


def _valid_sample_sequence(samples: list[ImportProfileSample]) -> list[ImportProfileSample]:
    if len(samples) < 3:
        return []
    samples = [sample for sample in samples if sample.depth is not None]
    samples.sort(key=lambda sample: sample.elapsed_seconds)
    if any(samples[index].elapsed_seconds < samples[index - 1].elapsed_seconds for index in range(1, len(samples))):
        return []
    max_depth = max(sample.depth or 0 for sample in samples)
    return samples if max_depth > 0 else []
