"""Файл и текст про сборник приходят в ОДИН чат.

ТЗ владельца 2026-08-13: «либо всё боту, либо всё в Избранное, сейчас разъезжается».
Разъезжалось потому, что текст шлёт бот (Bot API), а файл — пользовательская MTProto-
сессия, и её адресат по умолчанию — собственный chat_id, то есть «Избранное».
"""
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from app.delivery import BOT_API_FILE_LIMIT_BYTES, deliver
from app.tg_uploader import resolve_delivery_chat
from app.yt_playlists import DELIVERY_CAPTION_MARKER, build_delivery_caption


@dataclass
class FakePlaylistSettings:
    deliver_chat: str = ""


@dataclass
class FakeConfig:
    telegram_bot_token: str = "123:ABC"
    telegram_admin_chat_id: int | None = 777
    youtube_playlists: FakePlaylistSettings = None

    def __post_init__(self):
        if self.youtube_playlists is None:
            self.youtube_playlists = FakePlaylistSettings()


def test_file_goes_to_the_bot_chat_by_default():
    with patch("app.tg_uploader.bot_username", return_value="@NewsPost1Bot"):
        assert resolve_delivery_chat(FakeConfig()) == "@NewsPost1Bot"


def test_explicit_deliver_chat_wins():
    config = FakeConfig(youtube_playlists=FakePlaylistSettings(deliver_chat="@other"))

    with patch("app.tg_uploader.bot_username", return_value="@NewsPost1Bot"):
        assert resolve_delivery_chat(config) == "@other"


def test_falls_back_to_saved_messages_when_bot_unknown():
    """Telegram не ответил — доставка обязана состояться хоть куда-то: пересобрать
    сборник на 130 МБ дороже, чем переслать его руками из «Избранного»."""
    with patch("app.tg_uploader.bot_username", return_value=""):
        assert resolve_delivery_chat(FakeConfig()) == 777


def test_caption_is_marked_as_delivery():
    """Бот Новостей — тот же самый, и присланное видео он гонит через уникализатор.
    Метка разводит доставку и заказ на уникализацию."""
    caption = build_delivery_caption("Русский рэп 2026")

    assert caption.startswith(DELIVERY_CAPTION_MARKER)
    assert "Русский рэп 2026" in caption


class FakeUploader:
    destination = "@NewsPost1Bot"

    def send_file(self, path, caption=""):
        return True


def test_message_says_where_the_file_went(tmp_path):
    path = tmp_path / "work" / "compilation.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * (BOT_API_FILE_LIMIT_BYTES + 1000))

    result = deliver(
        path,
        ready_dir=tmp_path / "ready",
        file_name="Сборник.mp4",
        bot_token="token",
        chat_id=1,
        uploader=FakeUploader(),
    )

    assert "@NewsPost1Bot" in result.message
    assert Path(result.path).exists()
