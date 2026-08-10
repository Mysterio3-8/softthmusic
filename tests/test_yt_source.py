"""Граница YouTube: сборка URL источника, разбор названий, сопоставление файлов."""
from app.yt_source import build_source_url, clean_title, collect_tracks, split_artist_title


def test_link_source_is_used_as_is():
    url = "https://www.youtube.com/playlist?list=PL123"
    assert build_source_url(url) == url


def test_text_source_becomes_playlist_search():
    url = build_source_url("русский рэп плейлист")
    assert url.startswith("https://www.youtube.com/results?search_query=")
    # sp=EgIQAw%3D%3D — фильтр «только плейлисты». Без него поиск отдаёт одиночные
    # ролики, а владельцу нужны готовые пользовательские плейлисты.
    assert "sp=EgIQAw%3D%3D" in url


def test_split_artist_title_uses_dash_in_video_title():
    assert split_artist_title("Big Baby Tape - Gimme the Loot", "Чужой Паблик") == (
        "Big Baby Tape",
        "Gimme the Loot",
    )


def test_split_artist_title_supports_em_dash():
    assert split_artist_title("Ария — Штиль", "канал") == ("Ария", "Штиль")


def test_split_artist_title_falls_back_to_uploader_without_separator():
    assert split_artist_title("Штиль", "Ария - Topic") == ("Ария", "Штиль")


def test_clean_title_drops_service_brackets():
    assert clean_title("Штиль (Official Video)") == "Штиль"
    assert clean_title("Штиль (акустика)") == "Штиль (акустика)"


def _entry(index: int, video_id: str, title: str, duration: int = 200) -> dict:
    return {
        "playlist_index": index,
        "id": video_id,
        "title": title,
        "duration": duration,
        "uploader": "Чужой паблик",
    }


def test_collect_tracks_matches_downloaded_files(tmp_path):
    (tmp_path / "001 - aaa.mp3").write_bytes(b"x")
    (tmp_path / "001 - aaa.jpg").write_bytes(b"x")

    tracks = collect_tracks([_entry(1, "aaa", "Ария - Штиль")], tmp_path)

    assert len(tracks) == 1
    assert tracks[0].artist == "Ария"
    assert tracks[0].title == "Штиль"
    assert tracks[0].cover_path is not None


def test_collect_tracks_skips_missing_files_without_shifting_positions(tmp_path):
    """Упавший трек не должен сдвигать нумерацию: именно этот сдвиг ронял альбомы
    SoundCloud целиком («ни один трек не скачался» при реально скачанных mp3)."""
    (tmp_path / "001 - aaa.mp3").write_bytes(b"x")
    (tmp_path / "003 - ccc.mp3").write_bytes(b"x")

    tracks = collect_tracks(
        [_entry(1, "aaa", "A - 1"), _entry(2, "bbb", "B - 2"), _entry(3, "ccc", "C - 3")],
        tmp_path,
    )

    assert [track.position for track in tracks] == [1, 2]
    assert [track.title for track in tracks] == ["1", "3"]


def test_collect_tracks_ignores_none_entries(tmp_path):
    (tmp_path / "002 - bbb.mp3").write_bytes(b"x")
    tracks = collect_tracks([None, _entry(2, "bbb", "B - 2")], tmp_path)
    assert len(tracks) == 1
