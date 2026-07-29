"""CLI альбомного потока — точка входа для бота-менеджера и systemd-таймера.

Три команды:
  enqueue <url>  — поставить плейлист в очередь (вызывает бот)
  tick           — один шаг конвейера (вызывает таймер)
  status         — состояние очереди в JSON (вызывает бот)

Отдельный вход от `main.py`, чтобы YouTube-поток и альбомный поток не делили
argparse и не могли задеть друг друга.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.album_db import AlbumQueue  # noqa: E402
from app.album_publisher import tick  # noqa: E402
from app.config import Config, ConfigError, load_config  # noqa: E402
from app.logger import get_logger, setup_logging  # noqa: E402
from app.notifier import Notifier  # noqa: E402
from app.soundcloud import SoundCloudError, fetch_playlist_meta  # noqa: E402
from app.vk_client import VKClient  # noqa: E402

_SOUNDCLOUD_HOSTS = ("soundcloud.com", "on.soundcloud.com", "m.soundcloud.com")


def main() -> int:
    parser = argparse.ArgumentParser(description="SoundCloud -> VK: очередь альбомов")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--env", default=".env")
    sub = parser.add_subparsers(dest="command", required=True)

    enqueue_parser = sub.add_parser("enqueue", help="поставить плейлист в очередь")
    enqueue_parser.add_argument("url")
    enqueue_parser.add_argument("--chat-id", type=int, default=None, help="куда слать уведомления")

    sub.add_parser("tick", help="один шаг конвейера")
    sub.add_parser("status", help="состояние очереди (JSON)")

    args = parser.parse_args()

    try:
        config = load_config(args.config, args.env)
    except ConfigError as exc:
        _fail(f"Ошибка конфигурации: {exc}")
        return 2

    setup_logging(config.log_path)
    queue = AlbumQueue(config.database_path)
    try:
        if args.command == "enqueue":
            return _cmd_enqueue(queue, args.url, args.chat_id)
        if args.command == "status":
            return _cmd_status(queue)
        return _cmd_tick(config, queue)
    finally:
        queue.close()


def _cmd_enqueue(queue: AlbumQueue, url: str, chat_id: int | None) -> int:
    url = url.strip()
    if not any(host in url for host in _SOUNDCLOUD_HOSTS):
        _fail("Это не ссылка на SoundCloud")
        return 2

    try:
        meta = fetch_playlist_meta(url)
    except SoundCloudError as exc:
        _fail(str(exc))
        return 1

    album_id = queue.enqueue(url, meta.title, meta.artist, meta.track_count, chat_id)
    ahead = queue.pending_count() - 1
    print(json.dumps(
        {
            "ok": True,
            "album_id": album_id,
            "title": meta.title,
            "artist": meta.artist,
            "tracks": meta.track_count,
            "ahead_in_queue": max(0, ahead),
        },
        ensure_ascii=False,
    ))
    return 0


def _cmd_status(queue: AlbumQueue) -> int:
    active = queue.active_album()
    payload = {
        "ok": True,
        "pending_albums": queue.pending_count(),
        "active": None,
    }
    if active is not None:
        payload["active"] = {
            "title": active.title,
            "status": active.status,
            "tracks_total": active.tracks_total,
            "tracks_left": queue.pending_track_count(active.id),
            "next_post_at": active.next_post_at.isoformat() if active.next_post_at else None,
        }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _cmd_tick(config: Config, queue: AlbumQueue) -> int:
    log = get_logger()
    vk = VKClient(config.vk_group_token, config.vk_user_token, config.group_id)
    notifier = Notifier(config.telegram_bot_token, config.telegram_admin_chat_id)
    try:
        outcome = tick(config, queue, vk, notifier)
    except Exception:  # noqa: BLE001 — верхний уровень тика, логируем и падаем с кодом
        log.exception("Непредвиденная ошибка в тике альбомного потока")
        return 1
    log.info("Тик: %s", outcome)
    print(outcome)
    return 0


def _fail(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
