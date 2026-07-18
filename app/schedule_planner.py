from __future__ import annotations

from datetime import datetime, timedelta


def compute_publish_datetimes(times: list[str], now: datetime) -> list[datetime]:
    """Возвращает ближайшие будущие моменты публикации для заданных времён суток.

    Для каждого "HH:MM" берётся сегодняшняя дата; если момент уже прошёл —
    переносится на завтра. Результат отсортирован по возрастанию.
    """
    result: list[datetime] = []
    for hhmm in times:
        hours, minutes = (int(x) for x in hhmm.split(":"))
        candidate = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        result.append(candidate)
    return sorted(result)
