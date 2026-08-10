"""Очередь сборников-плейлистов с YouTube (SQLite).

Единственное место с SQL этой очереди — тот же принцип, что у `album_db.py`. Файл БД
общий с альбомным потоком: суточный предохранитель считает записи по одному `post_log`,
иначе два потока не видели бы публикаций друг друга.

Состояние живёт в БД, а не в памяти: тик — короткоживущий процесс из systemd-таймера.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PLAYLIST_PENDING = "pending"
PLAYLIST_DONE = "done"
PLAYLIST_FAILED = "failed"

POST_KIND_YT_PLAYLIST = "yt_playlist"


@dataclass
class PlaylistRow:
    id: int
    url: str
    title: str
    uploader: str
    source: str
    status: str
    attempts: int


class PlaylistQueue:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS yt_playlists (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                url          TEXT NOT NULL UNIQUE,
                title        TEXT NOT NULL DEFAULT '',
                uploader     TEXT NOT NULL DEFAULT '',
                source       TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL,
                attempts     INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL,
                published_at TEXT,
                post_url     TEXT,
                error        TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_yt_playlists_status ON yt_playlists(status, id);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add(self, url: str, title: str, uploader: str, source: str) -> bool:
        """True — плейлист был новым. UNIQUE по url делает повторный sync бесплатным."""
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO yt_playlists "
            "(url, title, uploader, source, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (url, title, uploader, source, PLAYLIST_PENDING, _now_iso()),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def next_pending(self) -> PlaylistRow | None:
        row = self._conn.execute(
            "SELECT * FROM yt_playlists WHERE status = ? ORDER BY id LIMIT 1",
            (PLAYLIST_PENDING,),
        ).fetchone()
        return _to_row(row) if row else None

    def pending_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM yt_playlists WHERE status = ?", (PLAYLIST_PENDING,)
        ).fetchone()
        return int(row["n"])

    def mark_published(self, playlist_id: int, post_url: str) -> None:
        self._conn.execute(
            "UPDATE yt_playlists SET status = ?, published_at = ?, post_url = ? WHERE id = ?",
            (PLAYLIST_DONE, _now_iso(), post_url, playlist_id),
        )
        self._conn.commit()

    def bump_attempt(self, playlist_id: int, limit: int, error: str) -> int:
        """Счётчик попыток; исчерпан — плейлист уходит в failed и не тормозит очередь."""
        self._conn.execute(
            "UPDATE yt_playlists SET attempts = attempts + 1, error = ? WHERE id = ?",
            (error[:500], playlist_id),
        )
        row = self._conn.execute(
            "SELECT attempts FROM yt_playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        attempts = int(row["attempts"])
        if attempts >= limit:
            self._conn.execute(
                "UPDATE yt_playlists SET status = ? WHERE id = ?",
                (PLAYLIST_FAILED, playlist_id),
            )
        self._conn.commit()
        return attempts

    def recent_titles(self, limit: int) -> list[str]:
        """Заголовки последних опубликованных сборников — чтобы не повторять шаблон."""
        rows = self._conn.execute(
            "SELECT title FROM yt_playlists WHERE status = ? "
            "ORDER BY published_at DESC LIMIT ?",
            (PLAYLIST_DONE, limit),
        ).fetchall()
        return [row["title"] for row in rows]


def _to_row(row: sqlite3.Row) -> PlaylistRow:
    return PlaylistRow(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        uploader=row["uploader"],
        source=row["source"],
        status=row["status"],
        attempts=row["attempts"],
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
