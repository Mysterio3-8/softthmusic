"""Очередь альбомов SoundCloud (SQLite).

Отдельный модуль от `database.py`: там дедуп YouTube-роликов, здесь очередь
альбомов. Разные ответственности — разные файлы, хотя файл БД один.

Состояние очереди живёт в БД, а не в памяти процесса: тик — короткоживущий
процесс из systemd-таймера, между тиками ничего не держится.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ALBUM_PENDING = "pending"
ALBUM_DOWNLOADING = "downloading"
ALBUM_PUBLISHING = "publishing"
ALBUM_DONE = "done"
ALBUM_FAILED = "failed"

TRACK_PENDING = "pending"
TRACK_PUBLISHED = "published"
TRACK_ERROR = "error"

# Альбом в этих статусах занимает конвейер — новый в работу не берём.
ACTIVE_ALBUM_STATUSES = (ALBUM_DOWNLOADING, ALBUM_PUBLISHING)


@dataclass
class AlbumRow:
    id: int
    url: str
    title: str
    artist: str
    status: str
    tracks_total: int
    next_post_at: datetime | None
    chat_id: int | None


@dataclass
class TrackRow:
    id: int
    album_id: int
    position: int
    title: str
    artist: str
    duration_s: int
    audio_path: str
    cover_path: str
    status: str
    error_count: int


class AlbumQueue:
    """Очередь альбомов и треков. Единственное место, где трогается их SQL."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS albums (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                url          TEXT NOT NULL,
                title        TEXT NOT NULL DEFAULT '',
                artist       TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL,
                tracks_total INTEGER NOT NULL DEFAULT 0,
                next_post_at TEXT,
                chat_id      INTEGER,
                created_at   TEXT NOT NULL,
                finished_at  TEXT,
                error        TEXT
            );
            CREATE TABLE IF NOT EXISTS album_tracks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                album_id     INTEGER NOT NULL REFERENCES albums(id),
                position     INTEGER NOT NULL,
                title        TEXT NOT NULL,
                artist       TEXT NOT NULL DEFAULT '',
                duration_s   INTEGER NOT NULL DEFAULT 0,
                audio_path   TEXT NOT NULL DEFAULT '',
                cover_path   TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL,
                error_count  INTEGER NOT NULL DEFAULT 0,
                published_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tracks_album ON album_tracks(album_id, position);
            CREATE TABLE IF NOT EXISTS post_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                kind       TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def log_post(self, kind: str) -> None:
        """Одна строка на каждую запись в VK — основа суточного предохранителя."""
        self._conn.execute(
            "INSERT INTO post_log (kind, created_at) VALUES (?, ?)", (kind, _now_iso())
        )
        self._conn.commit()

    def posts_since(self, moment: datetime) -> int:
        """created_at хранится в UTC — границу приводим туда же.

        Сравнение идёт строками, поэтому смешивать смещения нельзя: «+03:00» и
        «+00:00» лексикографически сравниваются по цифрам, а не по моменту времени.
        """
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM post_log WHERE created_at >= ?", (_to_utc_iso(moment),)
        ).fetchone()
        return int(row["n"])

    def enqueue(self, url: str, title: str, artist: str, tracks_total: int, chat_id: int | None) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO albums (url, title, artist, status, tracks_total, chat_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (url, title, artist, ALBUM_PENDING, tracks_total, chat_id, _now_iso()),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def active_album(self) -> AlbumRow | None:
        """Альбом, который сейчас в работе. Их всегда не больше одного."""
        placeholders = ",".join("?" * len(ACTIVE_ALBUM_STATUSES))
        row = self._conn.execute(
            f"SELECT * FROM albums WHERE status IN ({placeholders}) ORDER BY id LIMIT 1",
            ACTIVE_ALBUM_STATUSES,
        ).fetchone()
        return _to_album(row) if row else None

    def next_pending_album(self) -> AlbumRow | None:
        row = self._conn.execute(
            "SELECT * FROM albums WHERE status = ? ORDER BY id LIMIT 1", (ALBUM_PENDING,)
        ).fetchone()
        return _to_album(row) if row else None

    def pending_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM albums WHERE status = ?", (ALBUM_PENDING,)
        ).fetchone()
        return int(row["n"])

    def set_album_status(self, album_id: int, status: str, error: str = "") -> None:
        finished = _now_iso() if status in (ALBUM_DONE, ALBUM_FAILED) else None
        self._conn.execute(
            "UPDATE albums SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, error, finished, album_id),
        )
        self._conn.commit()

    def set_album_meta(self, album_id: int, title: str, artist: str, tracks_total: int) -> None:
        self._conn.execute(
            "UPDATE albums SET title = ?, artist = ?, tracks_total = ? WHERE id = ?",
            (title, artist, tracks_total, album_id),
        )
        self._conn.commit()

    def set_next_post_at(self, album_id: int, moment: datetime) -> None:
        self._conn.execute(
            "UPDATE albums SET next_post_at = ? WHERE id = ?", (moment.isoformat(), album_id)
        )
        self._conn.commit()

    def add_track(
        self,
        album_id: int,
        position: int,
        title: str,
        artist: str,
        duration_s: int,
        audio_path: str,
        cover_path: str,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO album_tracks
                (album_id, position, title, artist, duration_s, audio_path, cover_path, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (album_id, position, title, artist, duration_s, audio_path, cover_path, TRACK_PENDING),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def tracks(self, album_id: int) -> list[TrackRow]:
        rows = self._conn.execute(
            "SELECT * FROM album_tracks WHERE album_id = ? ORDER BY position", (album_id,)
        ).fetchall()
        return [_to_track(row) for row in rows]

    def next_pending_track(self, album_id: int) -> TrackRow | None:
        row = self._conn.execute(
            """
            SELECT * FROM album_tracks
            WHERE album_id = ? AND status = ?
            ORDER BY position LIMIT 1
            """,
            (album_id, TRACK_PENDING),
        ).fetchone()
        return _to_track(row) if row else None

    def pending_track_count(self, album_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM album_tracks WHERE album_id = ? AND status = ?",
            (album_id, TRACK_PENDING),
        ).fetchone()
        return int(row["n"])

    def mark_track_published(self, track_id: int) -> None:
        self._conn.execute(
            "UPDATE album_tracks SET status = ?, published_at = ? WHERE id = ?",
            (TRACK_PUBLISHED, _now_iso(), track_id),
        )
        self._conn.commit()

    def mark_track_error(self, track_id: int, max_attempts: int) -> int:
        """Считает попытку. Исчерпав лимит, трек уходит в error и не мешает очереди."""
        row = self._conn.execute(
            "SELECT error_count FROM album_tracks WHERE id = ?", (track_id,)
        ).fetchone()
        error_count = (row["error_count"] if row else 0) + 1
        status = TRACK_ERROR if error_count >= max_attempts else TRACK_PENDING
        self._conn.execute(
            "UPDATE album_tracks SET error_count = ?, status = ? WHERE id = ?",
            (error_count, status, track_id),
        )
        self._conn.commit()
        return error_count

    def close(self) -> None:
        self._conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_utc_iso(moment: datetime) -> str:
    """Наивный момент считаем местным (МСК на проде), аware — переводим в UTC."""
    aware = moment if moment.tzinfo else moment.astimezone()
    return aware.astimezone(timezone.utc).isoformat()


def _to_album(row: sqlite3.Row) -> AlbumRow:
    raw_next = row["next_post_at"]
    return AlbumRow(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        artist=row["artist"],
        status=row["status"],
        tracks_total=row["tracks_total"],
        next_post_at=datetime.fromisoformat(raw_next) if raw_next else None,
        chat_id=row["chat_id"],
    )


def _to_track(row: sqlite3.Row) -> TrackRow:
    return TrackRow(
        id=row["id"],
        album_id=row["album_id"],
        position=row["position"],
        title=row["title"],
        artist=row["artist"],
        duration_s=row["duration_s"],
        audio_path=row["audio_path"],
        cover_path=row["cover_path"],
        status=row["status"],
        error_count=row["error_count"],
    )
