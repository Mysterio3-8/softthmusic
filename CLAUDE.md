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

## Инварианты

- Секреты только в `.env` (`VK_TOKEN`), никогда в config.yaml/коде.
- Публикация только отложенная (`wall.post` с `publish_date`), не сразу.
- Повторов нет: перед скачиванием `Database.should_skip`.
- После успешной постановки — файл удаляется (на VPS роликов не остаётся).

## Грабли

- Загрузка видео в ВК требует **личный/админский** токен (`video,wall,groups`).
  Групповой токен даёт `[27] method is unavailable with group auth`.
- `VkUpload.video(..., wallpost=0)` — именно `0` (int), не `False`: vk_api
  сериализует Python `False` в строку `"False"`, VK её не парсит.
- yt-dlp нужен ffmpeg в PATH для склейки видео+аудио.
- Точные ретраи +1ч/+3ч работают только в `--schedule`. В `--once` (cron)
  ролик с ошибкой берётся заново на следующем суточном прогоне.

## Осталось

- Живой прогон с реальными токенами (YouTube-листинг, загрузка в ВК, отложка).
- Возможный gate на слишком большие видео (лимит размера ВК).
