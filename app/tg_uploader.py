"""Отправка большого файла в Telegram через MTProto (Telethon).

Зачем отдельно от `notifier.py`: тот шлёт текст ботом, а у Bot API потолок отдачи
**50 МБ**. Сборник из 15 треков весит 120-150 МБ (первый живой — 128 МБ), поэтому
ботом он не уходил вообще, и владелец видел «плейлист в тг не пришёл». MTProto
поднимает потолок до 2 ГБ.

Сессия — ОТДЕЛЬНАЯ КОПИЯ файла сессии Новостей: аккаунт тот же, но свой файл.
Общий файл ловит «database is locked», потому что читалку Новостей держит открытой
другой процесс (этот урок уже оплачен в `NewsSoft/core/publishing/
telethon_video_publisher.py` — здесь тот же приём).
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from app.logger import get_logger

CAPTION_LIMIT = 1024


class TelegramUploader:
    """None вместо экземпляра, если креды не заданы — вызывающий уйдёт на запасной путь."""

    def __init__(self, *, api_id: int, api_hash: str, session_name: str, chat_id: int) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_name = session_name
        self._chat_id = chat_id

    @classmethod
    def from_config(cls, config) -> "TelegramUploader | None":
        if not (config.tg_api_id and config.tg_api_hash and config.tg_session_name):
            return None
        if not config.telegram_admin_chat_id:
            return None
        return cls(
            api_id=config.tg_api_id,
            api_hash=config.tg_api_hash,
            session_name=config.tg_session_name,
            chat_id=config.telegram_admin_chat_id,
        )

    def send_file(self, path: Path, caption: str = "") -> bool:
        """True — файл ушёл. Ошибку наружу не пускаем: публикация в VK уже состоялась,
        и провал доставки не должен её отменять."""
        try:
            return asyncio.run(self._send(path, caption))
        except Exception:
            get_logger().exception("MTProto: не удалось отправить %s", path.name)
            return False

    async def _send(self, path: Path, caption: str) -> bool:
        from telethon import TelegramClient  # импорт внутри: без telethon модуль живёт

        session = _own_session_path(self._session_name)
        client = TelegramClient(str(session), self._api_id, self._api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                get_logger().error("MTProto: сессия %s не авторизована", session)
                return False
            # Документом, а не видео: Telegram перекодирует видео и режет качество, а
            # владельцу нужен ровно тот файл, который поедет на YouTube.
            await client.send_file(
                self._chat_id,
                str(path),
                caption=caption[:CAPTION_LIMIT],
                force_document=True,
            )
            get_logger().info("MTProto: %s отправлен владельцу", path.name)
            return True
        finally:
            await client.disconnect()


def _own_session_path(session_name: str) -> Path:
    """Своя копия сессии. Делается один раз, дальше живёт сама."""
    source = Path(f"{session_name}.session")
    own = Path(f"{session_name}_music.session")
    if not own.exists() and source.exists():
        shutil.copy(source, own)
        get_logger().info("MTProto: создана копия сессии %s", own)
    return own.with_suffix("")
