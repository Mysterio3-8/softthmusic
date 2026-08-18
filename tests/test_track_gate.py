"""Гейт имён для треклиста сборника (ТЗ владельца 2026-08-18).

Все примеры — ЖИВЫЕ, из сборника [285](https://vk.com/wall-240295467_285), который
владелец прислал со словами «это не надо в вк». Держим их тестами, потому что глазами
такой мусор виден только после публикации.
"""
from pathlib import Path

import pytest

from app import sc_compilation
from app.sc_discovery import TrackRef
from app.soundcloud import Track
from app.track_naming import (
    has_artist_separator,
    is_music_label,
    split_artist_title,
    usable_track,
)


# --- разбор имени -------------------------------------------------------------


def test_uploader_is_not_an_artist_when_the_title_has_no_separator():
    """«New Russian Rap Music» — паблик-перезаливщик, а не исполнитель."""
    assert has_artist_separator("Батальон Морской Пехоты - русский реп 2024") is True
    assert has_artist_separator("четкий фонк 💀") is False
    assert has_artist_separator("ВАЙБ 2025 ( ФОНК, Бразильский фонк )") is False


@pytest.mark.parametrize(
    "text",
    [
        "Русский Фонк",
        "русский реп 2024",
        "ВАЙБ 2025",
        "русская танцевальная музыка",
        "TOP лучшие треки 2026",
    ],
)
def test_genre_labels_are_recognised(text):
    assert is_music_label(text)


@pytest.mark.parametrize(
    "text",
    [
        "На Берегу Днепра",
        "Штиль",
        "Фонк для мамы",
        "Big Baby Tape",
        "MALO 2.0",
        "Русский вальс",   # ровно половина слов служебные — это ещё название
        "Танцы минус",
    ],
)
def test_real_names_are_not_labels(text):
    """Жанровое слово внутри честного названия — не повод его выбрасывать."""
    assert not is_music_label(text)


def test_keyword_soup_with_a_stray_word_is_still_a_label():
    """🔴 Живая дыра первой версии: правило «ВСЕ слова служебные» пропускало эту строку
    целиком из-за постороннего «war», и трек уезжал в сборник."""
    assert is_music_label("русский реп 2024 - best russian war rap 2024")


# --- пригодность пары ---------------------------------------------------------


@pytest.mark.parametrize(
    "artist,title",
    [
        ("****", "icarus. - Русский Фонк"),      # имя без единой буквы
        ("⊹", "ВАЙБ 2025"),                      # символ вместо имени
        ("слава кпсс замай", "Русский Фонк"),    # название = жанровый ярлык
        ("Glock Thrill Phonk", "Glock Thrill Phonk"),  # артист повторён названием
        ("Ария", ""),                            # пустое название
    ],
)
def test_junk_pairs_are_rejected(artist, title):
    assert not usable_track(artist, title)


def test_normal_pair_passes():
    assert usable_track("Ария", "Штиль")
    assert usable_track("TARAS", "Тебя Нежно Грубо")


# --- отбор в сборник ----------------------------------------------------------


def _ref(url, artist, title, *, from_title=True):
    return TrackRef(
        url=url, title=title, artist=artist, plays=1_000_000, russian=True,
        artist_from_title=from_title,
    )


def _fake_download(path: Path):
    def download(url, target_dir):
        # Сырые метаданные SoundCloud: артист = ЗАГРУЗЧИК, название = полный заголовок.
        return Track(
            position=1,
            title="Батальон Морской Пехоты - русский реп 2024 - best russian war rap",
            artist="New Russian Rap Music",
            duration_s=200,
            audio_path=path,
            cover_path=None,
        )
    return download


def test_compilation_uses_parsed_names_not_raw_metadata(tmp_path, monkeypatch):
    """🔴 Корень жалобы: в сборник уезжали сырые имена, хотя разобранные лежали рядом."""
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    monkeypatch.setattr(sc_compilation, "download_track", _fake_download(audio))
    monkeypatch.setattr(
        sc_compilation, "collect_new_tracks",
        lambda *a, **kw: [_ref("u1", "Ария", "Штиль"), _ref("u2", "TARAS", "Тебя Нежно Грубо")],
    )

    tracks = sc_compilation.collect_tracks(
        ["фонк"], tmp_path, wanted=2, min_tracks=1, max_track_seconds=900, min_plays=0
    )

    assert [(t.artist, t.title) for t in tracks] == [
        ("Ария", "Штиль"), ("TARAS", "Тебя Нежно Грубо")
    ]
    assert all("New Russian Rap Music" not in t.artist for t in tracks)


def test_tracks_without_a_clear_artist_never_reach_the_tracklist(tmp_path, monkeypatch):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    monkeypatch.setattr(sc_compilation, "download_track", _fake_download(audio))
    monkeypatch.setattr(
        sc_compilation, "collect_new_tracks",
        lambda *a, **kw: [
            _ref("u1", "New Russian Rap Music", "Русский дух. фонк", from_title=False),
            _ref("u2", "⊹", "ВАЙБ 2025"),
            _ref("u3", "слава кпсс замай", "Русский Фонк"),
            _ref("u4", "Ария", "Штиль"),
        ],
    )

    tracks = sc_compilation.collect_tracks(
        ["фонк"], tmp_path, wanted=4, min_tracks=1, max_track_seconds=900, min_plays=0
    )

    assert [(t.artist, t.title) for t in tracks] == [("Ария", "Штиль")]


def test_gate_can_leave_too_few_tracks_and_that_is_an_honest_refusal(tmp_path, monkeypatch):
    """Отсев может не оставить минимума — тогда сборник НЕ собираем, а не публикуем мусор."""
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    monkeypatch.setattr(sc_compilation, "download_track", _fake_download(audio))
    monkeypatch.setattr(
        sc_compilation, "collect_new_tracks",
        lambda *a, **kw: [_ref("u1", "****", "Русский Фонк"), _ref("u2", "⊹", "ФОНК 2025")],
    )

    assert sc_compilation.collect_tracks(
        ["фонк"], tmp_path, wanted=4, min_tracks=3, max_track_seconds=900, min_plays=0
    ) == []


def test_parsed_pair_survives_a_promo_tail():
    """«Артист - Песня (ПРЕМЬЕРА КЛИПА 2024)» — пара честная, хвост чистится."""
    artist, title = split_artist_title("Егор Крид - Худи (ПРЕМЬЕРА КЛИПА 2024)", "паблик")

    assert (artist, title) == ("Егор Крид", "Худи")
    assert usable_track(artist, title)
