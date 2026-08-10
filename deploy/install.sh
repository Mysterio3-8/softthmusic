#!/usr/bin/env bash
# Разовая установка альбомного потока на VPS. Запускать НА СЕРВЕРЕ, от root.
# Повторный запуск безопасен.
set -euo pipefail

REMOTE_DIR="/opt/yt-vk-publisher"

echo "==> Проверка ffmpeg..."
command -v ffmpeg >/dev/null || { echo "!! ffmpeg не найден: apt install ffmpeg"; exit 1; }

echo "==> Зависимости..."
"$REMOTE_DIR/venv/bin/pip" install -q -r "$REMOTE_DIR/requirements.txt"

echo "==> Юниты альбомного потока..."
cp "$REMOTE_DIR/deploy/tg-sc-publisher.service" /etc/systemd/system/
cp "$REMOTE_DIR/deploy/tg-sc-publisher.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tg-sc-publisher.timer

echo "==> Юниты потока сборников с YouTube..."
cp "$REMOTE_DIR/deploy/tg-yt-playlists.service" /etc/systemd/system/
cp "$REMOTE_DIR/deploy/tg-yt-playlists.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tg-yt-playlists.timer

echo "==> Отключение YouTube-потока (решение владельца: альбомы вместо роликов)..."
systemctl disable --now yt-vk-publisher.timer 2>/dev/null || echo "   таймер уже выключен"

echo "==> Состояние:"
systemctl is-active tg-sc-publisher.timer
systemctl is-active tg-yt-playlists.timer
systemctl list-timers --all | grep -E 'tg-sc|tg-yt|yt-vk' || true

echo
echo "Осталось руками:"
echo "  1. Дописать в $REMOTE_DIR/.env — TELEGRAM_BOT_TOKEN и TELEGRAM_ADMIN_CHAT_ID"
echo "  2. Перенести секцию soundcloud: из config.example.yaml в $REMOTE_DIR/config.yaml"
echo "  3. Зарегистрировать софт в реестре менеджера (см. CLAUDE.md, раздел «Пульт в боте»)"
