"""Сборник отдаётся владельцу ДО публикации в VK (ТЗ 2026-08-10)."""
from pathlib import Path

import pytest

from app.yt_playlist_db import PlaylistQueue
from app.yt_playlists import _deliver


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send(self, text, chat_id=None):
        self.messages.append(text)
        return True


class _Compilation:
    def __init__(self, path: Path):
        self.video_path = path
        self.title = "Плейлист 2026"
        self.description = "описание"
        self.post_text = "пост"
        self.tracks = []


class _Settings:
    def __init__(self, tmp_path):
        self.ready_dir = tmp_path / "ready"
        self.remote_host = "vps"
        self.deliver_chat = ""


class _Config:
    def __init__(self, tmp_path):
        self.youtube_playlists = _Settings(tmp_path)
        self.telegram_bot_token = ""
        self.telegram_admin_chat_id = None
        self.tg_api_id = 0
        self.tg_api_hash = ""
        self.tg_session_name = ""


@pytest.fixture
def queue(tmp_path):
    q = PlaylistQueue(tmp_path / "db.sqlite")
    yield q
    q.close()


def _compilation(tmp_path) -> _Compilation:
    path = tmp_path / "work" / "compilation.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"video")
    return _Compilation(path)


def test_delivery_returns_the_path_to_upload_from(tmp_path, queue):
    """Отдача ПЕРЕНОСИТ файл в ready/ — грузить в VK надо возвращённый путь,
    иначе загрузка не найдёт файл."""
    queue.add("https://youtube.com/playlist?list=1", "t", "u", "src")
    playlist = queue.next_pending()

    path = _deliver(_Config(tmp_path), queue, playlist, _compilation(tmp_path), FakeNotifier())

    assert path.exists()
    assert path.parent.name == "ready"


def test_delivery_is_recorded_in_the_queue(tmp_path, queue):
    queue.add("https://youtube.com/playlist?list=1", "t", "u", "src")
    playlist = queue.next_pending()

    _deliver(_Config(tmp_path), queue, playlist, _compilation(tmp_path), FakeNotifier())

    assert queue.next_pending().delivered is True


def test_second_attempt_does_not_send_the_file_twice(tmp_path, queue):
    """Публикация в VK может сорваться (занят токен), и плейлист вернётся в очередь.
    Второй раз тот же сборник владельцу присылать не надо."""
    queue.add("https://youtube.com/playlist?list=1", "t", "u", "src")
    _deliver(_Config(tmp_path), queue, queue.next_pending(), _compilation(tmp_path), FakeNotifier())

    notifier = FakeNotifier()
    second = _compilation(tmp_path)
    path = _deliver(_Config(tmp_path), queue, queue.next_pending(), second, notifier)

    assert notifier.messages == []
    assert path == second.video_path  # грузим из рабочего каталога, как и было


def test_notification_says_it_is_publishing_not_published(tmp_path, queue):
    queue.add("https://youtube.com/playlist?list=1", "t", "u", "src")
    notifier = FakeNotifier()

    _deliver(_Config(tmp_path), queue, queue.next_pending(), _compilation(tmp_path), notifier)

    assert "Публикую в VK" in notifier.messages[0]
