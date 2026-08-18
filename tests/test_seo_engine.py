"""SEO-движок сборников (ТЗ владельца 2026-08-18).

Три правила движка — не врать, не спамить, не повторяться — держатся тестами: все три
уже нарушались живьём, и заметить нарушение можно было только по стене.
"""
import random

import pytest

from app.seo_engine import (
    TITLE_ARTIST_LIMIT,
    TITLE_LENGTH_LIMIT,
    SeoParams,
    build_keywords,
    build_pack,
    build_title,
    find_profile,
    looks_spammy,
)


def _rng():
    return random.Random(1234)


# --- база жанров --------------------------------------------------------------


@pytest.mark.parametrize(
    "text,label",
    [
        ("Фонк", "Фонк"),
        ("русский фонк", "Фонк"),
        ("phonk", "Фонк"),
        ("Рэп", "Русский рэп"),
        ("русский реп новинки", "Русский рэп"),
        ("хип хоп", "Хип-хоп"),
        ("шансон", "Шансон"),
    ],
)
def test_genre_is_recognised_from_button_name_and_search_query(text, label):
    """Жанр приходит и кнопкой («Фонк»), и поисковым запросом («русский фонк»)."""
    assert find_profile(text).label == label


def test_unknown_genre_falls_back_to_a_working_profile():
    """Владелец добавляет жанры из бота — незнакомое имя обязано дать нормальный
    заголовок, а не пустое место."""
    profile = find_profile("вапорвейв")

    assert profile.label == "Музыка"
    assert profile.keywords


# --- не врать -----------------------------------------------------------------


def test_artists_never_appear_when_the_tracklist_has_none():
    """Заголовок «Macan, Miyagi и другие» на сборнике без них — обман читателя."""
    titles = {
        build_title(SeoParams(genre="Фонк", count=12, year=2026), rng=random.Random(seed))
        for seed in range(200)
    }

    assert titles
    assert all("Macan" not in title for title in titles)
    assert all("и другие" not in title for title in titles)


def test_only_real_artists_get_into_the_title():
    params = SeoParams(
        genre="Рэп", artists=("Macan", "Miyagi", "Скриптонит", "Баста"), count=15, year=2026
    )
    with_names = [
        title
        for title in (build_title(params, rng=random.Random(seed)) for seed in range(200))
        if "Macan" in title
    ]

    assert with_names, "конструкции с артистами должны участвовать"
    for title in with_names:
        assert "Баста" not in title, f"в заголовок влезает максимум {TITLE_ARTIST_LIMIT}"
        assert "и другие" in title, "остальные артисты обязаны быть свёрнуты честно"


def test_track_count_in_the_title_is_the_real_one():
    titles = [
        build_title(SeoParams(genre="Фонк", count=12, year=2026), rng=random.Random(seed))
        for seed in range(200)
    ]
    numbered = [title for title in titles if "треков" in title and "12" in title]

    assert numbered, "конструкции с количеством должны участвовать"
    assert not any("100" in title for title in titles)


def test_no_count_in_the_title_when_it_is_unknown():
    titles = {
        build_title(SeoParams(genre="Фонк", year=2026), rng=random.Random(seed))
        for seed in range(100)
    }

    assert all("лучших треков" not in title for title in titles)


# --- не спамить ---------------------------------------------------------------


def test_keyword_soup_is_recognised_as_spam():
    assert looks_spammy("РЭП 2026 РУССКИЙ РЭП ЛУЧШИЙ РЭП НОВИНКИ РЭП ХИТЫ РЭП RAP HIP HOP")
    assert not looks_spammy("Русский рэп 2026 — Лучшие треки | Новинки и хиты")


def test_generated_titles_are_never_spammy():
    params = SeoParams(genre="Рэп", artists=("Macan", "Miyagi"), count=15, year=2026)

    for seed in range(300):
        title = build_title(params, rng=random.Random(seed))
        assert not looks_spammy(title), title
        assert len(title) <= TITLE_LENGTH_LIMIT


# --- не повторяться -----------------------------------------------------------


def test_recent_titles_are_skipped():
    params = SeoParams(genre="Фонк", count=12, year=2026)
    first = build_title(params, rng=_rng())

    for seed in range(50):
        assert build_title(params, recent=(first,), rng=random.Random(seed)) != first


