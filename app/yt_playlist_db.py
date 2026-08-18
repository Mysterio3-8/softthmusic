"""Очередь сборников-плейлистов с YouTube (SQLite).

Единственное место с SQL этой очереди — тот же принцип, что у `album_db.py`. Файл БД
общий с альбомным потоком: суточный предохранитель считает записи по одному `post_log`,
иначе два потока не видели бы публикаций друг друга.

Состояние живёт в БД, а не в памяти: тик — короткоживущий процесс из systemd-таймера.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLAYLIST_PENDING = "pending"
PLAYLIST_DONE = "done"
PLAYLIST_FAILED = "failed"
PLAYLIST_REJECTED = "rejected"
"""Плейлист не подходит ПО СОСТАВУ (часовые миксы, слишком мало песен).

Отдельно от `failed` ради `revive_failed`: тот возвращает в очередь временные падения
(занятый токен, OOM, оборванная закачка), и отбракованный состав он возвращал бы вечно —
каждый раз заново упираясь в тот же гейт."""

SOURCE_REQUEST = "заказ"
"""Метка строки, заказанной руками из бота («собрать сборник по жанру»).

Отдельной таблицы у заказов нет намеренно: строка в общей очереди даёт им весь готовый
учёт — отдан ли файл, память о вышедших песнях, защита от повторов названий, попытки.
Второй, параллельный учёт разошёлся бы с первым."""

MAX_PENDING_REQUESTS = 2
"""Сколько заказов можно накопить. Ровно суточная квота сборников: третий заказ всё
равно ждал бы следующей ночи, а владелец к тому времени забудет, что его делал."""

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
    delivered: bool = False
    """Файл уже уходил владельцу. Нужен, потому что отдаём мы его ДО публикации в VK:
    если публикация сорвётся (занят токен) и плейлист вернётся в очередь, повторная
    попытка не должна прислать тот же сборник вторым файлом."""


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
        self._add_missing_columns()
        self._ensure_published_tracks()
        self._conn.commit()

    def _add_missing_columns(self) -> None:
        """CREATE TABLE IF NOT EXISTS не мигрирует уже существующую таблицу — новая
        колонка на проде не появилась бы, и любой SELECT падал бы «no such column»."""
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(yt_playlists)")}
        if "delivered_at" not in existing:
            self._conn.execute("ALTER TABLE yt_playlists ADD COLUMN delivered_at TEXT")
        if "published_title" not in existing:
            self._conn.execute("ALTER TABLE yt_playlists ADD COLUMN published_title TEXT")

    def _ensure_published_tracks(self) -> None:
        """Память о том, какие ПЕСНИ уже выходили.

        🔴 Жалоба владельца 2026-08-17: «почему в сборниках треки одинаковые». Плейлисты
        разные — каждый публикуется один раз, — но очередь целиком набита выдачей одного
        запроса («русский рэп плейлист 2026»), а такие подборки пересекаются по составу
        на две трети. Без памяти о песнях каждый следующий сборник повторял предыдущий."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS published_tracks (
                track_key    TEXT PRIMARY KEY,
                published_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def remember_tracks(self, keys: list[str]) -> int:
        """Запомнить вышедшие песни. Повтор ключа не ошибка — обновляем дату."""
        now = _now_iso()
        rows = [(key, now) for key in keys if key]
        if not rows:
            return 0
        self._conn.executemany(
            "INSERT INTO published_tracks (track_key, published_at) VALUES (?, ?) "
            "ON CONFLICT(track_key) DO UPDATE SET published_at = excluded.published_at",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def recent_track_keys(self, limit: int = 400) -> set[str]:
        """Песни последних сборников. Не «все за всё время»: через полгода запрет на
        повтор выел бы весь популярный репертуар, и собирать сборники стало бы не из чего."""
        rows = self._conn.execute(
            "SELECT track_key FROM published_tracks ORDER BY published_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {row["track_key"] for row in rows}

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

    def add_request(self, genre: str, now: datetime | None = None) -> bool:
        """Заказать сборник жанра `genre`. False — заказов уже максимум.

        В `title` кладём ИМЯ жанра, а не поисковый запрос: запрос берётся из конфига в
        момент сборки, и список жанров правится из бота — сохранённый запрос разъехался
        бы с конфигом молча."""
        if self.pending_requests() >= MAX_PENDING_REQUESTS:
            return False
        moment = now or datetime.now(timezone.utc)
        url = f"sc:auto:{moment.strftime('%Y%m%d%H%M%S')}"
        return self.add(url, genre, "SoundCloud", SOURCE_REQUEST)

    def pending_requests(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM yt_playlists WHERE status = ? AND source = ?",
            (PLAYLIST_PENDING, SOURCE_REQUEST),
        ).fetchone()
        return int(row["n"])

    def next_requested(self) -> PlaylistRow | None:
        """Заказ ПО ПОРЯДКУ нажатия, а не случайно (в отличие от `next_pending`).

        Владелец помнит, какой жанр нажал первым, и ждёт именно его. Случайный порядок
        двух заказов выглядел бы как «бот меня не послушал»."""
        row = self._conn.execute(
            "SELECT * FROM yt_playlists WHERE status = ? AND source = ? ORDER BY id LIMIT 1",
            (PLAYLIST_PENDING, SOURCE_REQUEST),
        ).fetchone()
        return _to_row(row) if row else None

    def next_pending(self) -> PlaylistRow | None:
        """Случайный плейлист из очереди, а не самый старый.

        ТЗ владельца 2026-08-17: «сборники пускай рандомные берёт». По порядку id очередь
        разбиралась ровно так, как её насыпал поиск: подряд шли соседние результаты одного
        запроса, то есть самые похожие друг на друга подборки. Случайный выбор разносит их
        и без всякой дополнительной логики делает соседние сборники разными.

        Отбраковка и повторные попытки от этого не страдают: у плейлиста своё состояние в
        строке, а не место в очереди."""
        row = self._conn.execute(
            "SELECT * FROM yt_playlists WHERE status = ? ORDER BY RANDOM() LIMIT 1",
            (PLAYLIST_PENDING,),
        ).fetchone()
        return _to_row(row) if row else None

    def pending_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM yt_playlists WHERE status = ?", (PLAYLIST_PENDING,)
        ).fetchone()
        return int(row["n"])

    def mark_delivered(self, playlist_id: int) -> None:
        """Файл отдан владельцу. Ставится ДО публикации в VK — см. PlaylistRow.delivered."""
        self._conn.execute(
            "UPDATE yt_playlists SET delivered_at = ? WHERE id = ?", (_now_iso(), playlist_id)
        )
        self._conn.commit()

    def mark_published(self, playlist_id: int, post_url: str, published_title: str = "") -> None:
        """`published_title` — НАШЕ название сборника, а не название плейлиста-донора.

        Раньше не сохранялось вовсе, и защита от повторов (`recent_titles`) сравнивала
        свежесобранное название с названиями ДОНОРОВ — совпасть они не могли никогда,
        поэтому повторов ничто не мешало. Ровно это владелец и увидел 2026-08-11:
        «у плейлистов одинаковые название одни и те же»."""
        self._conn.execute(
            "UPDATE yt_playlists SET status = ?, published_at = ?, post_url = ?, "
            "published_title = ? WHERE id = ?",
            (PLAYLIST_DONE, _now_iso(), post_url, published_title, playlist_id),
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

    def reject(self, playlist_id: int, reason: str) -> None:
        """Плейлист не годится по составу — убрать из очереди насовсем.

        Не `bump_attempt`: попытки нужны там, где повтор может помочь, а состав
        плейлиста от повторов не меняется. Каждая лишняя попытка здесь — это ещё один
        холостой запрос к YouTube и ещё один тик, в котором сборник не вышел."""
        self._conn.execute(
            "UPDATE yt_playlists SET status = ?, error = ? WHERE id = ?",
            (PLAYLIST_REJECTED, reason[:500], playlist_id),
        )
        self._conn.commit()

    def revive_failed(self, older_than_hours: int) -> int:
        """Вернуть в очередь давно упавшие плейлисты. Возвращает число возвращённых.

        Зачем это вообще нужно. `add()` вставляет через INSERT OR IGNORE по уникальному
        url, то есть **упавший плейлист остаётся в таблице навсегда** и повторным sync
        уже не добавится. А поисковая выдача YouTube по одним и тем же запросам приносит
        примерно один и тот же набор ссылок. Значит, стоит всем найденным плейлистам
        один раз провалиться — и очередь пустеет НАВСЕГДА: sync исправно отвечает
        «новых 0», тик исправно отвечает «очередь пуста», а сборники не выходят.
        Ровно это владелец увидел 2026-08-12: последний сборник 11.08 в 01:19.

        Почему это оправдано: причины падений почти всегда временные — занятый токен
        пула, OOM во время рендера на 961 МБ, оборванная закачка. Плейлист, упавший
        сутки назад, сегодня может собраться нормально. Счётчик попыток сбрасываем,
        иначе он тут же снова упрётся в потолок."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
        cursor = self._conn.execute(
            "UPDATE yt_playlists SET status = ?, attempts = 0 "
            "WHERE status = ? AND created_at < ?",
            (PLAYLIST_PENDING, PLAYLIST_FAILED, cutoff),
        )
        self._conn.commit()
        return cursor.rowcount

    def recent_titles(self, limit: int) -> list[str]:
        """НАШИ названия последних опубликованных сборников — чтобы их не повторять."""
        rows = self._conn.execute(
            "SELECT published_title FROM yt_playlists "
            "WHERE status = ? AND published_title IS NOT NULL AND published_title != '' "
            "ORDER BY published_at DESC LIMIT ?",
            (PLAYLIST_DONE, limit),
        ).fetchall()
        return [row["published_title"] for row in rows]


def _to_row(row: sqlite3.Row) -> PlaylistRow:
    return PlaylistRow(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        uploader=row["uploader"],
        source=row["source"],
        status=row["status"],
        attempts=row["attempts"],
        delivered=bool(row["delivered_at"]),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
