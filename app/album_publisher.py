"""Оркестрация альбомного потока: очередь -> скачивание -> склейка -> публикация.

Один тик делает ровно один шаг: либо запускает новый альбом (скачать, склеить,
опубликовать сборник), либо публикует один очередной трек. Тик короткий и
идемпотентный по состоянию БД — его безопасно дёргать таймером каждые N минут.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

from app.album_db import (
    ALBUM_DONE,
    ALBUM_DOWNLOADING,
    ALBUM_FAILED,
    ALBUM_PUBLISHING,
    AlbumQueue,
    AlbumRow,
)
from app.album_scheduler import is_quiet_hour, next_publish_moment, now_msk, soon, to_msk
from app.config import Config
from app.logger import get_logger
from app.media import MediaError, concat_videos, render_track_video
from app.notifier import Notifier
from app.post_builder import build_release_text, build_tracklist, format_title
from app.soundcloud import SoundCloudError, Track, covers_are_identical, download_playlist
from app.vk_client import VKClient, VKError

POST_KIND_ALBUM = "album"
POST_KIND_TRACK = "track"

# Запасная обложка, если у трека её не оказалось: берём первую доступную в альбоме.
_MISSING_COVER_MESSAGE = "У треков нет ни одной обложки — нечего показывать в видео"


def tick(config: Config, queue: AlbumQueue, vk: VKClient, notifier: Notifier,
         now: datetime | None = None) -> str:
    """Один шаг конвейера. Возвращает короткое описание сделанного (для логов и CLI)."""
    now = to_msk(now) if now else now_msk()
    settings = config.soundcloud

    if _daily_limit_reached(queue, settings.max_posts_per_day, now):
        return "суточный лимит постов исчерпан"

    # Ночь проверяется ДО ветвления: сборник — такая же запись в сообществе, как
    # трек, и ночью палит бота ровно так же. Раньше проверка стояла только на
    # ветке треков, и плейлист, брошенный под утро, публиковался в 06:00.
    if is_quiet_hour(now, settings.quiet_start_hour, settings.quiet_end_hour):
        return "ночная пауза"

    album = queue.active_album()
    if album is not None:
        return _continue_album(config, queue, vk, notifier, album, now)

    album = queue.next_pending_album()
    if album is None:
        return "очередь пуста"
    return _start_album(config, queue, vk, notifier, album, now)


def _daily_limit_reached(queue: AlbumQueue, max_posts_per_day: int, now: datetime) -> bool:
    return queue.posts_since(now - timedelta(days=1)) >= max_posts_per_day


def _start_album(
    config: Config, queue: AlbumQueue, vk: VKClient, notifier: Notifier,
    album: AlbumRow, now: datetime,
) -> str:
    """Скачивает плейлист, публикует склейку, ставит альбом в режим потрекового постинга."""
    log = get_logger()
    settings = config.soundcloud
    work_dir = settings.work_dir / f"album_{album.id}"
    queue.set_album_status(album.id, ALBUM_DOWNLOADING)

    try:
        tracks = download_playlist(album.url, work_dir)
        _ensure_own_covers(tracks)
        _store_tracks(queue, album.id, tracks)
        queue.set_album_meta(album.id, album.title, album.artist, len(tracks))

        compilation = _render_compilation(tracks, work_dir)
        _publish_compilation(config, queue, vk, album, tracks, compilation, now)
        compilation.unlink(missing_ok=True)

        queue.set_album_status(album.id, ALBUM_PUBLISHING)
        moment = next_publish_moment(
            now,
            settings.min_interval_minutes,
            settings.max_interval_minutes,
            settings.quiet_start_hour,
            settings.quiet_end_hour,
        )
        queue.set_next_post_at(album.id, moment)
        notifier.send(
            f"🎬 Сборник «{album.title}» опубликован.\n"
            f"Треков в очереди: {len(tracks)}. Первый — примерно {moment:%d.%m %H:%M}.",
            album.chat_id,
        )
        return f"опубликован сборник альбома {album.id}, треков {len(tracks)}"
    except (SoundCloudError, MediaError, VKError) as exc:
        log.error("Альбом %s провалился: %s", album.id, exc)
        queue.set_album_status(album.id, ALBUM_FAILED, str(exc))
        shutil.rmtree(work_dir, ignore_errors=True)
        notifier.send(f"❌ Альбом «{album.title}» не удалось обработать:\n{exc}", album.chat_id)
        return f"альбом {album.id} провален"


def _store_tracks(queue: AlbumQueue, album_id: int, tracks: list[Track]) -> None:
    for track in tracks:
        queue.add_track(
            album_id,
            track.position,
            track.title,
            track.artist,
            track.duration_s,
            str(track.audio_path),
            str(track.cover_path),
        )


def _ensure_own_covers(tracks: list[Track]) -> None:
    """Даёт каждому треку СВОЙ файл обложки, скопировав запасную бесхозным.

    Общий файл на всех был бы миной: треки публикуются по одному в течение
    нескольких дней и подчищают свои файлы за собой — сосед остался бы без арта.
    """
    donor = next((track.cover_path for track in tracks if track.cover_path), None)
    if donor is None:
        raise MediaError(_MISSING_COVER_MESSAGE)

    for track in tracks:
        if track.cover_path:
            continue
        own = track.audio_path.with_suffix(donor.suffix)
        shutil.copyfile(donor, own)
        track.cover_path = own


def _render_compilation(tracks: list[Track], work_dir: Path) -> Path:
    """Склеивает треки в одно видео. Обложка меняется по трекам сама собой:
    у каждого сегмента своя картинка, а если арт у всех один — выходит статика."""
    covers = [track.cover_path for track in tracks if track.cover_path]
    get_logger().info(
        "Обложки в альбоме: %s", "одна на все треки" if covers_are_identical(covers) else "разные"
    )

    segments = []
    for track in tracks:
        segment = work_dir / f"seg_{track.position:03d}.mp4"
        render_track_video(track.audio_path, track.cover_path, segment)
        segments.append(segment)

    compilation = work_dir / "compilation.mp4"
    concat_videos(segments, compilation)
    for segment in segments:
        segment.unlink(missing_ok=True)
    return compilation


def _publish_compilation(
    config: Config, queue: AlbumQueue, vk: VKClient, album: AlbumRow,
    tracks: list[Track], video_path: Path, now: datetime,
) -> None:
    settings = config.soundcloud
    title = format_title(
        settings.album_title_template, album.title, album.artist, settings.censorship_suffix
    )
    tracklist = build_tracklist(
        [track.title for track in tracks], [track.duration_s for track in tracks]
    )
    message = build_release_text(
        settings.post, album.artist, album.title, settings.post.album_kind, tracklist
    )

    attachment = vk.upload_video(video_path, title, tracklist)
    vk.schedule_post(message, attachment, soon(now))
    queue.log_post(POST_KIND_ALBUM)


def _continue_album(
    config: Config, queue: AlbumQueue, vk: VKClient, notifier: Notifier,
    album: AlbumRow, now: datetime,
) -> str:
    """Публикует один трек, если наступило время и не идёт ночная пауза."""
    settings = config.soundcloud

    if album.next_post_at and now < to_msk(album.next_post_at):
        return f"рано: следующий трек в {to_msk(album.next_post_at):%d.%m %H:%M}"

    track = queue.next_pending_track(album.id)
    if track is None:
        return _finish_album(config, queue, notifier, album)

    artist = track.artist or album.artist
    title = format_title(
        settings.track_title_template, track.title, artist, settings.censorship_suffix
    )
    message = build_release_text(settings.post, artist, track.title, settings.post.track_kind)
    video_path = Path(track.audio_path).with_name(f"track_{track.position:03d}.mp4")
    try:
        render_track_video(Path(track.audio_path), Path(track.cover_path), video_path)
        attachment = vk.upload_video(video_path, title, "")
        vk.schedule_post(message, attachment, soon(now))
    except (MediaError, VKError) as exc:
        attempts = queue.mark_track_error(track.id, settings.max_track_attempts)
        get_logger().error("Трек %s (попытка %d): %s", track.title, attempts, exc)
        return f"ошибка трека {track.position}, попытка {attempts}"
    finally:
        video_path.unlink(missing_ok=True)

    queue.mark_track_published(track.id)
    queue.log_post(POST_KIND_TRACK)
    Path(track.audio_path).unlink(missing_ok=True)

    moment = next_publish_moment(
        now,
        settings.min_interval_minutes,
        settings.max_interval_minutes,
        settings.quiet_start_hour,
        settings.quiet_end_hour,
    )
    queue.set_next_post_at(album.id, moment)
    remaining = queue.pending_track_count(album.id)
    get_logger().info("Трек %s опубликован, осталось %d", title, remaining)
    return f"опубликован трек {track.position}, осталось {remaining}"


def _finish_album(
    config: Config, queue: AlbumQueue, notifier: Notifier, album: AlbumRow
) -> str:
    """Треки кончились: чистим файлы, уведомляем, освобождаем конвейер."""
    queue.set_album_status(album.id, ALBUM_DONE)
    shutil.rmtree(config.soundcloud.work_dir / f"album_{album.id}", ignore_errors=True)

    waiting = queue.pending_count()
    tail = f"\nВ очереди ещё альбомов: {waiting}." if waiting else "\nОчередь пуста — кидай следующую ссылку."
    notifier.send(
        f"✅ Альбом «{album.title}» опубликован полностью: сборник + {album.tracks_total} треков.{tail}",
        album.chat_id,
    )
    return f"альбом {album.id} завершён"
