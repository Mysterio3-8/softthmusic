"""Автопоиск популярных треков: отбор, ранжирование, дедуп, наполнение очереди."""
from __future__ import annotations

import pytest

from app.album_db import AlbumQueue
from app.config import DiscoveryConfig
from app.sc_discovery import TrackRef, build_source_url, collect_new_tracks, rank_tracks


def _ref(title="T", artist="A", plays=1_000_000, url=None, russian=False) -> TrackRef:
    return TrackRef(
        url=url or f"https://api.soundcloud.com/tracks/{title}",
        title=title,
        artist=artist,
        plays=plays,
        russian=russian,
    )


def test_search_query_becomes_scsearch():
    assert build_source_url("русский рэп", 40) == "scsearch40:русский рэп"


def test_link_source_is_taken_as_is():
    url = "https://soundcloud.com/user/sets/hits"
    assert build_source_url(url, 40) == url


def test_russian_tracks_rank_above_more_popular_foreign_ones():
    """«Желательно русские» — это предпочтение: русский идёт первым даже с меньшим
    числом прослушиваний, но зарубежный хит не выбрасывается."""
    foreign = _ref(title="Foreign", plays=9_000_000)
    russian = _ref(title="Русский", plays=1_000_000, russian=True)

    assert rank_tracks([foreign, russian]) == [russian, foreign]


def test_within_one_language_more_plays_wins():
    quiet = _ref(title="Тихий", plays=200_000, russian=True)
    loud = _ref(title="Громкий", plays=5_000_000, russian=True)

    assert rank_tracks([quiet, loud]) == [loud, quiet]


def test_collect_skips_known_urls_and_reuploads(monkeypatch):
    """Один хит лежит на SoundCloud десятками перезаливов с разными ссылками —
    без дедупа по имени очередь набилась бы копиями одного трека."""
    from app import sc_discovery

    found = [
        _ref(title="Тебя Нежно Грубо", artist="TARAS", url="u1", russian=True),
        _ref(title="Тебя нежно грубо!", artist="taras", url="u2", russian=True),
        _ref(title="Новый", artist="Кто-то", url="u3", russian=True),
    ]
    monkeypatch.setattr(sc_discovery, "discover_tracks", lambda *a, **k: found)

    result = collect_new_tracks(["запрос"], known_urls=set(), known_names=set(), wanted=5)

    assert [ref.url for ref in result] == ["u1", "u3"]


def test_collect_survives_broken_source(monkeypatch):
    """У поисковой выдачи состав плавает: один упавший запрос не повод оставить
    очередь пустой."""
    from app import sc_discovery

    def fake(source, *args, **kwargs):
        if source == "плохой":
            raise sc_discovery.DiscoveryError("нет сети")
        return [_ref(title="Хит", url="ok", russian=True)]

    monkeypatch.setattr(sc_discovery, "discover_tracks", fake)

    result = collect_new_tracks(["плохой", "хороший"], set(), set(), wanted=5)

    assert [ref.url for ref in result] == ["ok"]


def test_entry_artist_comes_from_the_title_not_from_the_public():
    """У популярного трека uploader — это паблик-перезаливщик, а не исполнитель.
    Замер 2026-08-11: 12 млн прослушиваний, uploader «Русский Рэп», автор — TARAS."""
    from app.sc_discovery import _to_ref

    ref = _to_ref(
        {
            "url": "u1",
            "title": "TARAS - Тебя Нежно Грубо",
            "uploader": "Русский Рэп",
            "view_count": 12_752_733,
        }
    )

    assert ref.artist == "TARAS"
    assert ref.title == "Тебя Нежно Грубо"
    assert ref.russian is True


def test_same_track_from_two_publics_is_one_entry(monkeypatch):
    """Без разбора названия «Русский Рэп» и «RuRap» дали бы два разных ключа дедупа."""
    from app import sc_discovery

    entries = [
        {"url": "u1", "title": "TARAS - Тебя Нежно Грубо", "uploader": "Русский Рэп",
         "view_count": 12_000_000},
        {"url": "u2", "title": "TARAS - Тебя нежно грубо", "uploader": "RuRap",
         "view_count": 900_000},
    ]
    monkeypatch.setattr(
        sc_discovery,
        "discover_tracks",
        lambda *a, **k: sc_discovery.rank_tracks(
            [ref for ref in map(sc_discovery._to_ref, entries) if ref]
        ),
    )

    result = collect_new_tracks(["q"], set(), set(), wanted=5)

    assert [ref.url for ref in result] == ["u1"]


def test_collect_stops_at_wanted(monkeypatch):
    from app import sc_discovery

    monkeypatch.setattr(
        sc_discovery,
        "discover_tracks",
        lambda *a, **k: [_ref(title=f"T{i}", url=f"u{i}") for i in range(10)],
    )

    assert len(collect_new_tracks(["q"], set(), set(), wanted=3)) == 3


class _Config:
    def __init__(self, discovery: DiscoveryConfig) -> None:
        self.soundcloud = type("_SC", (), {"discovery": discovery})()


@pytest.fixture()
def queue(tmp_path) -> AlbumQueue:
    q = AlbumQueue(tmp_path / "queue.db")
    yield q
    q.close()


def test_refill_enqueues_found_tracks_as_singles(queue, monkeypatch):
    """Находка автопоиска — один трек: сборник из него опубликовал бы то же аудио дважды."""
    from app import sc_autofill

    monkeypatch.setattr(
        sc_autofill, "collect_new_tracks", lambda *a, **k: [_ref(title="Хит", url="u1")]
    )
    config = _Config(DiscoveryConfig(enabled=True, sources=["q"], min_queue=3, target_queue=5))

    assert sc_autofill.refill(config, queue) == 1

    album = queue.next_pending_album()
    assert album.url == "u1"
    assert album.skip_compilation is True
    assert album.tracks_total == 1


def test_refill_does_nothing_when_queue_is_full(queue, monkeypatch):
    from app import sc_autofill

    for i in range(3):
        queue.enqueue(f"u{i}", "T", "A", 1, None, skip_compilation=True)
    monkeypatch.setattr(sc_autofill, "collect_new_tracks", lambda *a, **k: pytest.fail("не должно"))
    config = _Config(DiscoveryConfig(enabled=True, sources=["q"], min_queue=3, target_queue=5))

    assert sc_autofill.refill(config, queue) == 0


def test_refill_disabled_keeps_manual_behaviour(queue, monkeypatch):
    """Выключенный автопоиск обязан вести себя ровно как до его появления."""
    from app import sc_autofill

    monkeypatch.setattr(sc_autofill, "collect_new_tracks", lambda *a, **k: pytest.fail("не должно"))
    config = _Config(DiscoveryConfig(enabled=False, sources=["q"]))

    assert sc_autofill.refill(config, queue) == 0


def test_refill_passes_queue_history_to_dedup(queue, monkeypatch):
    """Уже опубликованный трек не должен вернуться в очередь вторым заходом."""
    from app import sc_autofill

    queue.enqueue("u1", "Хит", "Артист", 1, None, skip_compilation=True)
    seen: dict = {}

    def fake(sources, known_urls, known_names, **kwargs):
        seen["urls"] = known_urls
        seen["names"] = known_names
        return []

    monkeypatch.setattr(sc_autofill, "collect_new_tracks", fake)
    config = _Config(DiscoveryConfig(enabled=True, sources=["q"], min_queue=3, target_queue=5))

    sc_autofill.refill(config, queue)

    assert seen["urls"] == {"u1"}
    assert seen["names"] == {"Артист Хит"}
