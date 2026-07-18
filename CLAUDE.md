# YouTube → VK Auto Publisher

**Статус:** 🟡 dev — код готов, тесты зелёные (20), живой прогон с реальными
YouTube/VK токенами ещё не проводился.

## Что это

Скрипт-переносчик видео YouTube → сообщество ВК. Раз в сутки берёт N (по умолч. 3)
неопубликованных ролика, скачивает через yt-dlp, грузит в ВК, ставит в отложенные
записи, пишет YouTube ID в SQLite (дедуп), удаляет файлы. Без UI.

## Архитектура

```
main.py → config.load_config → publisher.run_once
  publisher: select_candidates (по каналам по порядку, dedup через Database)
           → download_video (yt-dlp) → VKClient.upload_video → schedule_post
           → Database.mark_published → cleanup файла
```

Слои чистые: `vk_client`/`youtube` — границы внешних API (ловят Exception →
свои VKError/YouTubeError). `publisher` — оркестрация. `database` — только SQLite.
Чистая логика (`post_builder`, `schedule_planner`, `config._normalize_times`) —
без сайд-эффектов, покрыта тестами.

## Быстрые команды

```bash
venv/Scripts/python.exe -m pytest tests/ -q     # тесты
python app/main.py                              # один прогон (cron)
python app/main.py --schedule                   # постоянный процесс
```

## Токены VK (важно — анти-бан)

Два токена по назначению, чтобы не палить user-токен частым постингом:
- `VK_GROUP_TOKEN` (группа 240295467 «TG Music») — `wall.post` (отложенные записи).
- `VK_USER_TOKEN` (админ, scope `video`) — ТОЛЬКО `video.save` (загрузка). VK не даёт
  грузить видео групповым токеном: проверено вживую 2026-07-18 — `video.save`
  групповым токеном → **`[5] User authorization failed`** (не `[27]`, как думали
  по «Новостям», но суть та же — метод только user-контекста).

Видео грузится В ГРУППУ (`video.save(group_id=...)`) → владелец вложения — сообщество,
поэтому групповой токен корректно прикрепляет `video-GID_ID` к записи. User-токен
дёргается 1 раз на ролик (3/день) — минимально, бан в «Новостях» был от 12 подряд
постов user-токеном, а не от редких загрузок.

## Инварианты

- Секреты только в `.env`, никогда в config.yaml/коде.
- Постинг — групповым токеном; user-токен — только загрузка видео.
- Публикация только отложенная (`wall.post` с `publish_date`), не сразу.
- Повторов нет: перед скачиванием `Database.should_skip`.
- После успешной постановки — файл удаляется (на VPS роликов не остаётся).

## Грабли

- `VkUpload.video(..., wallpost=0)` — именно `0` (int), не `False`: vk_api
  сериализует Python `False` в строку `"False"`, VK её не парсит (урок «Новостей»).
- yt-dlp нужен ffmpeg в PATH для склейки видео+аудио.
- Точные ретраи +1ч/+3ч работают только в `--schedule`. В `--once` (cron)
  ролик с ошибкой берётся заново на следующем суточном прогоне.

## Осталось

- Живой прогон: нужен `VK_USER_TOKEN` (пользователь пока дал только групповой).
  Проверить связку upload(user, в группу) → wall.post(group, attach) на реальном ролике.
- Возможный gate на слишком большие видео (лимит размера ВК).
