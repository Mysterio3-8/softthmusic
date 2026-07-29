from datetime import datetime, timedelta, timezone

import pytest

from app.album_db import (
    ALBUM_DONE,
    ALBUM_PUBLISHING,
    TRACK_ERROR,
    TRACK_PENDING,
    AlbumQueue,
)


@pytest.fixture
def queue(tmp_path):
    q = AlbumQueue(tmp_path / "test.db")
    yield q
    q.close()


def _add_album(queue, url="https://soundcloud.com/user/sets/album", title="Album"):
    return queue.enqueue(url, title, "Artist", 2, chat_id=42)


def test_enqueued_album_is_pending_not_active(queue):
    _add_album(queue)

    assert queue.pending_count() == 1
    assert queue.active_album() is None
    assert queue.next_pending_album().title == "Album"


def test_only_one_album_occupies_the_pipeline(queue):
    first = _add_album(queue, title="First")
    _add_album(queue, title="Second")

    queue.set_album_status(first, ALBUM_PUBLISHING)

    active = queue.active_album()
    assert active.title == "First"
    assert queue.next_pending_album().title == "Second"


def test_finished_album_frees_the_pipeline(queue):
    album_id = _add_album(queue)
    queue.set_album_status(album_id, ALBUM_PUBLISHING)

    queue.set_album_status(album_id, ALBUM_DONE)

    assert queue.active_album() is None


def test_tracks_come_back_in_playlist_order(queue):
    album_id = _add_album(queue)
    queue.add_track(album_id, 2, "Second", "Artist", 60, "b.mp3", "b.jpg")
    queue.add_track(album_id, 1, "First", "Artist", 60, "a.mp3", "a.jpg")

    assert [t.title for t in queue.tracks(album_id)] == ["First", "Second"]
    assert queue.next_pending_track(album_id).title == "First"


def test_published_track_leaves_the_pending_queue(queue):
    album_id = _add_album(queue)
    track_id = queue.add_track(album_id, 1, "First", "Artist", 60, "a.mp3", "a.jpg")

    queue.mark_track_published(track_id)

    assert queue.pending_track_count(album_id) == 0
    assert queue.next_pending_track(album_id) is None


def test_track_retries_until_attempts_run_out(queue):
    album_id = _add_album(queue)
    track_id = queue.add_track(album_id, 1, "First", "Artist", 60, "a.mp3", "a.jpg")

    assert queue.mark_track_error(track_id, max_attempts=3) == 1
    assert queue.tracks(album_id)[0].status == TRACK_PENDING

    queue.mark_track_error(track_id, max_attempts=3)
    assert queue.mark_track_error(track_id, max_attempts=3) == 3
    assert queue.tracks(album_id)[0].status == TRACK_ERROR


def test_exhausted_track_stops_blocking_the_album(queue):
    album_id = _add_album(queue)
    stuck = queue.add_track(album_id, 1, "Broken", "Artist", 60, "a.mp3", "a.jpg")
    queue.add_track(album_id, 2, "Fine", "Artist", 60, "b.mp3", "b.jpg")

    for _ in range(3):
        queue.mark_track_error(stuck, max_attempts=3)

    assert queue.next_pending_track(album_id).title == "Fine"


def test_next_post_at_survives_a_round_trip(queue):
    album_id = _add_album(queue)
    moment = datetime(2026, 7, 28, 15, 30)

    queue.set_album_status(album_id, ALBUM_PUBLISHING)
    queue.set_next_post_at(album_id, moment)

    assert queue.active_album().next_post_at == moment


def test_post_log_counts_only_recent_posts(queue):
    queue.log_post("album")
    queue.log_post("track")

    now = datetime.now(timezone.utc)
    assert queue.posts_since(now - timedelta(days=1)) == 2
    assert queue.posts_since(now + timedelta(minutes=1)) == 0
