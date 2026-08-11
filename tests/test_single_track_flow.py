"""Находка автопоиска = один трек: без сборника, с глобальным интервалом между треками."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from app import album_publisher
from app.album_db import ALBUM_PUBLISHING, AlbumQueue
from app.album_publisher import POST_KIND_TRACK
from app.album_scheduler import now_msk, to_msk
from app.config import PostStyle, SoundCloudConfig
from app.soundcloud import Track


def _settings(tmp_path: Path) -> SoundCloudConfig:
    return SoundCloudConfig(
        min_interval_minutes=420,
        max_interval_minutes=660,
        quiet_start_hour=0,
        quiet_end_hour=0,
        max_posts_per_day=2,
        max_track_attempts=3,
        work_dir=tmp_path / "work",
        post=PostStyle(
            flag="🎧", title_suffix="Без цензуры", listen_label="", listen_url="",
            channel_label="", channel_url="", hashtag_template="{artist}_{name}",
            hashtag_group="", track_kind="Single", album_kind="Album",
        ),
    )


class _Config:
    def __init__(self, settings: SoundCloudConfig) -> None:
        self.soundcloud = settings


@pytest.fixture()
def queue(tmp_path) -> AlbumQueue:
    q = AlbumQueue(tmp_path / "queue.db")
    yield q
    q.close()


def _fake_track(tmp_path: Path) -> Track:
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"audio")
    cover = tmp_path / "track.jpg"
    cover.write_bytes(b"cover")
    return Track(
        position=1, title="Тебя Нежно Грубо", artist="TARAS",
        duration_s=219, audio_path=audio, cover_path=cover,
    )


def test_single_track_is_downloaded_without_publishing_a_compilation(
    queue, tmp_path, monkeypatch
):
    """Сборник из одного трека опубликовал бы то же аудио дважды — его быть не должно."""
    monkeypatch.setattr(album_publisher, "download_track", lambda *a, **k: _fake_track(tmp_path))
    monkeypatch.setattr(
        album_publisher, "_publish_compilation",
        lambda *a, **k: pytest.fail("сборник публиковаться не должен"),
    )
    album_id = queue.enqueue("u1", "Хит", "TARAS", 1, None, skip_compilation=True)

    result = album_publisher._start_album(
        _Config(_settings(tmp_path)), queue, None, None, queue.next_pending_album(), now_msk()
    )

    assert "скачан" in result
    album = queue.active_album()
    assert album.id == album_id
    assert album.status == ALBUM_PUBLISHING
    assert len(queue.tracks(album_id)) == 1


def test_next_single_waits_the_interval_after_the_previous_track(queue, tmp_path, monkeypatch):
    """У каждой находки свой альбом из одного трека. Если отсчитывать интервал от начала
    альбома, второй трек вышел бы сразу за первым — интервал схлопнулся бы в ноль."""
    monkeypatch.setattr(album_publisher, "download_track", lambda *a, **k: _fake_track(tmp_path))
    queue.log_post(POST_KIND_TRACK)
    queue.enqueue("u2", "Хит", "TARAS", 1, None, skip_compilation=True)
    now = now_msk()

    album_publisher._start_album(
        _Config(_settings(tmp_path)), queue, None, None, queue.next_pending_album(), now
    )

    moment = to_msk(queue.active_album().next_post_at)
    assert moment >= now + timedelta(minutes=419)


def test_first_ever_single_publishes_without_waiting(queue, tmp_path, monkeypatch):
    """Пустой post_log — публиковать нечему ждать: сообщество и так простаивало."""
    monkeypatch.setattr(album_publisher, "download_track", lambda *a, **k: _fake_track(tmp_path))
    queue.enqueue("u3", "Хит", "TARAS", 1, None, skip_compilation=True)
    now = now_msk()

    album_publisher._start_album(
        _Config(_settings(tmp_path)), queue, None, None, queue.next_pending_album(), now
    )

    assert to_msk(queue.active_album().next_post_at) <= now
