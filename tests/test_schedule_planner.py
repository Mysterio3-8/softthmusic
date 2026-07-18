from datetime import datetime

from app.schedule_planner import compute_publish_datetimes


def test_returns_today_slots_when_all_in_future():
    now = datetime(2026, 7, 18, 8, 0)
    slots = compute_publish_datetimes(["09:00", "15:00", "21:00"], now)

    assert [s.hour for s in slots] == [9, 15, 21]
    assert all(s.day == 18 for s in slots)


def test_moves_past_time_to_tomorrow():
    now = datetime(2026, 7, 18, 10, 0)
    slots = compute_publish_datetimes(["09:00", "15:00"], now)

    # 09:00 прошло -> завтра; 15:00 сегодня. Отсортировано по возрастанию.
    assert slots[0] == datetime(2026, 7, 18, 15, 0)
    assert slots[1] == datetime(2026, 7, 19, 9, 0)


def test_result_is_sorted():
    now = datetime(2026, 7, 18, 0, 0)
    slots = compute_publish_datetimes(["21:00", "09:00", "15:00"], now)
    assert slots == sorted(slots)
