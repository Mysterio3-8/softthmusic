"""Заказ сборника по жанру из бота (ТЗ владельца 2026-08-18).

Кнопка «🎼 Сборник по жанру» кладёт строку в ту же очередь, что и автосборники, но с
меткой `заказ`. Проверяем ровно то, что владелец увидит: заказ выходит раньше чужих
плейлистов, порог популярности к нему не применяется, коротким он тоже публикуется,
а не собравшийся — приходит сообщением.
"""
from datetime import datetime
from pathlib import Path

import pytest

from app.config import GenreConfig, _build_genres
from app.sc_compilation import SC_URL_PREFIX, genre_query
from app.soundcloud import Track
from app.yt_playlist_db import (
    MAX_PENDING_REQUESTS,
    PLAYLIST_PENDING,
    SOURCE_REQUEST,
    PlaylistQueue,
)
from app import yt_playlists
from app.yt_playlists import REQUEST_MIN_TRACKS, build_compilation, is_request
from app.yt_source import PlaylistUnsuitable

from tests.test_yt_playlists import (  # переиспользуем готовые заглушки потока
    FakeNotifier,
    _config,
    queues,  # noqa: F401 — фикстура
)


class _FakeVK:
    def pool_is_busy(self):
        return False


@pytest.fixture
def queue(tmp_path):
    q = PlaylistQueue(tmp_path / "req.sqlite")
    yield q
    q.close()


def _config_with_genres(tmp_path, **overrides):
    config = _config(tmp_path, **overrides)
    object.__setattr__(
        config.soundcloud,
        "genres",
        [
            GenreConfig(name="Фонк", query="русский фонк"),
            GenreConfig(name="Рэп", query="русский рэп"),
        ],
    )
    return config


def _fake_tracks() -> list[Track]:
    return [Track(1, "Трек", "Артист", 200, Path("a.mp3"), None)]


# --- очередь -----------------------------------------------------------------


def test_request_lands_in_the_same_queue_with_its_own_mark(queue):
    assert queue.add_request("Фонк") is True

    row = queue.next_requested()
    assert row.title == "Фонк"
    assert row.source == SOURCE_REQUEST
    assert row.status == PLAYLIST_PENDING
    assert row.url.startswith(SC_URL_PREFIX)


def test_requests_are_served_in_the_order_they_were_tapped(queue):
    queue.add_request("Фонк", now=datetime(2026, 8, 18, 10, 0, 0))
    queue.add_request("Рэп", now=datetime(2026, 8, 18, 10, 0, 5))

    assert queue.next_requested().title == "Фонк"


def test_third_request_is_refused(queue):
    for i in range(MAX_PENDING_REQUESTS):
        assert queue.add_request("Фонк", now=datetime(2026, 8, 18, 10, 0, i)) is True

    assert queue.add_request("Рэп") is False
    assert queue.pending_requests() == MAX_PENDING_REQUESTS


def test_published_request_frees_a_slot(queue):
    queue.add_request("Фонк")
    queue.mark_published(queue.next_requested().id, "https://vk.com/wall-1_1", "Фонк ТОП-12")

    assert queue.pending_requests() == 0


# --- сборка ------------------------------------------------------------------


def test_requested_genre_beats_the_random_one(tmp_path, queue, monkeypatch):
    """Жанр берётся из строки заказа, а не жребием, и ищется ЗАПРОСОМ из конфига."""
    seen = {}

    def fake_collect(sources, work_dir, **kwargs):
        seen.update(sources=sources, **kwargs)
        return _fake_tracks()

    monkeypatch.setattr(yt_playlists, "collect_sc_tracks", fake_collect)
    monkeypatch.setattr(yt_playlists, "_ensure_own_covers", lambda tracks: None)
    monkeypatch.setattr(
        yt_playlists, "_render", lambda tracks, work_dir, limit: (work_dir / "v.mp4", None)
    )
    queue.add_request("Фонк")

    compilation = build_compilation(
        _config_with_genres(tmp_path),
        queue,
        queue.next_requested(),
        tmp_path / "w",
        datetime(2026, 8, 18, 1, 0),
    )

    assert seen["sources"] == ["русский фонк"]
    assert seen["min_plays"] == 0, "у заказа порог популярности не применяется"
    assert seen["min_tracks"] == REQUEST_MIN_TRACKS
    assert compilation.tracks


