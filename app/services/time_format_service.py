def format_dive_duration(total_minutes) -> str:
    """분 단위 다이빙 시간을 사람이 읽기 쉬운 한국어 문자열로 변환한다."""
    minutes = _normalize_minutes(total_minutes)

    minutes_per_hour = 60
    minutes_per_day = 24 * minutes_per_hour
    minutes_per_month = 30 * minutes_per_day
    minutes_per_year = 365 * minutes_per_day

    if minutes >= minutes_per_year:
        years = minutes // minutes_per_year
        months = (minutes % minutes_per_year) // minutes_per_month
        return _join_duration_parts((years, "년"), (months, "개월"))

    if minutes >= minutes_per_month:
        months = minutes // minutes_per_month
        days = (minutes % minutes_per_month) // minutes_per_day
        return _join_duration_parts((months, "개월"), (days, "일"))

    if minutes >= minutes_per_day:
        days = minutes // minutes_per_day
        hours = (minutes % minutes_per_day) // minutes_per_hour
        return _join_duration_parts((days, "일"), (hours, "시간"))

    if minutes >= minutes_per_hour:
        hours = minutes // minutes_per_hour
        remaining_minutes = minutes % minutes_per_hour
        return _join_duration_parts((hours, "시간"), (remaining_minutes, "분"))

    return f"{minutes}분"


def _normalize_minutes(value) -> int:
    if value is None:
        return 0
    try:
        return max(int(round(float(value))), 0)
    except (TypeError, ValueError):
        return 0


def _join_duration_parts(*parts: tuple[int, str]) -> str:
    visible_parts = [f"{value}{unit}" for value, unit in parts if value]
    return " ".join(visible_parts) if visible_parts else "0분"
