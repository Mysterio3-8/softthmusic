import random
from datetime import datetime

from app.album_scheduler import MOSCOW, is_quiet_hour, next_publish_moment

QUIET_START = 23
QUIET_END = 9


def test_quiet_window_crossing_midnight_covers_both_sides():
    assert is_quiet_hour(datetime(2026, 7, 28, 23, 30), QUIET_START, QUIET_END)
    assert is_quiet_hour(datetime(2026, 7, 28, 3, 0), QUIET_START, QUIET_END)
    assert not is_quiet_hour(datetime(2026, 7, 28, 9, 0), QUIET_START, QUIET_END)
    assert not is_quiet_hour(datetime(2026, 7, 28, 22, 59), QUIET_START, QUIET_END)


def test_quiet_window_without_midnight_crossing():
    assert is_quiet_hour(datetime(2026, 7, 28, 2, 0), 1, 7)
    assert not is_quiet_hour(datetime(2026, 7, 28, 7, 0), 1, 7)


def test_equal_hours_disable_quiet_window():
    assert not is_quiet_hour(datetime(2026, 7, 28, 3, 0), 0, 0)


def test_interval_stays_within_configured_bounds():
    now = datetime(2026, 7, 28, 12, 0, tzinfo=MOSCOW)

    for seed in range(50):
        moment = next_publish_moment(now, 180, 300, QUIET_START, QUIET_END, random.Random(seed))
        delta_minutes = (moment - now).total_seconds() / 60
        assert 180 <= delta_minutes <= 300


def test_moment_landing_in_night_shifts_to_morning():
    # 21:30 + 3..5 ч попадает в ночное окно — публикация должна уехать на утро.
    now = datetime(2026, 7, 28, 21, 30)

    moment = next_publish_moment(now, 180, 300, QUIET_START, QUIET_END, random.Random(1))

    assert moment.day == 29
    assert not is_quiet_hour(moment, QUIET_START, QUIET_END)
    assert moment.hour == QUIET_END


def test_morning_resume_is_jittered_not_exactly_at_quiet_end():
    now = datetime(2026, 7, 28, 21, 30)

    minutes = {
        next_publish_moment(now, 180, 300, QUIET_START, QUIET_END, random.Random(seed)).minute
        for seed in range(30)
    }

    assert len(minutes) > 1, "все посты в одну и ту же минуту — это роботизированный тайминг"


def test_night_window_is_measured_in_moscow_not_process_timezone():
    """Тик из systemd идёт в МСК, а руками по SSH — в UTC сервера. Окно обязано
    считаться одинаково: иначе ночная пауза разъезжается на 3 часа."""
    from datetime import timezone
    from app.album_scheduler import MOSCOW

    # Один и тот же момент: 02:00 МСК = 23:00 UTC предыдущих суток.
    msk = datetime(2026, 7, 29, 2, 0, tzinfo=MOSCOW)
    utc = msk.astimezone(timezone.utc)

    assert is_quiet_hour(msk, QUIET_START, QUIET_END)
    assert is_quiet_hour(utc, QUIET_START, QUIET_END)


def test_daytime_moment_is_not_quiet_in_either_representation():
    from datetime import timezone
    from app.album_scheduler import MOSCOW

    msk = datetime(2026, 7, 29, 15, 0, tzinfo=MOSCOW)

    assert not is_quiet_hour(msk, QUIET_START, QUIET_END)
    assert not is_quiet_hour(msk.astimezone(timezone.utc), QUIET_START, QUIET_END)


def test_next_moment_is_moscow_aware():
    from app.album_scheduler import MOSCOW, now_msk

    moment = next_publish_moment(now_msk(), 180, 300, QUIET_START, QUIET_END, random.Random(0))

    assert moment.tzinfo is not None
    assert moment.utcoffset() == MOSCOW.utcoffset(moment.replace(tzinfo=None))


def test_disabled_night_window_lets_posts_through_at_any_hour():
    """Владелец разрешил постить круглосуточно (quiet_start == quiet_end).
    Ночные часы обязаны проходить, а момент — не уезжать на утро."""
    night = datetime(2026, 7, 30, 3, 0, tzinfo=MOSCOW)

    assert not is_quiet_hour(night, 0, 0)

    moment = next_publish_moment(night, 160, 235, 0, 0, random.Random(3))
    delta_minutes = (moment - night).total_seconds() / 60
    assert 160 <= delta_minutes <= 235, "без ночного окна сдвига быть не должно"


def test_seven_posts_a_day_fit_into_the_configured_interval():
    """Арифметика режима «7 в сутки»: 1440 / 7 = 205 мин — потолок среднего
    интервала. Настройки обязаны давать чуть больше семи, чтобы ограничителем
    работал суточный лимит, а не расписание."""
    from datetime import timedelta

    start = datetime(2026, 7, 30, 9, 0, tzinfo=MOSCOW)
    rng = random.Random(11)
    moment = start
    count = 0
    while moment < start + timedelta(days=1):
        moment = next_publish_moment(moment, 160, 235, 0, 0, rng)
        count += 1

    assert count >= 7, f"расписание не дотягивает до 7 постов в сутки: {count}"
