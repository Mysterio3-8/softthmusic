"""Гейт состава плейлиста и короткая версия сборника для Telegram.

Оба механизма родились из одного простоя 14–16.08: поиск приносил подборки ЧАСОВЫХ
диджей-миксов, сборник из них физически не собирался (13 часов видео, юнит убивало по
таймауту на склейке), а владелец видел только «сборники не приходят».
"""
import pytest

from app.yt_playlist_db import PLAYLIST_PENDING, PLAYLIST_REJECTED, PlaylistQueue
from app.yt_playlists import build_delivery_caption
from app.yt_source import (
    proxy_candidates,
    PlaylistEntry,
    PlaylistUnsuitable,
    download_playlist,
    select_entries,
)


def _entries(*durations: int) -> list[PlaylistEntry]:
    return [
        PlaylistEntry(index=i, title=f"трек {i}", duration_s=d)
        for i, d in enumerate(durations, start=1)
    ]


def test_hour_long_mixes_are_dropped():
    """Живой замер плейлиста «CHILL РОССИЯ»: каждый «трек» — час с лишним."""
    chosen = select_entries(_entries(3514, 3435, 3659), max_track_seconds=900)

    assert chosen == []


def test_normal_songs_pass():
    chosen = select_entries(_entries(210, 185, 240), max_track_seconds=900)

    assert [entry.index for entry in chosen] == [1, 2, 3]


def test_total_budget_stops_the_compilation_from_growing():
    """Треки поодиночке проходят, а вместе дают многочасовой ролик — второй рубеж."""
    chosen = select_entries(
        _entries(600, 600, 600, 600), max_track_seconds=900, max_total_seconds=1300
    )

    assert [entry.index for entry in chosen] == [1, 2]


def test_entry_without_duration_is_skipped():
    """У YouTube это недоступный ролик или трансляция — качать неизвестно что нельзя."""
    assert select_entries(_entries(0, 200), max_track_seconds=900)[0].index == 2


def test_long_track_does_not_block_shorter_ones_behind_it():
    """Не берём — но и не обрываем перебор: следующая короткая песня ещё влезает."""
    chosen = select_entries(
        _entries(200, 1200, 200), max_track_seconds=900, max_total_seconds=600
    )

    assert [entry.index for entry in chosen] == [1, 3]


def test_download_refuses_unsuitable_playlist_without_downloading(tmp_path, monkeypatch):
    """Отказ должен стоить один плоский запрос — иначе на диск приедут гигабайты."""
    calls = []

    def fake_list(url, limit):
        calls.append(url)
        return _entries(3514, 3435, 3659)

    monkeypatch.setattr("app.yt_source.list_playlist_entries", fake_list)

    with pytest.raises(PlaylistUnsuitable) as error:
        download_playlist("https://youtube.com/playlist?list=x", tmp_path / "w")

    assert calls == ["https://youtube.com/playlist?list=x"]
    assert "3659" in str(error.value)


def test_rejected_playlist_is_not_revived(tmp_path):
    """`revive_failed` возвращает временные падения. Состав от повторов не меняется —
    отбракованный плейлист обязан остаться за бортом, иначе он вернётся навсегда."""
    queue = PlaylistQueue(tmp_path / "db.sqlite")
    queue.add("https://youtube.com/playlist?list=mix", "Миксы", "канал", "поиск")
    row = queue.next_pending()

    queue.reject(row.id, "часовые миксы")

    assert queue.revive_failed(older_than_hours=0) == 0
    assert queue.next_pending() is None
    queue.close()


def test_failed_playlist_is_still_revived(tmp_path):
    queue = PlaylistQueue(tmp_path / "db.sqlite")
    queue.add("https://youtube.com/playlist?list=ok", "Плейлист", "канал", "поиск")
    row = queue.next_pending()

    queue.bump_attempt(row.id, limit=1, error="занят токен")

    assert queue.revive_failed(older_than_hours=0) == 1
    assert queue.next_pending().status == PLAYLIST_PENDING
    queue.close()


def test_reject_sets_its_own_status(tmp_path):
    queue = PlaylistQueue(tmp_path / "db.sqlite")
    queue.add("https://youtube.com/playlist?list=mix", "Миксы", "канал", "поиск")
    row = queue.next_pending()

    queue.reject(row.id, "часовые миксы")

    stored = queue._conn.execute(
        "SELECT status FROM yt_playlists WHERE id = ?", (row.id,)
    ).fetchone()
    assert stored["status"] == PLAYLIST_REJECTED
    queue.close()


def test_delivery_caption_says_the_file_is_shortened():
    """Молча прислать файл короче опубликованного значит заставить владельца гадать,
    почему длительности не сходятся."""
    caption = build_delivery_caption("Плейлист 2026", part_of=(8, 15))

    assert "8" in caption and "15" in caption
    assert "полный" in caption


def test_delivery_caption_without_truncation_is_unchanged():
    assert "Готов к заливке" in build_delivery_caption("Плейлист 2026")


def test_proxy_candidates_default_to_the_single_configured_exit(monkeypatch):
    """Переменной с портами нет → поведение прежнее, одна попытка."""
    monkeypatch.setenv("YT_PROXY", "socks5://127.0.0.1:10808")
    monkeypatch.delenv("YT_PROXY_PORTS", raising=False)

    assert proxy_candidates() == ["socks5://127.0.0.1:10808"]


def test_proxy_candidates_try_spare_exits_then_direct(monkeypatch):
    """Основной выход первый, дубля нет, прямой путь замыкает: он иногда проходит,
    а «не пробовать вовсе» гарантирует сутки без сборника."""
    monkeypatch.setenv("YT_PROXY", "socks5://127.0.0.1:10808")
    monkeypatch.setenv("YT_PROXY_PORTS", "10808, 10813 ,10811")

    assert proxy_candidates() == [
        "socks5://127.0.0.1:10808",
        "socks5://127.0.0.1:10813",
        "socks5://127.0.0.1:10811",
        None,
    ]


def test_download_switches_exit_when_the_first_one_returns_nothing(tmp_path, monkeypatch):
    """🔴 Живой случай 16.08: выход отдаёт метаданные, но CDN на каждом треке шлёт 403.
    Проверять выход метаданными бесполезно — он ответит «жив», поэтому переключаемся
    именно по пустому результату скачивания."""
    monkeypatch.setenv("YT_PROXY", "socks5://127.0.0.1:10808")
    monkeypatch.setenv("YT_PROXY_PORTS", "10808,10813")
    monkeypatch.setattr("app.yt_source.list_playlist_entries", lambda url, limit: _entries(200, 210, 220, 230, 240))

    used: list[str | None] = []

    class FakeYDL:
        def __init__(self, options):
            used.append(options.get("proxy"))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            if used[-1] == "socks5://127.0.0.1:10808":
                raise RuntimeError("HTTP Error 403: Forbidden")
            return {"entries": [{"id": "a"}]}

    monkeypatch.setattr("app.yt_source.yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr(
        "app.yt_source.collect_tracks",
        lambda entries, target: ["трек"] if entries else [],
    )

    tracks = download_playlist("https://youtube.com/playlist?list=x", tmp_path / "w")

    assert tracks == ["трек"]
    assert used == ["socks5://127.0.0.1:10808", "socks5://127.0.0.1:10813"]
