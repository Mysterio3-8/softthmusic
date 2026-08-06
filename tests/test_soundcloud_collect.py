"""Сопоставление записей yt-dlp со скачанными файлами."""
from pathlib import Path

from app.soundcloud import _collect_tracks


def _touch(directory: Path, stem: str) -> None:
    (directory / f"{stem}.mp3").write_bytes(b"x")


def test_collects_tracks_when_some_entries_failed(tmp_path):
    """Регрессия 2026-08-06: имя файла yt-dlp даёт по playlist_index (номер среди ВСЕХ
    записей), а сбор шёл по счётчику успешных. Любой упавший трек — а SoundCloud отдаёт
    часть треков под DRM — сдвигал нумерацию, и дальше не находился НИ ОДИН файл:
    альбом падал с «Ни один трек не скачался» при реально скачанных mp3."""
    _touch(tmp_path, "001 - aaa")
    _touch(tmp_path, "003 - ccc")

    entries = [
        {"id": "aaa", "title": "First", "playlist_index": 1, "duration": 10},
        None,  # DRM/ошибка — yt-dlp с ignoreerrors отдаёт None
        {"id": "ccc", "title": "Third", "playlist_index": 3, "duration": 30},
    ]

    tracks = _collect_tracks(entries, tmp_path)

    assert [t.title for t in tracks] == ["First", "Third"]
    assert [t.audio_path.name for t in tracks] == ["001 - aaa.mp3", "003 - ccc.mp3"]


def test_falls_back_to_order_when_playlist_index_missing(tmp_path):
    """Без playlist_index опираемся на порядковый номер записи, а не на счётчик успешных."""
    _touch(tmp_path, "002 - bbb")

    entries = [None, {"id": "bbb", "title": "Second", "duration": 20}]

    tracks = _collect_tracks(entries, tmp_path)

    assert [t.audio_path.name for t in tracks] == ["002 - bbb.mp3"]
