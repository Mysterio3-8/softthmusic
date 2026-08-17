"""Обложка сборника — картинка 1280×720 для YouTube.

Зачем. Статистика канала 2026-08-17: три просмотра за двое суток. Заголовок решает,
кликнут ли по УЖЕ показанному ролику, но первым в глаза бросается превью, а превью у нас
не было вовсе — YouTube брал случайный кадр, то есть обложку первого трека без единой
подписи. По такому кадру не понять ни что это сборник, ни сколько в нём песен.

Что рисуем (и почему именно это):

* крупное «ТОП-15» — число видно с миниатюры даже на телефоне, и это единственный
  элемент, который сразу отличает сборник от одиночного трека;
* месяц и год — свежесть; по ней кликают;
* три имени исполнителей — за них цепляется глаз тех, кто их слушает;
* фоном — обложка первого трека, размытая и затемнённая, чтобы текст читался поверх
  любой картинки, а не только поверх тёмной.

Обложка НЕ вшивается в видео. Первые секунды ролика — это удержание, и статичная
заставка там работает против него; на YouTube превью ставится отдельным файлом, туда мы
её и отдаём. В VK превью берётся из кадра видео, и там всё остаётся как было.
"""
from __future__ import annotations

from pathlib import Path

from app.logger import get_logger
from app.overlay import find_font

WIDTH, HEIGHT = 1280, 720
"""Размер превью YouTube. 16:9 — миниатюра не обрежется ни в поиске, ни в рекомендациях."""

BACKGROUND = (14, 12, 20)
ACCENT = (255, 214, 0)
"""Жёлтый — тот же, что на подписях клипов Кино: он держит контраст на любом фоне."""


MONTHS_NOMINATIVE = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)
"""На обложке месяц стоит ОТДЕЛЬНОЙ подписью, а не внутри фразы, поэтому нужен
именительный: «АВГУСТ 2026». Родительный «АВГУСТА 2026» без опоры читается как обрывок
(поймано глазом на первом же отрендеренном превью)."""


def build_thumbnail(
    out_path: Path,
    *,
    count: int,
    month_index: int,
    year: int,
    artists: list[str],
    cover_path: Path | None = None,
) -> Path | None:
    """Рисует обложку. None — Pillow или шрифт недоступны, это не повод ронять сборник.

    Тот же принцип, что у подписей на видео: нет шрифта → молча работаем без картинки.
    Сборник без превью хуже, чем с превью, но несравнимо лучше, чем отсутствие сборника."""
    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
    except ImportError:
        get_logger().info("Pillow недоступен — обложка не рисуется")
        return None

    try:
        font_path = find_font()
    except Exception:  # noqa: BLE001 — граница окружения, шрифта может не быть
        get_logger().info("Шрифт не найден — обложка не рисуется")
        return None

    canvas = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    if cover_path and Path(cover_path).exists():
        try:
            cover = Image.open(cover_path).convert("RGB")
            # Заполняем кадр целиком по короткой стороне, потом режем по центру: иначе
            # квадратная обложка легла бы с чёрными полями по бокам.
            scale = max(WIDTH / cover.width, HEIGHT / cover.height)
            resized = cover.resize(
                (max(1, int(cover.width * scale)), max(1, int(cover.height * scale)))
            )
            left = (resized.width - WIDTH) // 2
            top = (resized.height - HEIGHT) // 2
            frame = resized.crop((left, top, left + WIDTH, top + HEIGHT))
            frame = frame.filter(ImageFilter.GaussianBlur(18))
            # Затемняем ДО текста: без этого жёлтый по светлой обложке нечитаем.
            canvas = ImageEnhance.Brightness(frame).enhance(0.45)
        except Exception as error:  # noqa: BLE001 — битая обложка это норма
            get_logger().info("Обложка %s не прочиталась (%s) — фон однотонный", cover_path, error)

    draw = ImageDraw.Draw(canvas)
    big = ImageFont.truetype(str(font_path), 210)
    medium = ImageFont.truetype(str(font_path), 68)
    small = ImageFont.truetype(str(font_path), 54)

    _centered(draw, f"ТОП-{count}", 120, big, ACCENT)
    _centered(draw, f"{MONTHS_NOMINATIVE[month_index - 1]} {year}".upper(), 355, medium, (255, 255, 255))
    names = ", ".join(artists[:3])
    if names:
        _centered(draw, names, 470, small, (235, 235, 235))
    # «ТРЕКОВ ПОДРЯД» здесь висело обрывком: глаз читает нижнюю строку отдельно от
    # «ТОП-15», а не как её продолжение.
    _centered(draw, "ПОДРЯД · БЕЗ РЕКЛАМЫ", 600, small, ACCENT)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "JPEG", quality=90)
    return out_path


def _centered(draw, text: str, y: int, font, color) -> None:
    """Строка по центру с чёрной обводкой.

    Обводка обязательна: фон — чужая обложка, и её светлые участки съедают даже белый
    текст. `stroke_width` дешевле, чем рисовать текст восемь раз со сдвигом."""
    text = _fit(draw, text, font)
    width = draw.textlength(text, font=font)
    draw.text(
        ((WIDTH - width) / 2, y),
        text,
        font=font,
        fill=color,
        stroke_width=max(2, font.size // 18),
        stroke_fill=(0, 0, 0),
    )


def _fit(draw, text: str, font, margin: int = 60) -> str:
    """Обрезает строку до ширины кадра. Имена артистов бывают длинными, а перенос в
    миниатюре читается хуже, чем многоточие."""
    limit = WIDTH - margin * 2
    if draw.textlength(text, font=font) <= limit:
        return text
    while text and draw.textlength(text + "…", font=font) > limit:
        text = text[:-1]
    return text.rstrip(", ") + "…"
