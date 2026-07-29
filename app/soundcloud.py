"""Граница SoundCloud (yt-dlp): плейлист -> метаданные, треки, обложки."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from app.logger import get_logger

# Обложка yt-dlp кладёт рядом с аудио под тем же stem — расширение заранее не известно.
_COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


class SoundCloudError(Exception):
    """Ошибка чтения плейлиста или скачивания трека."""


@dataclass
class PlaylistMeta:
    """Быстрая сводка по ссылке — чтобы бот сразу ответил, что именно принял."""

    title: str
    artist: str
    track_count: int


@dataclass
class Track:
    position: int
    title: str
    artist: str
    duration_s: int
    audio_path: Path
    cover_path: Path | None


def fetch_playlist_meta(url: str) -> PlaylistMeta:
    """Название, автор и число треков без скачивания. Дёргается ботом на enqueue."""
    options = {
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 — граница внешней библиотеки
        raise SoundCloudError(f"Не удалось прочитать плейлист: {exc}") from exc

    if not info:
        raise SoundCloudError("Плейлист пуст или недоступен")

    entries = [e for e in (info.get("entries") or []) if e]
    if not entries:
        raise SoundCloudError("В плейлисте нет треков (возможно, он приватный)")

    return PlaylistMeta(
        title=(info.get("title") or "").strip(),
        artist=(info.get("uploader") or info.get("channel") or "").strip(),
        track_count=len(entries),
    )


def download_playlist(url: str, target_dir: Path) -> list[Track]:
    """Скачивает все треки плейлиста в mp3 с обложками. Порядок — как в плейлисте."""
    target_dir.mkdir(parents=True, exist_ok=True)
    options = {
        "format": "bestaudio/best",
        "outtmpl": str(target_dir / "%(playlist_index)03d - %(id)s.%(ext)s"),
        "writethumbnail": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
        ],
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001 — граница внешней библиотеки
        raise SoundCloudError(f"Не удалось скачать плейлист: {exc}") from exc

    if not info:
        raise SoundCloudError("yt-dlp не вернул данные плейлиста")

    tracks = _collect_tracks(info.get("entries") or [], target_dir)
    if not tracks:
        raise SoundCloudError("Ни один трек не скачался")

    get_logger().info("Скачано треков: %d из %s", len(tracks), url)
    return tracks


def _collect_tracks(entries: list, target_dir: Path) -> list[Track]:
    """Сопоставляет записи yt-dlp со скачанными файлами. Несошедшиеся — пропускает."""
    tracks: list[Track] = []
    position = 0
    for entry in entries:
        if not entry:
            continue  # ignoreerrors=True даёт None на упавших треках
        position += 1
        stem = f"{position:03d} - {entry.get('id')}"
        audio_path = target_dir / f"{stem}.mp3"
        if not audio_path.exists():
            get_logger().warning("Трек %s не скачался, пропуск", entry.get("title"))
            continue
        tracks.append(
            Track(
                position=position,
                title=(entry.get("title") or "").strip(),
                artist=(entry.get("uploader") or entry.get("artist") or "").strip(),
                duration_s=int(entry.get("duration") or 0),
                audio_path=audio_path,
                cover_path=_find_cover(target_dir, stem),
            )
        )
    return tracks


def _find_cover(target_dir: Path, stem: str) -> Path | None:
    for suffix in _COVER_SUFFIXES:
        candidate = target_dir / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    return None


def covers_are_identical(cover_paths: list[Path]) -> bool:
    """True — у всех треков одна и та же обложка (сравнение по содержимому).

    Плейлист-альбом обычно отдаёт одинаковый арт на каждый трек; сборник — разный.
    От этого зависит, будет компиляция статичной картинкой или слайд-шоу.
    """
    if len(cover_paths) < 2:
        return True
    digests = {_file_digest(path) for path in cover_paths}
    return len(digests) == 1


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
