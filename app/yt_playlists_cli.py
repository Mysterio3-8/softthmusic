"""CLI потока сборников с YouTube — точка входа для systemd-таймера и для рук.

Команды:
  sync    — обойти источники и добавить новые плейлисты в очередь
  tick    — один шаг конвейера (скачать/склеить/опубликовать один сборник)
  status  — состояние очереди в JSON

Отдельный вход от `soundcloud_cli.py`: потоки независимы, и общий argparse позволил бы
им задеть друг друга.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.album_db import AlbumQueue  # noqa: E402
from app.config import Config, ConfigError, load_config  # noqa: E402
from app.logger import get_logger, setup_logging  # noqa: E402
from app.notifier import Notifier  # noqa: E402
from app.vk_client import VKClient, build_token_pool  # noqa: E402
from app.yt_playlist_db import PlaylistQueue  # noqa: E402
from app.yt_playlists import sync, tick  # noqa: E402

RETRY_FAILED_AFTER_HOURS = 12
"""Через сколько часов упавший плейлист можно попробовать снова.

Меньше суток намеренно: сборников всего два в день, и ждать сутки значит потерять их
оба. Двенадцать часов — достаточно, чтобы кончился кулдаун токена и разошлась нагрузка
на память, но не настолько долго, чтобы сообщество простаивало."""


def main() -> int:
    parser = argparse.ArgumentParser(description="YouTube-плейлисты -> VK: сборники")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--env", default=".env")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="обновить очередь плейлистов с источников")
    sub.add_parser("tick", help="один шаг конвейера")
    sub.add_parser("status", help="состояние очереди (JSON)")
    args = parser.parse_args()

    try:
        config = load_config(args.config, args.env)
    except ConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    setup_logging(config.log_path)
    playlists = PlaylistQueue(config.database_path)
    try:
        if args.command == "sync":
            return _cmd_sync(config, playlists)
        if args.command == "status":
            return _cmd_status(playlists)
        return _cmd_tick(config, playlists)
    finally:
        playlists.close()


def _cmd_sync(config: Config, playlists: PlaylistQueue) -> int:
    added = sync(config, playlists)
    print(json.dumps(
        {"ok": True, "added": added, "pending": playlists.pending_count()}, ensure_ascii=False
    ))
    return 0


def _cmd_status(playlists: PlaylistQueue) -> int:
    print(json.dumps({"ok": True, "pending": playlists.pending_count()}, ensure_ascii=False))
    return 0


def _cmd_tick(config: Config, playlists: PlaylistQueue) -> int:
    log = get_logger()
    # Очередь пуста — сами добираем плейлисты. Иначе поток молча простаивал бы, пока
    # кто-то не вызовет sync руками; для потока, который ищет источники сам, это
    # неправильное поведение по умолчанию.
    if playlists.pending_count() == 0:
        sync(config, playlists)
        # Sync ничего не дал — поднимаем давно упавшие. Без этого очередь пустеет
        # НАВСЕГДА: упавший плейлист остаётся в таблице, повторный sync его игнорирует
        # (INSERT OR IGNORE по уникальному url), а выдача YouTube по тем же запросам
        # приносит тот же набор ссылок. Причины падений почти всегда временные —
        # занятый токен, OOM при рендере, оборванная закачка (см. revive_failed).
        if playlists.pending_count() == 0:
            revived = playlists.revive_failed(RETRY_FAILED_AFTER_HOURS)
            if revived:
                log.warning("Очередь сборников пуста — вернул в работу упавших: %d", revived)

    posts = AlbumQueue(config.database_path)
    vk = VKClient(
        config.vk_group_token, config.vk_user_token, config.group_id,
        token_pool=build_token_pool(config),
    )
    notifier = Notifier(config.telegram_bot_token, config.telegram_admin_chat_id)
    try:
        outcome = tick(config, playlists, posts, vk, notifier)
    except Exception:  # noqa: BLE001 — верхний уровень тика, логируем и падаем с кодом
        log.exception("Непредвиденная ошибка в тике потока сборников")
        return 1
    finally:
        posts.close()
    log.info("Тик сборников: %s", outcome)
    print(outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
