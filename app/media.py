"""Граница ffmpeg: обложка + аудио -> mp4; склейка сегментов в компиляцию."""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.logger import get_logger

# Единый кадр для всех сегментов — concat demuxer требует совпадения потоков.
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720

# Статичная картинка: 1 к/с на входе, разреженные ключевые кадры на выходе.
# Даёт компиляцию из 20 треков весом порядка сотни мегабайт вместо гигабайтов.
_INPUT_FPS = "1"
_OUTPUT_FPS = "10"
_KEYFRAME_INTERVAL = "60"

FFMPEG_TIMEOUT_SECONDS = 3600


class MediaError(Exception):
    """ffmpeg не смог собрать видео."""


def render_track_video(audio_path: Path, cover_path: Path, output_path: Path) -> Path:
    """Один трек -> mp4 со статичной обложкой. Длительность равна длине аудио."""
    scale_filter = (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,"
        "format=yuv420p"
    )
    _run_ffmpeg(
        [
            "-loop", "1", "-framerate", _INPUT_FPS, "-i", str(cover_path),
            "-i", str(audio_path),
            "-vf", scale_filter,
            "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast",
            "-r", _OUTPUT_FPS, "-g", _KEYFRAME_INTERVAL,
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart",
            str(output_path),
        ],
        description=f"сегмент {output_path.name}",
    )
    return output_path


def concat_videos(segments: list[Path], output_path: Path) -> Path:
    """Склеивает готовые сегменты в один mp4 без перекодирования."""
    if not segments:
        raise MediaError("Нечего склеивать: список сегментов пуст")

    list_file = output_path.with_suffix(".txt")
    # concat demuxer разбирает строку по кавычкам — одинарные внутри пути надо экранировать.
    lines = [f"file '{_escape(segment)}'" for segment in segments]
    list_file.write_text("\n".join(lines), encoding="utf-8")

    try:
        _run_ffmpeg(
            [
                "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c", "copy", "-movflags", "+faststart",
                str(output_path),
            ],
            description=f"склейка {len(segments)} сегментов",
        )
    finally:
        list_file.unlink(missing_ok=True)
    return output_path


def _escape(path: Path) -> str:
    return str(path.resolve()).replace("'", r"'\''")


def _run_ffmpeg(args: list[str], description: str) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    get_logger().info("ffmpeg: %s", description)
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS
        )
    except FileNotFoundError as exc:
        raise MediaError("ffmpeg не найден в PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"ffmpeg не уложился в таймаут на шаге «{description}»") from exc

    if proc.returncode != 0:
        raise MediaError(f"ffmpeg упал на шаге «{description}»: {proc.stderr.strip()}")
