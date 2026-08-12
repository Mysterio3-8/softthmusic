"""Очередь сборников не должна пустеть навсегда.

Владелец 2026-08-12: сборников нет полтора суток. Механика: `add()` вставляет через
INSERT OR IGNORE по уникальному url, упавший плейлист остаётся в таблице, а поиск
YouTube по тем же запросам приносит тот же набор ссылок — значит после того, как все
найденные однажды провалились, очередь пустеет насовсем.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.yt_playlist_db import PLAYLIST_FAILED, PlaylistQueue


@pytest.fixture()
def queue(tmp_path) -> PlaylistQueue:
    q = PlaylistQueue(tmp_path / "playlists.db")
    yield q
    q.close()


def _age(queue: PlaylistQueue, url: str, hours: float) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    queue._conn.execute("UPDATE yt_playlists SET created_at = ? WHERE url = ?", (stamp, url))
    queue._conn.commit()


def _fail(queue: PlaylistQueue, url: str) -> None:
    queue._conn.execute(
        "UPDATE yt_playlists SET status = ?, attempts = 3 WHERE url = ?", (PLAYLIST_FAILED, url)
    )
    queue._conn.commit()


def test_old_failed_playlist_returns_to_the_queue(queue):
    queue.add("https://yt/1", "Плейлист", "Канал", "запрос")
    _fail(queue, "https://yt/1")
    _age(queue, "https://yt/1", hours=20)

    assert queue.revive_failed(older_than_hours=12) == 1
    assert queue.pending_count() == 1


def test_attempts_are_reset_on_revive(queue):
    """Иначе плейлист тут же снова упрётся в потолок попыток и вернётся в failed."""
    queue.add("https://yt/1", "Плейлист", "Канал", "запрос")
    _fail(queue, "https://yt/1")
    _age(queue, "https://yt/1", hours=20)

    queue.revive_failed(older_than_hours=12)

    row = queue._conn.execute("SELECT attempts FROM yt_playlists").fetchone()
    assert row["attempts"] == 0


def test_recently_failed_playlist_is_left_alone(queue):
    """Свежее падение почти наверняка повторится — не тратим на него тик."""
    queue.add("https://yt/1", "Плейлист", "Канал", "запрос")
    _fail(queue, "https://yt/1")
    _age(queue, "https://yt/1", hours=2)

    assert queue.revive_failed(older_than_hours=12) == 0
    assert queue.pending_count() == 0


def test_published_playlist_is_never_revived(queue):
    """Опубликованный сборник поднимать нельзя — вышел бы второй раз."""
    queue.add("https://yt/1", "Плейлист", "Канал", "запрос")
    queue.mark_published(1, "https://vk.com/wall-1_1", published_title="Наше название")
    _age(queue, "https://yt/1", hours=99)

    assert queue.revive_failed(older_than_hours=12) == 0
    assert queue.pending_count() == 0


def test_resync_cannot_re_add_a_failed_playlist(queue):
    """Причина всей проблемы, зафиксированная тестом: повторный sync не возвращает
    упавший плейлист, потому что url уникален и строка уже есть."""
    queue.add("https://yt/1", "Плейлист", "Канал", "запрос")
    _fail(queue, "https://yt/1")

    added_again = queue.add("https://yt/1", "Плейлист", "Канал", "запрос")

    assert added_again is False
    assert queue.pending_count() == 0
