"""Граница ffmpeg: обложка + аудио -> mp4; склейка сегментов в компиляцию."""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.logger import get_logger
from app.overlay import CAPTION_SECONDS, TrackCaption, render_caption

# Единый кадр для всех сегментов — concat demuxer требует совпадения потоков.
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720

# Та же причина: concat склеивает БЕЗ перекодирования и берёт частоту первого
# сегмента. Разъезд 44.1/48 кГц давал ускорение звука на ~9% (жалоба 2026-07-30).
AUDIO_SAMPLE_RATE = 44100

BACKGROUND_BLUR_SIGMA = 18

# Статичная картинка: 1 к/с на входе, разреженные ключевые кадры на выходе.
# Даёт компиляцию из 20 треков весом порядка сотни мегабайт вместо гигабайтов.
_INPUT_FPS = "1"
_OUTPUT_FPS = "10"
_KEYFRAME_INTERVAL = "60"

FFMPEG_TIMEOUT_SECONDS = 3600


class MediaError(Exception):
    """ffmpeg не смог собрать видео."""


def build_background_filter(blur_sigma: int = BACKGROUND_BLUR_SIGMA) -> str:
    """Кадр из обложки: размытая копия во весь кадр + чёткий оригинал по центру.

    Обложки квадратные, кадр 16:9 — без фона по бокам оставались бы чёрные поля.
    Размытая растянутая копия (ТЗ 2026-08-10: «обложка трека, небольшой блюр»)
    смотрится как оформление, а не как дырка в кадре."""
    return (
        f"[0:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},gblur=sigma={blur_sigma}[bg];"
        f"[0:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )


def build_caption_filter(seconds: int = CAPTION_SECONDS) -> str:
    """Подпись поверх кадра только первые `seconds` секунд трека.

    `enable='lte(t,N)'` — ffmpeg считает время ВЫХОДНОГО кадра, поэтому счёт идёт от
    начала сегмента, а не от начала склеенного сборника: в компиляции подпись честно
    появляется на каждом треке заново. Запятая внутри `enable` экранирована — иначе
    filtergraph разберёт её как разделитель фильтров."""
    return f"[base][2:v]overlay=0:0:enable='lte(t\\,{seconds})'"


def build_filter_graph(with_caption: bool) -> str:
    """Полный filter_complex с ЯВНОЙ выходной меткой [v].

    Метка обязательна: как только в команде появляется хоть один `-map` (а он нужен
    для звука), ffmpeg выключает автоматический маппинг — безымянный выход графа
    просто не попал бы в файл, и трек вышел бы без картинки."""
    base = f"{build_background_filter()},format=yuv420p"
    if not with_caption:
        return f"{base}[v]"
    return f"{base}[base];{build_caption_filter()},format=yuv420p[v]"


def render_track_video(
    audio_path: Path,
    cover_path: Path,
    output_path: Path,
    caption: TrackCaption | None = None,
) -> Path:
    """Один трек -> mp4 с обложкой. Длительность равна длине аудио.

    caption задан → первые CAPTION_SECONDS секунд поверх кадра висит «название +
    исполнитель». Сбой отрисовки подписи НЕ роняет рендер: трек без подписи лучше,
    чем сборник, который не собрался."""
    caption_png = _prepare_caption(caption, output_path)
    inputs = ["-loop", "1", "-framerate", _INPUT_FPS, "-i", str(cover_path),
              "-i", str(audio_path)]
    if caption_png is not None:
        inputs += ["-loop", "1", "-framerate", _INPUT_FPS, "-i", str(caption_png)]

    try:
        _run_ffmpeg(
            [
                *inputs,
                "-filter_complex", build_filter_graph(caption_png is not None),
                "-map", "[v]", "-map", "1:a",
                "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast",
                "-r", _OUTPUT_FPS, "-g", _KEYFRAME_INTERVAL,
                # -ar фиксирован: concat-демуксер склеивает БЕЗ перекодирования и берёт
                # частоту первого сегмента. Разные частоты у сегментов давали ускорение
                # звука («все аудио ускоряются, немного но заметно», 2026-07-30).
                "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_SAMPLE_RATE),
                "-shortest", "-movflags", "+faststart",
                str(output_path),
            ],
            description=f"сегмент {output_path.name}",
        )
    finally:
        if caption_png is not None:
            caption_png.unlink(missing_ok=True)
    return output_path


def _prepare_caption(caption: TrackCaption | None, output_path: Path) -> Path | None:
    if caption is None or caption.is_empty():
        return None
    try:
        return render_caption(
            caption,
            output_path.with_name(f"{output_path.stem}_caption.png"),
            VIDEO_WIDTH,
            VIDEO_HEIGHT,
        )
    except Exception as exc:  # noqa: BLE001 — граница Pillow, подпись не критична
        get_logger().warning("Подпись к треку не отрисована (%s) — рендерю без неё", exc)
        return None


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
