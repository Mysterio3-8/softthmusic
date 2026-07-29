from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.album_db import ALBUM_DONE, ALBUM_PUBLISHING, AlbumQueue
from app.album_scheduler import MOSCOW
from app.album_publisher import tick
from app.config import Config, PostStyle, SoundCloudConfig


class FakeVK:
    """Считает обращения к VK — тесты проверяют, что лишних постов не случилось."""

    def __init__(self):
        self.uploads = []
        self.posts = []

    def upload_video(self, file_path, name, description):
        self.uploads.append(name)
        return "video-1_1"

    def schedule_post(self, message, attachment, publish_at):
        self.posts.append((message, publish_at))
        return len(self.posts)


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send(self, text, chat_id=None):
        self.messages.append(text)
        return True


def _config(tmp_path, **overrides) -> Config:
    settings = SoundCloudConfig(
        min_interval_minutes=180,
        max_interval_minutes=300,
        quiet_start_hour=23,
        quiet_end_hour=9,
        max_posts_per_day=overrides.get("max_posts_per_day", 5),
        album_title_template="{name} — {artist} | {suffix}",
        track_title_template="{name} — {artist} | {suffix}",
        censorship_suffix="без цензуры",
        max_track_attempts=3,
        work_dir=tmp_path / "work",
        post=PostStyle(
            flag="🎧",
            listen_url="https://t.me/tgram_music_bot",
            channel_url="https://t.me/tgramuzuka",
            hashtag_template="{artist}_{name}",
            hashtag_group="tgmusic",
            track_kind="Single",
            album_kind="Album",
        ),
    )
    return Config(
        vk_group_token="g",
        vk_user_token="u",
        group_id=1,
        channels=["c"],
        max_height=480,
        posts_per_day=3,
        posts_per_run=1,
        publish_times=["09:00"],
        ad_block="реклама",
        retry_delays_minutes=[60],
        database_path=tmp_path / "db.sqlite",
        downloads_dir=tmp_path / "dl",
        log_path=tmp_path / "log.txt",
        soundcloud=settings,
    )


@pytest.fixture
def queue(tmp_path):
    q = AlbumQueue(tmp_path / "db.sqlite")
    yield q
    q.close()


def test_empty_queue_does_nothing(tmp_path, queue):
    vk, notifier = FakeVK(), FakeNotifier()

    outcome = tick(_config(tmp_path), queue, vk, notifier, datetime(2026, 7, 28, 12, 0))

    assert outcome == "очередь пуста"
    assert vk.posts == []


def test_daily_limit_blocks_publishing(tmp_path, queue):
    config = _config(tmp_path, max_posts_per_day=2)
    queue.enqueue("https://soundcloud.com/a/sets/b", "Album", "Artist", 1, None)
    queue.log_post("album")
    queue.log_post("track")
    vk, notifier = FakeVK(), FakeNotifier()

    outcome = tick(config, queue, vk, notifier, datetime(2026, 7, 28, 12, 0))

    assert outcome == "суточный лимит постов исчерпан"
    assert vk.posts == []


def test_yesterdays_posts_do_not_count_against_the_limit(tmp_path, queue):
    config = _config(tmp_path, max_posts_per_day=1)
    queue.log_post("track")
    vk, notifier = FakeVK(), FakeNotifier()

    # Тик через двое суток: вчерашний пост уже вне окна, лимит снова свободен.
    outcome = tick(config, queue, vk, notifier, datetime.now() + timedelta(days=2))

    assert outcome == "очередь пуста"


def test_night_pause_holds_the_next_track(tmp_path, queue):
    config = _config(tmp_path)
    album_id = queue.enqueue("https://soundcloud.com/a/sets/b", "Album", "Artist", 1, None)
    queue.add_track(album_id, 1, "Track", "Artist", 60, "a.mp3", "a.jpg")
    queue.set_album_status(album_id, ALBUM_PUBLISHING)
    queue.set_next_post_at(album_id, datetime(2026, 7, 29, 1, 0))
    vk, notifier = FakeVK(), FakeNotifier()

    outcome = tick(config, queue, vk, notifier, datetime(2026, 7, 29, 2, 0))

    assert outcome == "ночная пауза"
    assert vk.posts == []


