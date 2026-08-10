"""Отдача готового сборника владельцу: MTProto → Bot API → ready/ + scp."""
from pathlib import Path

from app.delivery import BOT_API_FILE_LIMIT_BYTES, deliver


class FakeUploader:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send_file(self, path, caption=""):
        self.sent.append(Path(path))
        return self.ok


def _compilation(tmp_path, size_bytes: int) -> Path:
    path = tmp_path / "work" / "compilation.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size_bytes)
    return path


def test_big_file_goes_through_mtproto(tmp_path):
    """Ровно этот случай и сломался вживую: сборник 128 МБ ботом не уходил вообще."""
    uploader = FakeUploader()

    result = deliver(
        _compilation(tmp_path, BOT_API_FILE_LIMIT_BYTES + 1000),
        ready_dir=tmp_path / "ready",
        file_name="Сборник.mp4",
        bot_token="token",
        chat_id=1,
        uploader=uploader,
    )

    assert result.sent_to_telegram is True
    assert uploader.sent == [tmp_path / "ready" / "Сборник.mp4"]


def test_file_is_kept_in_ready_even_after_sending(tmp_path):
    """Файл нужен владельцу и после отправки — заливать на YouTube он будет позже."""
    result = deliver(
        _compilation(tmp_path, 100),
        ready_dir=tmp_path / "ready",
        file_name="Сборник.mp4",
        bot_token="",
        chat_id=None,
        uploader=FakeUploader(),
    )

    assert result.path.exists()


def test_falls_back_to_scp_hint_without_any_channel(tmp_path):
    result = deliver(
        _compilation(tmp_path, 100),
        ready_dir=tmp_path / "ready",
        file_name="Сборник.mp4",
        bot_token="",
        chat_id=None,
        remote_host="vps",
    )

    assert result.sent_to_telegram is False
    assert "scp vps:" in result.message


def test_mtproto_failure_falls_back(tmp_path):
    """Сбой MTProto не должен глотать файл: он остаётся в ready/ с подсказкой."""
    result = deliver(
        _compilation(tmp_path, BOT_API_FILE_LIMIT_BYTES + 1000),
        ready_dir=tmp_path / "ready",
        file_name="Сборник.mp4",
        bot_token="",
        chat_id=None,
        remote_host="vps",
        uploader=FakeUploader(ok=False),
    )

    assert result.sent_to_telegram is False
    assert result.path.exists()
