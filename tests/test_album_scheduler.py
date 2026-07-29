import random
from datetime import datetime

from app.album_scheduler import is_quiet_hour, next_publish_moment, soon

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
    now = datetime(2026, 7, 28, 12, 0)

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


def test_soon_is_in_the_future_and_rounded():
    now = datetime(2026, 7, 28, 12, 0, 37)

    moment = soon(now, random.Random(0))

    assert moment > now
    assert moment.second == 0