def test_auto_compilation_keeps_the_popularity_gate(tmp_path, queue, monkeypatch):
    """Автосборник не должен зацепить послаблений заказа."""
    seen = {}

    def fake_collect(sources, work_dir, **kwargs):
        seen.update(kwargs)
        return _fake_tracks()

    monkeypatch.setattr(yt_playlists, "collect_sc_tracks", fake_collect)
    monkeypatch.setattr(yt_playlists, "_ensure_own_covers", lambda tracks: None)
    monkeypatch.setattr(
        yt_playlists, "_render", lambda tracks, work_dir, limit: (work_dir / "v.mp4", None)
    )
    config = _config_with_genres(tmp_path)
    object.__setattr__(config.soundcloud.discovery, "sources", ["русские хиты"])
    queue.add(f"{SC_URL_PREFIX}20260818", "Своя подборка SoundCloud", "SoundCloud", "автосборник")

    build_compilation(
        config, queue, queue.next_pending(), tmp_path / "w", datetime(2026, 8, 18, 1, 0)
    )

    assert seen["min_plays"] == config.soundcloud.discovery.min_plays
    assert seen["min_tracks"] == config.youtube_playlists.min_tracks


# --- порядок в тике ----------------------------------------------------------


def test_request_is_taken_before_foreign_playlists(tmp_path, queues, monkeypatch):
    """В очереди 65 чужих плейлистов — в общем порядке кнопка ждала бы месяц."""
    posts, playlists = queues
    for i in range(5):
        playlists.add(f"https://youtube.com/playlist?list={i}", "чужой", "канал", "поиск")
    playlists.add_request("Фонк")

    taken = {}

    def fake_process(config, playlists_, posts_, vk, notifier, playlist, now):
        taken["row"] = playlist
        return "готово", False

    monkeypatch.setattr(yt_playlists, "_process", fake_process)
    yt_playlists.tick(
        _config_with_genres(tmp_path),
        playlists,
        posts,
        _FakeVK(),
        FakeNotifier(),
        now=datetime(2026, 8, 18, 1, 0),
    )

    assert taken["row"].title == "Фонк"
    assert is_request(taken["row"])


# --- отчёт об ошибке ---------------------------------------------------------


def test_failed_request_is_reported_to_the_owner(tmp_path, queues, monkeypatch):
    posts, playlists = queues
    playlists.add_request("Фонк")
    notifier = FakeNotifier()

    def boom(*args, **kwargs):
        raise PlaylistUnsuitable("SoundCloud не отдал достаточно треков для сборника")

    monkeypatch.setattr(yt_playlists, "build_compilation", boom)
    yt_playlists._process(
        _config_with_genres(tmp_path),
        playlists,
        posts,
        _FakeVK(),
        notifier,
        playlists.next_requested(),
        datetime(2026, 8, 18, 1, 0),
    )

    assert notifier.messages and "Фонк" in notifier.messages[0]


def test_failed_auto_compilation_stays_silent(tmp_path, queues, monkeypatch):
    """Автосборник владелец не заказывал — сообщать не о чем."""
    posts, playlists = queues
    playlists.add(
        f"{SC_URL_PREFIX}20260818", "Своя подборка SoundCloud", "SoundCloud", "автосборник"
    )
    notifier = FakeNotifier()

    def boom(*args, **kwargs):
        raise PlaylistUnsuitable("мало треков")

    monkeypatch.setattr(yt_playlists, "build_compilation", boom)
    yt_playlists._process(
        _config_with_genres(tmp_path),
        playlists,
        posts,
        _FakeVK(),
        notifier,
        playlists.next_pending(),
        datetime(2026, 8, 18, 1, 0),
    )

    assert notifier.messages == []


# --- конфиг ------------------------------------------------------------------


def test_genres_accept_both_plain_strings_and_pairs():
    genres = _build_genres(["Фонк", {"name": "Рэп", "query": "русский рэп"}, {"name": "  "}])

    assert [(g.name, g.query) for g in genres] == [("Фонк", "Фонк"), ("Рэп", "русский рэп")]


def test_query_falls_back_to_the_genre_name_when_it_left_the_config():
    """Жанр убрали из конфига, а заказ уже в очереди — сборник всё равно собирается."""
    assert genre_query([GenreConfig("Фонк", "русский фонк")], "Шансон") == "Шансон"
    assert genre_query([GenreConfig("Фонк", "русский фонк")], "фонк") == "русский фонк"
