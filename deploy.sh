#!/usr/bin/env bash
# Автодеплой на прод-VPS (news-rewriter-vps). Вызывается post-commit хуком —
# правило «коммит = деплой» для всех софтов на VPS.
#
# Синхронизирует только app/ и tests/ (.env и config.yaml у сервера свои и
# редактируются вручную), проверяет импорты ПЕРЕД тем как трогать юниты, чтобы
# сломанный код не встал в расписание.
#
# ВНИМАНИЕ: тарит файлы С ДИСКА, а не из git-состояния. Чужая незакоммиченная
# правка уедет на прод вместе с твоим коммитом — перед коммитом смотри git status.
set -euo pipefail

HOST="news-rewriter-vps"
REMOTE_DIR="/opt/yt-vk-publisher"
TIMER="tg-sc-publisher.timer"

echo "==> Синхронизация app/, tests/ и deploy/ на $HOST..."
tar -czf - --exclude='__pycache__' --exclude='*.pyc' app tests deploy \
  | ssh "$HOST" "tar -xzf - -C $REMOTE_DIR"

echo "==> Проверка импортов на сервере..."
ssh "$HOST" "cd $REMOTE_DIR && venv/bin/python -c '
import app.publisher
import app.album_publisher
import app.soundcloud
import app.media
import app.soundcloud_cli
' " || { echo "!! Импорты сломаны — таймер НЕ тронут, старый код продолжает работать."; exit 1; }

echo "==> Импорты чистые. Обновление юнитов и перезапуск $TIMER..."
# Правку .timer/.service из репозитория надо доносить до /etc/systemd — иначе
# сервер молча живёт со старым расписанием (поймано 2026-07-29).
ssh "$HOST" "cp $REMOTE_DIR/deploy/tg-sc-publisher.{service,timer} /etc/systemd/system/ \
  && systemctl daemon-reload \
  && systemctl restart $TIMER && systemctl is-active $TIMER"

echo "==> Готово."
