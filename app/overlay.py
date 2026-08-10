"""Подпись «исполнитель — трек» поверх кадра: рисуется Pillow, накладывается ffmpeg.

Почему PNG, а не ffmpeg-drawtext: drawtext требует экранировать кириллицу прямо в
строке фильтра, не переносит текст по словам и даёт плохую обводку. Тот же вывод
раньше сделали в Новостях (`core/video/clip_overlay.py`) — здесь тот же приём.

Подпись висит первые `CAPTION_SECONDS` секунд каждого трека (ТЗ владельца 2026-08-10:
«название трека и исполнителя накладывал на видос… на секунд 10»), дальше кадр
остаётся чистой обложкой.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CAPTION_SECONDS = 10

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_FONT = PROJECT_ROOT / "assets" / "fonts" / "DejaVuSans-Bold.ttf"
# Запасные пути на случай, если assets не доехали до сервера: на Debian-хостах
# DejaVu стоит из коробки. Без кириллического шрифта подпись рисовать нельзя —
# встроенный шрифт Pillow отдал бы вместо букв квадраты.
SYSTEM_FONTS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
)

TITLE_COLOR = (255, 255, 255, 255)
ARTIST_COLOR = (255, 214, 0, 255)
STROKE_COLOR = (0, 0, 0, 235)
SCRIM_COLOR = (0, 0, 0, 150)
"""Полупрозрачная подложка под текстом: обложки бывают светлые, и белый текст на
светлой обложке без неё не читается даже с обводкой."""

TITLE_SIZE_RATIO = 0.058
ARTIST_SIZE_RATIO = 0.042
MARGIN_RATIO = 0.05
STROKE_RATIO = 0.10
MAX_TITLE_LINES = 2


@dataclass(frozen=True)
class TrackCaption:
    artist: str
    title: str

    def is_empty(self) -> bool:
        return not (self.artist.strip() or self.title.strip())


class OverlayError(Exception):
    """Кириллического шрифта нет — подпись не рисуем (вызывающий переживёт это сам)."""


def find_font() -> Path:
    for candidate in (BUNDLED_FONT, *SYSTEM_FONTS):
        if candidate.exists():
            return candidate
    raise OverlayError(
        "Шрифт DejaVuSans-Bold.ttf не найден ни в assets/fonts, ни в системе"
    )


def wrap_lines(text: str, max_width_px: float, measure, max_lines: int) -> list[str]:
    """Перенос по словам. Слово шире строки остаётся целым — обрубок читается хуже."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and measure(candidate) > max_width_px:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:max_lines]


def render_caption(caption: TrackCaption, out_path: Path, width: int, height: int) -> Path:
    """Прозрачный PNG width×height: тёмная плашка внизу, название и исполнитель."""
    font_path = find_font()
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    title_font = ImageFont.truetype(str(font_path), int(width * TITLE_SIZE_RATIO))
    artist_font = ImageFont.truetype(str(font_path), int(width * ARTIST_SIZE_RATIO))
    margin = int(width * MARGIN_RATIO)

    title_lines = wrap_lines(
        caption.title.strip(),
        width - 2 * margin,
        lambda line: draw.textlength(line, font=title_font),
        MAX_TITLE_LINES,
    )
    artist_lines = wrap_lines(
        caption.artist.strip(),
        width - 2 * margin,
        lambda line: draw.textlength(line, font=artist_font),
        1,
    )

    line_height = int(title_font.size * 1.25)
    artist_height = int(artist_font.size * 1.35) if artist_lines else 0
    block_height = len(title_lines) * line_height + artist_height
    top = height - margin - block_height

    draw.rectangle(
        [(0, max(top - margin // 2, 0)), (width, height)], fill=SCRIM_COLOR
    )

    y = top
    for line in title_lines:
        _draw_line(draw, line, y, width, title_font, TITLE_COLOR)
        y += line_height
    for line in artist_lines:
        _draw_line(draw, line, y, width, artist_font, ARTIST_COLOR)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


def _draw_line(draw, line: str, y: int, width: int, font, color) -> None:
    draw.text(
        (width // 2, y),
        line,
        font=font,
        fill=color,
        stroke_width=max(int(font.size * STROKE_RATIO), 2),
        stroke_fill=STROKE_COLOR,
        anchor="ma",
    )
