from datetime import datetime, timedelta, timezone

from app.database import Database


def _db(tmp_path):
    return Database(tmp_path / "test.db")


def test_new_video_is_not_skipped(tmp_path):
    db = _db(tmp_path)
    assert db.should_skip("abc") is False


def test_published_video_is_skipped(tmp_path):
    db = _db(tmp_path)
    db.mark_published("abc", "channel", "Title")
    assert db.is_published("abc") is True
    assert db.should_skip("abc") is True


def test_errored_video_skipped_until_retry_due(tmp_path):
    db = _db(tmp_path)
    db.mark_error("abc", "channel", "Title", [60, 180])

    now = datetime.now(timezone.utc)
    assert db.should_skip("abc", now) is True  # ретрай ещё не наступил
    assert db.should_skip("abc", now + timedelta(minutes=61)) is False


def test_should_skip_accepts_naive_now(tmp_path):
    # planner передаёт наивное локальное время — не должно падать на сравнении с UTC retry_at
    db = _db(tmp_path)
    db.mark_error("abc", "channel", "Title", [60, 180])
    assert db.should_skip("abc", datetime.now()) is True


def test_error_count_increments_and_stops_retrying(tmp_path):
    db = _db(tmp_path)
    assert db.mark_error("abc", "c", "t", [60, 180]) == 1
    assert db.mark_error("abc", "c", "t", [60, 180]) == 2

    third = db.mark_error("abc", "c", "t", [60, 180])
    assert third == 3
    # После исчерпания задержек retry_at пустой -> ролик снова доступен.
    assert db.should_skip("abc", datetime.now(timezone.utc)) is False


def test_publish_after_error_clears_retry(tmp_path):
    db = _db(tmp_path)
    db.mark_error("abc", "c", "t", [60, 180])
    db.mark_published("abc", "c", "Title")
    assert db.should_skip("abc") is True
    assert db.is_published("abc") is True
