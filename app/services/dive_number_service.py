from datetime import date, time

from app.models import DiveLog


def _sort_key(log: DiveLog):
    return (
        log.dive_date or date.max,
        log.entry_time is None,
        log.entry_time or time.max,
        log.exit_time is None,
        log.exit_time or time.max,
        log.id,
    )


def recalculate_dive_numbers(db, user_id: int | None = None):
    query = db.query(DiveLog)
    if user_id is not None:
        query = query.filter(DiveLog.user_id == user_id)

    logs = sorted(query.all(), key=_sort_key)
    for number, log in enumerate(logs, start=1):
        log.dive_number = number

    db.flush()
    return len(logs)


def recalculate_all_dive_numbers(db):
    user_ids = [
        row[0]
        for row in db.query(DiveLog.user_id)
        .distinct()
        .all()
    ]

    total = 0
    for user_id in user_ids:
        query = db.query(DiveLog)
        if user_id is None:
            query = query.filter(DiveLog.user_id.is_(None))
        else:
            query = query.filter(DiveLog.user_id == user_id)

        logs = sorted(query.all(), key=_sort_key)
        for number, log in enumerate(logs, start=1):
            log.dive_number = number
        total += len(logs)

    db.flush()
    return total
