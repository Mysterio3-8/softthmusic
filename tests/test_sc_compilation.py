"""Сборник из треков SoundCloud — запасной источник, когда YouTube не отдаёт.

ТЗ владельца 2026-08-16: «можешь с sc плейлисты брать или свои создавать софт будет,
если на ютубе проблемы». Повод — три простоя подряд на ОДНОМ источнике за две недели.
"""
from pathlib import Path

from app.sc_compilation import SC_URL_PREFIX, collect_tracks, is_sc_source
from app.sc_discovery import TrackRef
from app.soundcloud import Track


def _ref(url: str, artist: str = "Артист", title: str = "Трек") -> TrackRef:
    return TrackRef(url=url, title=title, artist=artist, plays=500_000, russian=True)


def _track(seconds: int, artist: str = "Артист", title: str = "Трек") -> Track:
    return Track(
        position=1,
        title=title,
        artist=artist,
        duration_s=seconds,
        audio_path=Path("/tmp/a.mp3"),
        cover_path=None,
    )


def test_marker_url_is_recognised():
    assert is_sc_source(f"{SC_URL_PREFIX}20260816")
    assert not is_sc_source("https://www.youtube.com/playlist?list=x")


def test_tracks_are_numbered_consecutively(monkeypatch, tmp_path):
    """download_track отдаёт каждый трек одиночным (позиция 1), а в треклисте
    нумерация сквозная — иначе тайминги в описании не сойдутся."""
    monkeypatch.setattr(
        "app.sc_compilation.collect_new_tracks",
        lambda *a, **k: [_ref(f"u{i}", title=f"Трек {i}") for i in range(5)],
    )
    monkeypatch.setattr("app.sc_compilation.download_track", lambda url, d: _track(200))

    tracks = collect_tracks(
        ["рэп"], tmp_path, wanted=5, min_tracks=3, max_track_seconds=900, min_plays=1000
    )

    assert [t.position for t in tracks] == [1, 2, 3, 4, 5]


def test_hour_long_mix_is_dropped_here_too(monkeypatch, tmp_path):
    """Часовой микс одинаково вреден независимо от того, откуда он приехал."""
    monkeypatch.setattr(
        "app.sc_compilation.collect_new_tracks",
        lambda *a, **k: [_ref(f"u{i}", title=f"Трек {i}") for i in range(6)],
    )
    calls = iter([3600, 200, 200, 200, 200, 200])
    monkeypatch.setattr(
        "app.sc_compilation.download_track",
        lambda url, d: _track(next(calls), title=f"Трек {url}"),
    )

    tracks = collect_tracks(
        ["рэп"], tmp_path, wanted=5, min_tracks=3, max_track_seconds=900, min_plays=1000
    )

    assert len(tracks) == 5
    assert all(t.duration_s == 200 for t in tracks)


def test_undownloadable_track_does_not_break_the_batch(monkeypatch, tmp_path):
    """Часть треков SoundCloud отдаёт под DRM — это норма, а не поломка."""
    monkeypatch.setattr(
        "app.sc_compilation.collect_new_tracks",
        lambda *a, **k: [_ref(f"u{i}", title=f"Трек {i}") for i in range(6)],
    )
    state = {"n": 0}

    def flaky(url, target):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("This video is DRM protected")
        return _track(200, title=f"Трек {url}")

    monkeypatch.setattr("app.sc_compilation.download_track", flaky)

    tracks = collect_tracks(
        ["рэп"], tmp_path, wanted=4, min_tracks=3, max_track_seconds=900, min_plays=1000
    )

    assert len(tracks) == 4


def test_duplicate_reuploads_are_not_taken_twice(monkeypatch, tmp_path):
    """Один хит лежит на SoundCloud десятками перезаливов с разными ссылками — рядом
    в треклисте они выглядят как поломка."""
    monkeypatch.setattr(
        "app.sc_compilation.collect_new_tracks",
        lambda *a, **k: [_ref(f"u{i}", artist="Баста", title="Сансара") for i in range(4)]
        + [_ref("u9", artist="Другой", title="Песня")],
    )
    monkeypatch.setattr(
        "app.sc_compilation.download_track",
        lambda url, d: _track(200, artist="Баста" if url != "u9" else "Другой",
                              title="Сансара" if url != "u9" else "Песня"),
    )

    tracks = collect_tracks(
        ["рэп"], tmp_path, wanted=5, min_tracks=2, max_track_seconds=900, min_plays=1000
    )

    assert len(tracks) == 2


def test_too_few_tracks_means_no_compilation(monkeypatch, tmp_path):
    """Огрызок из двух песен публиковать хуже, чем пропустить тик."""
    monkeypatch.setattr(
        "app.sc_compilation.collect_new_tracks", lambda *a, **k: [_ref("u1"), _ref("u2")]
    )
    monkeypatch.setattr("app.sc_compilation.download_track", lambda url, d: _track(200))

    assert collect_tracks(
        ["рэп"], tmp_path, wanted=10, min_tracks=5, max_track_seconds=900, min_plays=1000
    ) == []


def test_silent_search_yields_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr("app.sc_compilation.collect_new_tracks", lambda *a, **k: [])

    assert collect_tracks(
        ["рэп"], tmp_path, wanted=10, min_tracks=5, max_track_seconds=900, min_plays=1000
    ) == []


def test_soundcloud_search_link_becomes_a_query():
    """🔴 ТЗ владельца 2026-08-17: источник дан ссылкой на страницу поиска SoundCloud.
    yt-dlp такую ссылку не умеет и падает — переводим в родной поисковый запрос."""
    from app.sc_discovery import build_source_url

    assert build_source_url("https://soundcloud.com/search?q=bandana", 40) == "scsearch40:bandana"


def test_multiword_search_link_is_decoded():
    from app.sc_discovery import build_source_url

    url = "https://soundcloud.com/search?q=%D1%80%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9%20%D1%80%D1%8D%D0%BF"

    assert build_source_url(url, 10) == "scsearch10:русский рэп"


def test_playlist_link_is_left_alone():
    """Ссылка на плейлист или профиль — рабочая для yt-dlp, трогать её нельзя."""
    from app.sc_discovery import build_source_url

    link = "https://soundcloud.com/user/sets/mix"

    assert build_source_url(link, 40) == link
