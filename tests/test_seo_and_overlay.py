"""SEO-обвязка Infinity Music и подпись «исполнитель — трек» на видео."""
from PIL import Image

from app import seo
from app.media import build_background_filter, build_caption_filter, build_filter_graph
from app.overlay import CAPTION_SECONDS, TrackCaption, render_caption, wrap_lines


def test_slugify_keeps_cyrillic_and_drops_punctuation():
    assert seo.slugify("Big Baby Tape") == "big_baby_tape"
    assert seo.slugify("ARGUMENTS & FACTS") == "arguments_facts"


def test_hashtags_start_with_artist_and_bind_to_group():
    """Конкретика впереди: при маленьком лимите постоянные теги вытеснили бы имя."""
    tags = seo.build_hashtags(["Ария", "Штиль"], ["музыка"], "tgmusic", 5)
    assert tags[0] == "#ария@tgmusic"
    assert "#музыка@tgmusic" in tags


def test_hashtags_respect_limit():
    tags = seo.build_hashtags(["a", "b", "c"], ["x", "y"], "", 3)
    assert len(tags) == 3


def test_search_line_skips_empty_subjects():
    line = seo.build_search_line(["Ария", "", "  "], ["{q} слушать онлайн"])
    assert line == "Ария слушать онлайн"


def test_video_description_puts_tags_last_and_service_before_them():
    description = seo.build_video_description(
        header="🎧 Ария — Штиль",
        body="00:00 1. Штиль",
        subjects=["Ария", "Штиль"],
        phrases=["{q} слушать онлайн"],
        base_tags=["музыка"],
        group="tgmusic",
        tag_limit=6,
        service_block="♾️ Infinity Music — бот https://t.me/tgram_music_bot",
    )

    lines = description.rstrip().splitlines()
    assert description.startswith("🎧 Ария — Штиль")
    assert lines[-1].startswith("#")
    assert "https://t.me/tgram_music_bot" in description


def test_video_description_trims_on_word_boundary():
    description = seo.build_video_description(
        header="Заголовок",
        body="слово " * 2000,
        subjects=["Ария"],
        phrases=[],
        base_tags=[],
        group="",
        tag_limit=0,
        limit=80,
    )
    assert len(description) <= 81
    assert description.endswith("…")


def test_wrap_lines_breaks_by_words_and_respects_max_lines():
    lines = wrap_lines("одно два три четыре пять", 10, len, 2)
    assert lines == ["одно два", "три четыре"]


def test_wrap_lines_keeps_a_word_longer_than_the_line():
    """Обрубок слова читается хуже, чем вылезшая за край строка."""
    assert wrap_lines("сверхдлинноеслово", 5, len, 2) == ["сверхдлинноеслово"]


def test_caption_is_transparent_png_of_requested_size(tmp_path):
    path = render_caption(
        TrackCaption(artist="Ария", title="Штиль"), tmp_path / "cap.png", 640, 360
    )

    image = Image.open(path)
    assert image.size == (640, 360)
    assert image.mode == "RGBA"
    # Верх кадра остаётся прозрачным — подпись живёт внизу и обложку не закрывает.
    assert image.getpixel((320, 10))[3] == 0


def test_empty_caption_is_detected():
    assert TrackCaption(artist="  ", title="").is_empty() is True
    assert TrackCaption(artist="Ария", title="").is_empty() is False


def test_caption_filter_counts_time_from_segment_start():
    """`enable` считает время ВЫХОДНОГО кадра: в склейке подпись обязана появляться
    заново на каждом треке, а не один раз в начале сборника."""
    assert f"lte(t\\,{CAPTION_SECONDS})" in build_caption_filter()


def test_background_filter_blurs_and_centers_cover():
    graph = build_background_filter()
    assert "gblur=sigma=" in graph
    assert "overlay=(W-w)/2:(H-h)/2" in graph


def test_filter_graph_always_labels_its_output():
    """Без явной метки [v] видео пропало бы: любой -map в команде выключает
    автоматический маппинг безымянного выхода графа."""
    assert build_filter_graph(with_caption=False).endswith("[v]")
    assert build_filter_graph(with_caption=True).endswith("[v]")


def test_filter_graph_wires_caption_only_when_asked():
    assert "[2:v]" not in build_filter_graph(with_caption=False)
    assert "[2:v]" in build_filter_graph(with_caption=True)
