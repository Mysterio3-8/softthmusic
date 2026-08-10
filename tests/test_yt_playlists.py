"""Поток сборников с YouTube: расписание, лимит, тексты записи и описания."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.album_db import AlbumQueue
from app.config import Config, PostStyle, SoundCloudConfig, YoutubePlaylistsConfig
from app.soundcloud import Track
from app.yt_playlist_db import POST_KIND_YT_PLAYLIST, PlaylistQueue
from app.yt_playlists import (
    build_description,
    build_post_text,
    build_title,
    _minutes_until_due,
    tick,
)


class FakeVK:
    def __init__(self, busy=False):
        self.busy = busy
        self.uploads = []
        self.posts = []

    def pool_is_busy(self):
        return self.busy

    def upload_video(self, file_path, name, description):
        self.uploads.append((name, description))
        return "video-1_1"

    def post_now(self, message, attachment):
        self.posts.append(message)
        return len(self.posts)


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send(self, text, chat_id=None):
        self.messages.append(text)
        return True


def _style(**overrides) -> PostStyle:
    base = dict(
        flag="🎧",
        title_suffix="Без цензуры",
        listen_label="♾️ Слушать в Telegram:",
        listen_url="https://t.me/tgram_music_bot",
        channel_label="📢 Канал:",
        channel_url="",
        hashtag_template="{artist}_{name}",
        hashtag_group="tgmusic",
        track_kind="Single",
        album_kind="Album",
        base_tags=["музыка", "плейлист"],
        search_phrases=["{q} слушать онлайн", "{q} скачать бесплатно"],
        post_tag_limit=5,
        video_tag_limit=12,
        service_block="♾️ Infinity Music — бот https://t.me/tgram_music_bot",
    )
    base.update(overrides)
    return PostStyle(**base)


def _playlists_config(tmp_path, **overrides) -> YoutubePlaylistsConfig:
    base = dict(
        enabled=True,
        sources=["тест"],
        discover_limit=5,
        max_tracks=5,
        max_posts_per_day=2,
        min_interval_minutes=420,
        max_interval_minutes=620,
        quiet_start_hour=0,
        quiet_end_hour=0,
        max_attempts=3,
        work_dir=tmp_path / "work",
        ready_dir=tmp_path / "ready",
        ready_keep_days=5,
        remote_host="vps",
        header="♾️ Плейлисты от Infinity Music",
        playlist_description="Сборник собран вручную.",
        title_templates=["Плейлист {year}: музыка на каждый день"],
    )
    base.update(overrides)
    return YoutubePlaylistsConfig(**base)


def _config(tmp_path, **overrides) -> Config:
    return Config(
        vk_group_token="g",
        vk_user_token="u",
        group_id=240295467,
        channels=["c"],
        max_height=480,
        posts_per_day=3,
        posts_per_run=1,
        publish_times=["09:00"],
        ad_block="",
        retry_delays_minutes=[60],
        database_path=tmp_path / "db.sqlite",
        downloads_dir=tmp_path / "dl",
        log_path=tmp_path / "log.txt",
        soundcloud=SoundCloudConfig(
            min_interval_minutes=180,
            max_interval_minutes=300,
            quiet_start_hour=0,
            quiet_end_hour=0,
            max_posts_per_day=3,
            max_track_attempts=3,
            work_dir=tmp_path / "sc",
            post=_style(),
        ),
        youtube_playlists=_playlists_config(tmp_path, **overrides),
    )


@pytest.fixture
def queues(tmp_path):
    posts = AlbumQueue(tmp_path / "db.sqlite")
    playlists = PlaylistQueue(tmp_path / "db.sqlite")
    yield posts, playlists
    posts.close()
    playlists.close()


def _tracks() -> list[Track]:
    return [
        Track(1, "Штиль", "Ария", 300, Path("a.mp3"), None),
        Track(2, "Осколок льда", "Ария", 240, Path("b.mp3"), None),
    ]


def test_disabled_stream_does_nothing(tmp_path, queues):
    posts, playlists = queues
    config = _config(tmp_path, enabled=False)

    assert tick(config, playlists, posts, FakeVK(), FakeNotifier()) == "поток сборников выключен"


def test_daily_limit_blocks_publishing(tmp_path, queues):
    posts, playlists = queues
    posts.log_post(POST_KIND_YT_PLAYLIST)
    posts.log_post(POST_KIND_YT_PLAYLIST)

    outcome = tick(_config(tmp_path), playlists, posts, FakeVK(), FakeNotifier())

    assert outcome == "суточный лимит сборников исчерпан"


def test_track_posts_do_not_eat_playlist_quota(tmp_path, queues):
    """У каждого потока свой счётчик: три трека не должны блокировать сборник."""
    posts, playlists = queues
    for _ in range(3):
        posts.log_post("track")

    outcome = tick(_config(tmp_path), playlists, posts, FakeVK(), FakeNotifier())

    assert outcome == "очередь сборников пуста"


def test_busy_token_pool_stops_before_heavy_work(tmp_path, queues):
    posts, playlists = queues
    playlists.add("https://youtube.com/playlist?list=1", "t", "u", "src")

    outcome = tick(_config(tmp_path), playlists, posts, FakeVK(busy=True), FakeNotifier())

    assert outcome == "личный токен занят — ждём следующего тика"


def test_quiet_hours_hold_the_stream(tmp_path, queues):
    posts, playlists = queues
    config = _config(tmp_path, quiet_start_hour=23, quiet_end_hour=9)

    outcome = tick(
        config, playlists, posts, FakeVK(), FakeNotifier(), datetime(2026, 8, 10, 3, 0)
    )

    assert outcome == "ночная пауза"


def test_interval_is_measured_from_the_previous_playlist(tmp_path, queues):
    posts, playlists = queues
    posts.log_post(POST_KIND_YT_PLAYLIST)
    settings = _playlists_config(tmp_path)

    from app.album_scheduler import now_msk

    assert _minutes_until_due(posts, settings, now_msk()) > 0
    # Через сутки интервал заведомо выдержан — какой бы бросок ни выпал.
    assert _minutes_until_due(posts, settings, now_msk() + timedelta(days=1)) == 0


def test_interval_roll_is_stable_between_calls(tmp_path, queues):
    """Бросок детерминирован временем прошлой публикации: если бросать заново на
    каждом тике, диапазон схлопывается в нижнюю границу."""
    posts, playlists = queues
    posts.log_post(POST_KIND_YT_PLAYLIST)
    settings = _playlists_config(tmp_path)

    from app.album_scheduler import now_msk

    moment = now_msk()
    assert _minutes_until_due(posts, settings, moment) == _minutes_until_due(
        posts, settings, moment
    )


def test_title_avoids_recently_used_templates():
    templates = ["A {year}", "B {year}"]
    now = datetime(2026, 8, 10)

    assert build_title(templates, ["A 2026"], now) == "B 2026"


def test_title_falls_back_when_all_templates_used():
    now = datetime(2026, 8, 10)
    assert build_title(["A {year}"], ["A 2026"], now) == "A 2026"


def test_post_text_is_short_and_has_bot_link(tmp_path):
    config = _config(tmp_path)
    text = build_post_text(config, "Плейлист 2026", 12)

    assert text.startswith("♾️ Плейлисты от Infinity Music Плейлист 2026")
    assert "https://t.me/tgram_music_bot" in text
    assert "#музыка@tgmusic" in text
    # Тайминги в записи не идут — они уезжают в описание видео (ТЗ 2026-08-10).
    assert "00:00" not in text


def test_description_has_timings_search_phrases_and_service(tmp_path):
    config = _config(tmp_path)
    description = build_description(
        config, "Плейлист 2026", "00:00 1. Ария — Штиль\n05:00 2. Ария — Осколок льда", _tracks()
    )

    assert "00:00 1. Ария — Штиль" in description
    assert "Ария слушать онлайн" in description
    assert "Infinity Music" in description
    assert description.rstrip().splitlines()[-1].startswith("#")


def test_description_is_bigger_than_post_text(tmp_path):
    config = _config(tmp_path)
    text = build_post_text(config, "Плейлист 2026", 2)
    description = build_description(config, "Плейлист 2026", "00:00 1. Ария — Штиль", _tracks())

    assert len(description) > len(text)