def test_interval_not_elapsed_holds_the_next_track(tmp_path, queue):
    config = _config(tmp_path)
    album_id = queue.enqueue("https://soundcloud.com/a/sets/b", "Album", "Artist", 1, None)
    queue.add_track(album_id, 1, "Track", "Artist", 60, "a.mp3", "a.jpg")
    queue.set_album_status(album_id, ALBUM_PUBLISHING)
    queue.set_next_post_at(album_id, datetime(2026, 7, 28, 18, 0))
    vk, notifier = FakeVK(), FakeNotifier()

    outcome = tick(config, queue, vk, notifier, datetime(2026, 7, 28, 15, 0))

    assert outcome.startswith("рано")
    assert vk.posts == []


def test_album_without_pending_tracks_finishes_and_notifies(tmp_path, queue):
    config = _config(tmp_path)
    album_id = queue.enqueue("https://soundcloud.com/a/sets/b", "Album", "Artist", 0, 42)
    queue.set_album_status(album_id, ALBUM_PUBLISHING)
    queue.set_next_post_at(album_id, datetime(2026, 7, 28, 12, 0))
    vk, notifier = FakeVK(), FakeNotifier()

    outcome = tick(config, queue, vk, notifier, datetime(2026, 7, 28, 15, 0))

    assert outcome == f"альбом {album_id} завершён"
    assert queue.active_album() is None
    assert "опубликован полностью" in notifier.messages[0]


def test_finish_message_mentions_the_waiting_album(tmp_path, queue):
    config = _config(tmp_path)
    done = queue.enqueue("https://soundcloud.com/a/sets/b", "First", "Artist", 0, 42)
    queue.enqueue("https://soundcloud.com/a/sets/c", "Second", "Artist", 3, 42)
    queue.set_album_status(done, ALBUM_PUBLISHING)
    vk, notifier = FakeVK(), FakeNotifier()

    tick(config, queue, vk, notifier, datetime(2026, 7, 28, 15, 0))

    assert "В очереди ещё альбомов: 1" in notifier.messages[0]


def test_next_album_starts_only_after_previous_is_done(tmp_path, queue):
    config = _config(tmp_path)
    first = queue.enqueue("https://soundcloud.com/a/sets/b", "First", "Artist", 1, None)
    queue.add_track(first, 1, "Track", "Artist", 60, "a.mp3", "a.jpg")
    queue.enqueue("https://soundcloud.com/a/sets/c", "Second", "Artist", 1, None)
    queue.set_album_status(first, ALBUM_PUBLISHING)
    queue.set_next_post_at(first, datetime(2026, 7, 28, 18, 0))
    vk, notifier = FakeVK(), FakeNotifier()

    # Пока первый занимает конвейер, второй не должен начать скачиваться.
    tick(config, queue, vk, notifier, datetime(2026, 7, 28, 15, 0))

    assert queue.active_album().title == "First"
    assert queue.next_pending_album().title == "Second"


def test_night_pause_also_blocks_starting_a_new_album(tmp_path, queue):
    """Сборник — такая же запись в сообществе, как трек: ночью он тоже ждёт.
    Раньше проверка стояла только на ветке треков, и плейлист, брошенный под
    утро, публиковался в 06:00 (поймано живым прогоном 2026-07-29)."""
    config = _config(tmp_path)
    queue.enqueue("https://soundcloud.com/a/sets/b", "Album", "Artist", 5, None)
    vk, notifier = FakeVK(), FakeNotifier()

    outcome = tick(config, queue, vk, notifier, datetime(2026, 7, 29, 5, 54, tzinfo=MOSCOW))

    assert outcome == "ночная пауза"
    assert vk.uploads == [], "ночью не должно быть даже загрузки видео"
    assert queue.next_pending_album().title == "Album", "альбом остался ждать утра"


def test_album_starts_once_the_night_is_over(tmp_path, queue):
    config = _config(tmp_path)
    queue.enqueue("https://soundcloud.com/a/sets/b", "Album", "Artist", 5, None)
    vk, notifier = FakeVK(), FakeNotifier()

    # 09:30 МСК — окно уже открыто, тик берётся за альбом (упрётся в сеть, но
    # важно, что он вообще дошёл до работы, а не отбился ночной паузой).
    outcome = tick(config, queue, vk, notifier, datetime(2026, 7, 29, 9, 30, tzinfo=MOSCOW))

    assert outcome != "ночная пауза"
