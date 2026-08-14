"""Отдача готового сборника владельцу — чтобы он залил его на YouTube руками.

ТЗ 2026-08-10: «хочу, чтобы софт скидывал мне этот сборник на пк локально или в тг».
Софт живёт на VPS, положить файл прямо на домашний ПК он не может, поэтому:

1. файл переносится из рабочего каталога в `ready/` и там ЖИВЁТ (а не удаляется
   сразу после публикации, как раньше);
2. если он влезает в лимит Telegram Bot API — уходит в личку файлом;
3. если нет — в личку уходит команда `scp`, которой файл забирается одной строкой.

Почему не «всегда в Telegram»: Bot API режет отдачу на 50 МБ, а сборник из 15 треков
весит заметно больше. Обойти это можно только MTProto-сессией (Telethon) — это
отдельный логин и отдельная зависимость, заводить её без спроса не стали.

Адресат по умолчанию — ЧАТ С БОТОМ уведомлений (`tg_uploader.resolve_delivery_chat`),
чтобы файл лежал рядом с текстом про этот же сборник. ТЗ владельца 2026-08-13: «файл и
текст в одно место, сейчас разъезжается» — раньше текст слал бот, а файл уходил в
«Избранное» пользовательской сессии.
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from app.logger import get_logger

BOT_API_FILE_LIMIT_BYTES = 50 * 1024 * 1024
UPLOAD_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class DeliveryResult:
    path: Path
    sent_to_telegram: bool
    message: str


def deliver(
    video_path: Path,
    *,
    ready_dir: Path,
    file_name: str,
    bot_token: str,
    chat_id: int | None,
    remote_host: str = "",
    caption: str = "",
    uploader=None,
) -> DeliveryResult:
    """Переносит сборник в ready_dir и отправляет его владельцу в Telegram.

    Порядок попыток жёсткий и не случайный:

    1. **MTProto** (`uploader`) — единственный путь, которым реально уходит сборник:
       он весит 120-150 МБ, а Bot API отдаёт максимум 50 МБ. Первый живой сборник
       (128 МБ) именно поэтому и не пришёл.
    2. **Bot API** — только если файл вдруг мелкий (короткий плейлист).
    3. **ready/ + команда scp** — когда ни один канал не настроен.
    """
    stored = _store(video_path, ready_dir, file_name)
    size_mb = stored.stat().st_size / 1e6

    if uploader is not None and uploader.send_file(stored, caption):
        where = getattr(uploader, "destination", "")
        target = f" → {where}" if where else ""
        return DeliveryResult(stored, True, f"отправлен в Telegram ({size_mb:.0f} МБ){target}")

    if stored.stat().st_size <= BOT_API_FILE_LIMIT_BYTES and bot_token and chat_id:
        if _send_document(stored, bot_token, chat_id, caption):
            return DeliveryResult(stored, True, f"отправлен в Telegram ({size_mb:.0f} МБ)")

    hint = _pull_command(stored, remote_host)
    return DeliveryResult(stored, False, f"лежит на сервере ({size_mb:.0f} МБ)\n{hint}")


def _store(video_path: Path, ready_dir: Path, file_name: str) -> Path:
    ready_dir.mkdir(parents=True, exist_ok=True)
    target = ready_dir / file_name
    shutil.move(str(video_path), target)
    get_logger().info("Сборник сохранён для владельца: %s", target)
    return target


def _pull_command(path: Path, remote_host: str) -> str:
    host = remote_host or "news-rewriter-vps"
    return f"Забрать одной командой:\nscp {host}:{path} ."


def _send_document(path: Path, bot_token: str, chat_id: int, caption: str) -> bool:
    """sendDocument, а не sendVideo: Telegram перекодирует видео и режет качество,
    а владельцу нужен ровно тот файл, который уедет на YouTube."""
    try:
        with path.open("rb") as handle:
            response = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendDocument",
                data={"chat_id": chat_id, "caption": caption[:1024]},
                files={"document": (path.name, handle)},
                timeout=UPLOAD_TIMEOUT_SECONDS,
            )
        response.raise_for_status()
    except requests.RequestException as exc:
        get_logger().warning("Не удалось отправить сборник в Telegram: %s", exc)
        return False
    return True


def cleanup_ready(ready_dir: Path, keep_days: int) -> int:
    """Удаляет старые сборники. Диск VPS маленький, а файлы тяжёлые: без уборки
    каталог `ready/` забьёт его за пару недель. Возвращает число удалённых."""
    if keep_days <= 0 or not ready_dir.exists():
        return 0
    deadline = time.time() - keep_days * 86400
    removed = 0
    for item in ready_dir.iterdir():
        if item.is_file() and item.stat().st_mtime < deadline:
            item.unlink(missing_ok=True)
            removed += 1
    if removed:
        get_logger().info("Удалено старых сборников из ready/: %d", removed)
    return removed
