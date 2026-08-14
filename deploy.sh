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

# ⚠️ ssh из Git Bash НЕ читает ~/.ssh/config, если имя пользователя Windows написано
# кириллицей: msys-сборка OpenSSH не находит домашний каталог, читает только
# /etc/ssh/ssh_config и падает с «Could not resolve hostname news-rewriter-vps».
# Хук post-commit работает именно в этой оболочке, поэтому «коммит = деплой» молча не
# срабатывал (обнаружено 2026-08-11). Передаём конфиг явно, если он существует.
# Явных -F/-o оказалось мало: путь к КЛЮЧУ msys тоже отдаёт в своей кодировке
# (`/c/Users/\310\353\374\377/.ssh/id_ed25519`), файла по нему нет, и ssh падает уже на
# «no such identity». Поэтому сначала пробуем ВИНДОВЫЙ ssh.exe — он читает те же конфиг
# и known_hosts, но домашний каталог берёт из Windows и кириллицу переваривает.
WIN_SSH="/c/Windows/System32/OpenSSH/ssh.exe"
SSH=(ssh)
if [ -x "$WIN_SSH" ]; then
  SSH=("$WIN_SSH")
elif [ -f "$HOME/.ssh/config" ]; then
  # known_hosts указываем тем же явным путём: иначе ssh ищет его по ненайденному
  # домашнему каталогу, не находит запись сервера и падает «Host key verification failed».
  SSH=(ssh -F "$HOME/.ssh/config" -o "UserKnownHostsFile=$HOME/.ssh/known_hosts")
fi
ssh() { command "${SSH[@]}" "$@"; }

echo "==> Синхронизация app/, tests/, deploy/ и assets/ на $HOST..."
# assets/ добавлен вместе с подписями на видео (2026-08-10): без шрифта
# DejaVuSans-Bold.ttf подпись «исполнитель — трек» молча не рисуется.
tar -czf - --exclude='__pycache__' --exclude='*.pyc' app tests deploy assets \
  | ssh "$HOST" "tar -xzf - -C $REMOTE_DIR"

echo "==> Проверка импортов на сервере..."
ssh "$HOST" "cd $REMOTE_DIR && venv/bin/python -c '
import app.publisher
import app.album_publisher
import app.soundcloud
import app.media
import app.soundcloud_cli
import app.yt_playlists
import app.yt_playlists_cli
' " || { echo "!! Импорты сломаны — таймер НЕ тронут, старый код продолжает работать."; exit 1; }

echo "==> Импорты чистые. Обновление юнитов и перезапуск таймеров..."
# Правку .timer/.service из репозитория надо доносить до /etc/systemd — иначе
# сервер молча живёт со старым расписанием (поймано 2026-07-29).
ssh "$HOST" "cp $REMOTE_DIR/deploy/tg-sc-publisher.{service,timer} /etc/systemd/system/ \
  && cp $REMOTE_DIR/deploy/tg-yt-playlists.{service,timer} /etc/systemd/system/ \
  && systemctl daemon-reload \
  && systemctl restart $TIMER && systemctl is-active $TIMER"

echo "==> Готово."
