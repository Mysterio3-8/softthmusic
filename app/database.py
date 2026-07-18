from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATUS_PUBLISHED = "published"
STATUS_ERROR = "error"


class Database:
    """История обработанных роликов (SQLite). Гарант отсутствия повторов."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS videos (
                youtube_id   TEXT PRIMARY KEY,
                channel      TEXT NOT NULL,
                title        TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                status       TEXT NOT NULL,
                error_count  INTEGER NOT NULL DEFAULT 0,
                retry_at     TEXT
            )
            """
        )
        self._conn.commit()

    def is_published(self, youtube_id: str) -> bool:
        row = self._conn.execute(
            "SELECT status FROM videos WHERE youtube_id = ?", (youtube_id,)
        ).fetchone()
        return row is not None and row["status"] == STATUS_PUBLISHED

    def should_skip(self, youtube_id: str, now: datetime | None = None) -> bool:
        """Пропустить ролик, если он опубликован или ждёт отложенного ретрая."""
        now = now or datetime.now(timezone.utc)
        row = self._conn.execute(
            "SELECT status, retry_at FROM videos WHERE youtube_id = ?", (youtube_id,)
        ).fetchone()
        if row is None:
            return False
        if row["status"] == STATUS_PUBLISHED:
            return True
        if row["status"] == STATUS_ERROR and row["retry_at"]:
            return datetime.fromisoformat(row["retry_at"]) > now
        return False

    def mark_published(self, youtube_id: str, channel: str, title: str) -> None:
        self._conn.execute(
            """
            INSERT INTO videos (youtube_id, channel, title, processed_at, status, error_count, retry_at)
            VALUES (?, ?, ?, ?, ?, 0, NULL)
            ON CONFLICT(youtube_id) DO UPDATE SET
                status = excluded.status,
                title = excluded.title,
                processed_at = excluded.processed_at,
                retry_at = NULL
            """,
            (youtube_id, channel, title, _now_iso(), STATUS_PUBLISHED),
        )
        self._conn.commit()

    def mark_error(
        self, youtube_id: str, channel: str, title: str, retry_delays_minutes: list[int]
    ) -> int:
        """Отмечает ошибку и вычисляет время следующего ретрая. Возвращает error_count."""
        row = self._conn.execute(
            "SELECT error_count FROM videos WHERE youtube_id = ?", (youtube_id,)
        ).fetchone()
        error_count = (row["error_count"] if row else 0) + 1

        retry_at = _next_retry_at(error_count, retry_delays_minutes)
        self._conn.execute(
            """
            INSERT INTO videos (youtube_id, channel, title, processed_at, status, error_count, retry_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(youtube_id) DO UPDATE SET
                status = excluded.status,
                processed_at = excluded.processed_at,
                error_count = excluded.error_count,
                retry_at = excluded.retry_at
            """,
            (youtube_id, channel, title, _now_iso(), STATUS_ERROR, error_count, retry_at),
        )
        self._conn.commit()
        return error_count

    def close(self) -> None:
        self._conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_retry_at(error_count: int, delays_minutes: list[int]) -> str | None:
    """delays_minutes=[60,180]: первая ошибка -> +60м, вторая -> +180м, дальше нет."""
    index = error_count - 1
    if index >= len(delays_minutes):
        return None
    delay = delays_minutes[index]
    return (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat()
