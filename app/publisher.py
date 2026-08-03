from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.config import Config
from app.database import Database
from app.logger import get_logger
from app.post_builder import build_post_text, clean_description
from app.schedule_planner import compute_publish_datetimes
from app.vk_client import VKClient, build_token_pool, VKError
from app.youtube import VideoMeta, YouTubeError, download_video, list_channel_video_ids


# Запас кандидатов сверх target — на случай нескачавшихся роликов.
CANDIDATE_BUFFER = 5


@dataclass
class Candidate:
    youtube_id: str
    channel: str


def select_candidates(config: Config, db: Database, count: int, now: datetime) -> list[Candidate]:
    """Находит первые `count` неопубликованных роликов, идя по каналам по порядку."""
    candidates: list[Candidate] = []
    for channel in config.channels:
        if len(candidates) >= count:
            break
        try:
            video_ids = list_channel_video_ids(channel)
        except YouTubeError as exc:
            get_logger().error("Пропуск канала %s: %s", channel, exc)
            continue
        get_logger().info("Канал %s: найдено %d роликов", channel, len(video_ids))
        for youtube_id in video_ids:
            if len(candidates) >= count:
                break
            if db.should_skip(youtube_id, now):
                continue
            candidates.append(Candidate(youtube_id=youtube_id, channel=channel))
    return candidates


def run_once(config: Config, db: Database, now: datetime | None = None) -> int:
    """Готовит до posts_per_day отложенных публикаций. Возвращает число созданных."""
    now = now or datetime.now()
    log = get_logger()
    log.info("=== Запуск подготовки публикаций ===")

    vk = VKClient(
        config.vk_group_token, config.vk_user_token, config.group_id,
        token_pool=build_token_pool(config),
    )
    # Один запуск = posts_per_run роликов (по умолч. 1), каждый на ближайший
    # свободный слот. Джоб запускается несколько раз в день (см. systemd-таймер),
    # поэтому видео грузятся по одному, а не пачкой.
    target = config.posts_per_run
    slots = compute_publish_datetimes(config.publish_times, now)[:target]
    # Берём с запасом: если ролик не скачался (напр. «Video unavailable»),
    # переходим к следующему, чтобы слот не остался пустым.
    candidates = select_candidates(config, db, target + CANDIDATE_BUFFER, now)

    if not candidates:
        log.info("Неопубликованных роликов не найдено — работа завершена")
        return 0

    published = 0
    for candidate in candidates:
        if published >= target:
            break
        slot = slots[published]
        if _process_candidate(config, db, vk, candidate, slot):
            published += 1

    log.info("=== Готово. Создано отложенных публикаций: %d ===", published)
    return published


def _process_candidate(
    config: Config, db: Database, vk: VKClient, candidate: Candidate, slot: datetime
) -> bool:
    log = get_logger()
    meta: VideoMeta | None = None
    try:
        meta = download_video(candidate.youtube_id, config.downloads_dir, config.max_height)
        # Чистим авто-описание YouTube: и из текста поста, и из описания видео,
        # чтобы служебный боилерплейт не всплывал ни в посте, ни «в дополнение»
        # под видео.
        description = clean_description(meta.description)
        attachment = vk.upload_video(meta.file_path, meta.title, description)
        message = build_post_text(meta.title, description, config.ad_block)
        vk.schedule_post(message, attachment, slot)
        db.mark_published(candidate.youtube_id, candidate.channel, meta.title)
        log.info("Ролик %s поставлен в очередь на %s", candidate.youtube_id, slot.isoformat())
        return True
    except (YouTubeError, VKError) as exc:
        title = meta.title if meta else ""
        count = db.mark_error(candidate.youtube_id, candidate.channel, title, config.retry_delays_minutes)
        log.error("Ошибка обработки %s (попытка %d): %s", candidate.youtube_id, count, exc)
        return False
    finally:
        if meta is not None:
            _cleanup(meta.file_path)


def _cleanup(file_path: Path) -> None:
    """Удаляет скачанный файл и возможные временные части рядом с ним."""
    for path in file_path.parent.glob(f"{file_path.stem}.*"):
        try:
            path.unlink()
        except OSError:
            get_logger().warning("Не удалось удалить файл: %s", path)
