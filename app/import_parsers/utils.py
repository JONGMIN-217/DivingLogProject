from datetime import date, datetime, time


def first_value(row: dict[str, str], *names: str) -> str:
    normalized = {_normalize_key(key): value for key, value in row.items()}
    for name in names:
        value = normalized.get(_normalize_key(name))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def parse_date_value(value: str | None) -> date | None:
    if not value:
        return None

    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt).date()
        except ValueError:
            continue

    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_time_value(value: str | None) -> time | None:
    if not value:
        return None

    value = value.strip()
    for fmt in ("%H:%M:%S", "%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt).time()
        except ValueError:
            continue

    return None


def parse_float_value(value: str | None) -> float | None:
    if not value:
        return None

    cleaned = (
        value.strip()
        .replace(",", ".")
        .replace("m", "")
        .replace("°C", "")
        .replace("C", "")
    )
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int_value(value: str | None) -> int | None:
    number = parse_float_value(value)
    if number is None:
        return None

    return int(round(number))


def build_note(row: dict[str, str], *names: str) -> str | None:
    values = [
        first_value(row, name)
        for name in names
    ]
    text = " / ".join(value for value in values if value)
    return text or None


def _normalize_key(value: str) -> str:
    return value.lower().replace(" ", "").replace("_", "").replace("-", "").replace(".", "")