def test_engine_gives_many_different_titles_for_one_genre():
    """Ради этого движок и писался: сотня сборников одного жанра не должна выходить
    под одним и тем же названием."""
    params = SeoParams(genre="Фонк", artists=("Kizaru", "Big Baby Tape"), count=12, year=2026)
    titles = {build_title(params, rng=random.Random(seed)) for seed in range(200)}

    assert len(titles) >= 15


def test_all_titles_used_still_returns_something():
    """Сборник с повторным названием лучше, чем сборник без названия."""
    params = SeoParams(genre="Фонк", count=12, year=2026)
    everything = tuple(
        build_title(params, rng=random.Random(seed)) for seed in range(300)
    )

    assert build_title(params, recent=everything, rng=_rng())


# --- год ----------------------------------------------------------------------


def test_year_comes_from_parameters_not_from_a_constant():
    assert "2027" in build_title(SeoParams(genre="Фонк", count=12, year=2027), rng=_rng())


# --- пакет целиком ------------------------------------------------------------


def test_pack_has_title_description_and_keywords():
    pack = build_pack(
        SeoParams(genre="Рэп", artists=("Macan", "Miyagi"), count=15, year=2026), rng=_rng()
    )

    assert pack.title
    assert "рэп" in pack.description.lower()
    assert "Macan" in pack.description
    assert pack.keywords


def test_description_mentions_no_invented_artists():
    pack = build_pack(SeoParams(genre="Фонк", count=12, year=2026), rng=_rng())

    assert "Macan" not in pack.description
    assert pack.description.strip()


def test_keywords_carry_the_long_tail_not_just_the_genre():
    keys = build_keywords(SeoParams(genre="Фонк", artists=("Kizaru",), count=12, year=2026))

    assert "фонк" in keys
    assert any(key.startswith("фонк") and "2026" in key for key in keys)
    assert any("для машины" in key for key in keys), "ситуация — самый дешёвый длинный хвост"
    assert "kizaru" in keys
    assert len(keys) == len(set(keys)), "повторов в ключах быть не должно"


# --- грамматика ---------------------------------------------------------------


def test_dependent_constructions_use_the_genitive_case():
    """«Лучшие треки русский рэп» читается как машинный перевод и убивает клик."""
    params = SeoParams(genre="Рэп", count=15, year=2026)
    titles = {build_title(params, rng=random.Random(seed)) for seed in range(300)}

    assert any("русского рэпа" in title for title in titles)
    assert not any("треков русский рэп" in title for title in titles)
    assert not any("треки русский рэп" in title for title in titles)


def test_unknown_genre_never_produces_a_broken_case():
    """У жанра из бота падежа в базе нет — конструкции с ним просто не участвуют."""
    from app.seo_engine import GenreProfile, _usable

    profile = GenreProfile(label="Вапорвейв")
    params = SeoParams(genre="Вапорвейв", count=10, year=2026)

    assert not _usable("Лучшие треки {genre_gen} {year}", params, profile)


def test_artist_list_never_collides_with_a_trailing_phrase():
    """«Kizaru, Macan и другие и не только» — так выглядела первая версия."""
    params = SeoParams(
        genre="Фонк", artists=("Kizaru", "Macan", "Miyagi", "Big Baby Tape"), count=12, year=2026
    )

    for seed in range(300):
        assert "и другие и" not in build_title(params, rng=random.Random(seed))


def test_description_does_not_repeat_the_title():
    """Заголовок стоит строкой выше — повтор читался как «… — Лучшие релизы — подборка…»."""
    params = SeoParams(genre="Рэп", artists=("Macan",), count=15, year=2026)

    for seed in range(50):
        pack = build_pack(params, rng=random.Random(seed))
        assert not pack.description.startswith(pack.title)


def test_artists_are_named_in_their_own_sentence():
    """Склонять имена исполнителей нечем, поэтому они идут отдельным предложением."""
    pack = build_pack(
        SeoParams(genre="Рэп", artists=("Macan", "Miyagi"), count=15, year=2026),
        rng=random.Random(3),
    )

    assert "В сборнике: Macan, Miyagi." in pack.description
