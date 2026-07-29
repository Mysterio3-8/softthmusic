"""Когда публиковать следующий трек: случайный интервал + ночная пауза.

Время — МСК и всегда с зоной. Опираться на локальную зону процесса нельзя:
systemd-юнит стоит с TZ=Europe/Moscow, а тот же CLI, запущенный руками по SSH,
получит UTC сервера — ночное окно и next_post_at разъехались бы на 3 часа
(поймано на первом живом прогоне 2026-07-29).

Чистая логика без сайд-эффектов, `rng` параметром — ради детерминированных тестов.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def now_msk() -> datetime:
    """Текущий момент в МСК, независимо от TZ процесса."""
    return datetime.now(MOSCOW)


def to_msk(moment: datetime) -> datetime:
    """Приводит момент к МСК. Наивный считаем уже московским."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=MOSCOW)
    return moment.astimezone(MOSCOW)


# Разброс момента возобновления после ночной паузы. Без него каждый «утренний»
# пост падал бы ровно в quiet_end — роботизированный тайминг, который и палит бота.
_RESUME_JITTER_MINUTES = 45


def is_quiet_hour(moment: datetime, quiet_start_hour: int, quiet_end_hour: int) -> bool:
    """Попадает ли момент в ночное окно. Окно может пересекать полночь (23..9)."""
    if quiet_start_hour == quiet_end_hour:
        return False  # окно нулевой длины = пауза выключена
    hour = to_msk(moment).hour
    if quiet_start_hour < quiet_end_hour:
        return quiet_start_hour <= hour < quiet_end_hour
    return hour >= quiet_start_hour or hour < quiet_end_hour


def next_publish_moment(
    now: datetime,
    min_interval_minutes: int,
    max_interval_minutes: int,
    quiet_start_hour: int,
    quiet_end_hour: int,
    rng: random.Random | None = None,
) -> datetime:
    """Момент следующей публикации: now + случайный интервал, сдвинутый из ночи."""
    rng = rng or random.Random()
    delay = rng.randint(min_interval_minutes, max_interval_minutes)
    candidate = (to_msk(now) + timedelta(minutes=delay)).replace(second=0, microsecond=0)
    if not is_quiet_hour(candidate, quiet_start_hour, quiet_end_hour):
        return candidate
    return _resume_after_quiet(candidate, quiet_end_hour, rng)


def _resume_after_quiet(moment: datetime, quiet_end_hour: int, rng: random.Random) -> datetime:
    """Переносит момент на конец ночного окна плюс случайные минуты."""
    resume = moment.replace(hour=quiet_end_hour, minute=0, second=0, microsecond=0)
    if resume <= moment:
        resume += timedelta(days=1)
    return resume + timedelta(minutes=rng.randint(0, _RESUME_JITTER_MINUTES))
