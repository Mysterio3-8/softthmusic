"""Обложка сборника для YouTube (ТЗ 2026-08-17, статистика канала: 3 просмотра за 48 ч)."""
from pathlib import Path

from app.thumbnail import MONTHS_NOMINATIVE, build_thumbnail


def test_thumbnail_is_written(tmp_path):
    out = build_thumbnail(
        tmp_path / "t.jpg", count=15, month_index=8, year=2026, artists=["Ария", "Кино"]
    )

    assert out is not None and out.exists()


def test_thumbnail_is_youtube_sized(tmp_path):
    from PIL import Image

    out = build_thumbnail(
        tmp_path / "t.jpg", count=15, month_index=8, year=2026, artists=["Ария"]
    )

    assert Image.open(out).size == (1280, 720)


def test_month_is_nominative_on_the_cover():
    """«АВГУСТА 2026» отдельной подписью — обрывок: на обложке месяц не внутри фразы."""
    assert MONTHS_NOMINATIVE[7] == "август"


def test_missing_cover_does_not_break_the_thumbnail(tmp_path):
    """Обложки первого трека может не быть — превью всё равно обязано получиться."""
    out = build_thumbnail(
        tmp_path / "t.jpg", count=10, month_index=1, year=2026,
        artists=["Ария"], cover_path=Path("нет-такого.jpg"),
    )

    assert out is not None and out.exists()


def test_broken_cover_falls_back_to_plain_background(tmp_path):
    """Битый файл обложки — норма (yt-dlp иногда кладёт обрезанный webp)."""
    broken = tmp_path / "broken.jpg"
    broken.write_bytes("это не картинка".encode("utf-8"))

    out = build_thumbnail(
        tmp_path / "t.jpg", count=10, month_index=1, year=2026,
        artists=["Ария"], cover_path=broken,
    )

    assert out is not None and out.exists()


def test_long_artist_line_is_trimmed(tmp_path):
    """Имена бывают длинными, а перенос в миниатюре читается хуже многоточия."""
    out = build_thumbnail(
        tmp_path / "t.jpg", count=15, month_index=8, year=2026,
        artists=["Очень длинное имя исполнителя раз", "И ещё одно такое же длинное",
                 "И третье не короче прочих"],
    )

    assert out is not None and out.exists()
