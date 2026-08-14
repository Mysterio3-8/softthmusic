"""Уведомления в Telegram через Bot API.

Только отправка (`sendMessage`) — поллинга нет, поэтому конфликта с ботом
Новостей, который держит `getUpdates` на том же токене, не возникает.
Сбой уведомления не должен ронять публикацию: логируем и живём дальше.
"""
from __future__ import annotations

import requests

from app.logger import get_logger

TELEGRAM_TIMEOUT_SECONDS = 15


def bot_username(bot_token: str) -> str:
    """`@имя` бота, которым уходят уведомления. Пустая строка — узнать не удалось.

    Нужно, чтобы файл и текст приходили в ОДИН чат (ТЗ владельца 2026-08-13: «либо всё
    боту, либо всё в Избранное, сейчас разъезжается»). Текст шлёт бот, а файл — ваша
    пользовательская сессия MTProto (у Bot API потолок отдачи 50 МБ, сборник весит
    120–150). Отправив файл САМОМУ боту, кладём его в ту же переписку, где лежит текст.

    Имя спрашиваем у Telegram, а не держим в конфиге: захардкоженное имя расходится с
    реальным токеном молча, и файл уходил бы неизвестно куда."""
    if not bot_token:
        return ""
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getMe",
            timeout=TELEGRAM_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        username = response.json().get("result", {}).get("username", "")
    except (requests.RequestException, ValueError) as exc:
        get_logger().warning("Не удалось узнать имя бота: %s", exc)
        return ""
    return f"@{username}" if username else ""


class Notifier:
    def __init__(self, bot_token: str, default_chat_id: int | None) -> None:
        self._token = bot_token
        self._default_chat_id = default_chat_id

    def send(self, text: str, chat_id: int | None = None) -> bool:
        target = chat_id or self._default_chat_id
        if not self._token or not target:
            get_logger().info("Уведомление пропущено (нет токена или chat_id): %s", text)
            return False

        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": target, "text": text, "disable_web_page_preview": True},
                timeout=TELEGRAM_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            get_logger().warning("Не удалось отправить уведомление в Telegram: %s", exc)
            return False
        return True
