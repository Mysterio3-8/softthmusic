"""Сопоставление записей yt-dlp со скачанными файлами."""
from pathlib import Path

from app.soundcloud import _collect_tracks


def _touch(directory: Path, stem: str) -> None:
    (directory / f"{stem}.mp3").write_bytes(b"x")


def test_collects_tracks_when_some_entries_failed(tmp_path):
    """Регрессия 2026-08-06: имя файла yt-dlp даёт по playlist_index (номер среди ВСЕХ
    записей), а сбор шёл по счётчику успешных. Любой упавший трек — а SoundCloud отдаёт
    часть треков под DRM — сдвигал нумерацию, и дальше не находился НИ ОДИН файл:
    альбом падал с «Ни один трек не скачался» при реально скачанных mp3."""
    _touch(tmp_path, "001 - aaa")
    _touch(tmp_path, "003 - ccc")

    entries = [
        {"id": "aaa", "title": "First", "playlist_index": 1, "duration": 10},
        None,  # DRM/ошибка — yt-dlp с ignoreerrors отдаёт None
        {"id": "ccc", "title": "Third", "playlist_index": 3, "duration": 30},
    ]

    tracks = _collect_tracks(entries, tmp_path)

    assert [t.title for t in tracks] == ["First", "Third"]
    assert [t.audio_path.name for t in tracks] == ["001 - aaa.mp3", "003 - ccc.mp3"]


def test_falls_back_to_order_when_playlist_index_missing(tmp_path):
    """Без playlist_index опираемся на порядковый номер записи, а не на счётчик успешных."""
    _touch(tmp_path, "002 - bbb")

    entries = [None, {"id": "bbb", "title": "Second", "duration": 20}]

    tracks = _collect_tracks(entries, tmp_path)

    assert [t.audio_path.name for t in tracks] == ["002 - bbb.mp3"]


def test_busy_token_returns_album_to_queue(tmp_path, monkeypatch):
    """Регрессия 2026-08-06: занятый пул токенов помечал альбом failed и сносил рабочий
    каталог — сгорали 14 уже скачанных и отрендеренных треков. Пул общий на весь сервер
    и рабочий аккаунт остался один, поэтому «занято» — штатное состояние, а не поломка."""
    from app import album_publisher
    from app.album_db import ALBUM_PENDING
    from app.vk_client import VKTokenBusy

    statuses = []

    class _Queue:
        def set_album_status(self, album_id, status, error=None):
            statuses.append(status)

    class _Album:
        id = 1
        url = "https://soundcloud.com/x/tracks"
        title = "T"
        artist = "A"
        chat_id = None

    work_dir = tmp_path / "album_1"
    work_dir.mkdir()
    monkeypatch.setattr(album_publisher, "download_playlist",
                        lambda *a, **k: (_ for _ in ()).throw(VKTokenBusy("занято")))

    class _Settings:
        work_dir = tmp_path

    class _Config:
        soundcloud = _Settings()

    result = album_publisher._start_album(_Config(), _Queue(), None, None, _Album(), None)

    assert "отложен" in result
    assert statuses[-1] == ALBUM_PENDING
    assert work_dir.exists()  # скачанное не выброшено


def test_busy_token_does_not_burn_track_attempts(tmp_path, monkeypatch):
    """Регрессия: VKTokenBusy — не наследник VKError, поэтому при публикации ТРЕКА он
    пролетал мимо except и ронял тик. А будь пойман общим except — сжигал бы бюджет
    попыток трека и уводил его в failed, хотя занятый пул к треку отношения не имеет."""
    from app import album_publisher
    from app.vk_client import VKTokenBusy

    attempts_burned = []

    class _Queue:
        def next_pending_track(self, album_id):
            class _T:
                id = 1
                position = 1
                title = "T"
                artist = "A"
                audio_path = str(tmp_path / "a.mp3")
                cover_path = str(tmp_path / "c.jpg")
            return _T()

        def mark_track_error(self, track_id, limit):
            attempts_burned.append(track_id)
            return 1

    class _Album:
        id = 1
        title = "Alb"
        artist = "A"
        chat_id = None
        next_post_at = None

    monkeypatch.setattr(album_publisher, "render_track_video", lambda *a, **k: None)
    monkeypatch.setattr(album_publisher, "build_release_header", lambda *a, **k: "h")
    monkeypatch.setattr(album_publisher, "build_release_text", lambda *a, **k: "t")

    class _VK:
        def pool_is_busy(self):
            return False  # слот есть, падаем уже на самой загрузке

        def upload_video(self, *a, **k):
            raise VKTokenBusy("занято")

    result = album_publisher._continue_album(
        _cfg(), _Queue(), _VK(), None, _Album(), album_publisher.now_msk()
    )

    assert "отложен" in result
    assert attempts_burned == [], "занятый пул не должен тратить попытки трека"


def _cfg():
    class _Post:
        track_kind = "Track"

    class _Settings:
        post = _Post()
        min_interval_minutes = 240
        max_interval_minutes = 330
        quiet_start_hour = 0
        quiet_end_hour = 0
        max_track_attempts = 3

    class _Config:
        soundcloud = _Settings()

    return _Config()


def test_busy_pool_skips_render(tmp_path, monkeypatch):
    """Регрессия 07.08: рендер трека шёл ДО проверки токена, а тик ходит каждые 3 минуты.
    Пока аккаунт стоял в кулдауне после ошибки, сервер часами перекодировал один и тот же
    трек впустую — в логе подряд «ffmpeg: сегмент track_002.mp4»."""
    from app import album_publisher

    rendered = []

    class _Queue:
        def next_pending_track(self, album_id):
            class _T:
                id = 1
                position = 2
                title = "2L8"
                artist = "A"
                audio_path = str(tmp_path / "a.mp3")
                cover_path = str(tmp_path / "c.jpg")
            return _T()

    class _Album:
        id = 1
        title = "Alb"
        artist = "A"
        chat_id = None
        next_post_at = None

    class _VKBusy:
        def pool_is_busy(self):
            return True

    monkeypatch.setattr(
        album_publisher, "render_track_video", lambda *a, **k: rendered.append("x")
    )

    result = album_publisher._continue_album(
        _cfg(), _Queue(), _VKBusy(), None, _Album(), album_publisher.now_msk()
    )

    assert "отложен" in result
    assert rendered == [], "при занятом пуле рендерить нечего"
