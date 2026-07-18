from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from app.logger import get_logger


class YouTubeError(Exception):
    """Ошибка получения списка или скачивания ролика."""


@dataclass
class VideoMeta:
    youtube_id: str
    title: str
    description: str
    file_path: Path


def list_channel_video_ids(channel_url: str) -> list[str]:
    """Возвращает id всех роликов канала (без скачивания), в порядке выдачи YouTube."""
    options = {
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(channel_url, download=False)
    except Exception as exc:  # noqa: BLE001 — граница внешней библиотеки
        raise YouTubeError(f"Не удалось получить список видео {channel_url}: {exc}") from exc

    if not info:
        return []
    entries = info.get("entries") or []
    return [e["id"] for e in entries if e and e.get("id")]


def download_video(youtube_id: str, downloads_dir: Path, max_height: int) -> VideoMeta:
    """Скачивает один ролик в среднем качестве, возвращает метаданные и путь к файлу."""
    downloads_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(downloads_dir / "%(id)s.%(ext)s")

    options = {
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/best",
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    url = f"https://www.youtube.com/watch?v={youtube_id}"
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
    except Exception as exc:  # noqa: BLE001 — граница внешней библиотеки
        raise YouTubeError(f"Не удалось скачать {youtube_id}: {exc}") from exc

    if not file_path.exists():
        raise YouTubeError(f"Файл не найден после скачивания: {file_path}")

    get_logger().info("Скачан ролик %s -> %s", youtube_id, file_path.name)
    return VideoMeta(
        youtube_id=youtube_id,
        title=(info.get("title") or "").strip(),
        description=(info.get("description") or "").strip(),
        file_path=file_path,
    )
