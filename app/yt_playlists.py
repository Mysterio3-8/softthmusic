"""Сборники-плейлисты с YouTube: очередь → скачивание → склейка → отдача → публикация.

ТЗ владельца 2026-08-10: «хочу добавить публикацию плейлистов сборников песен, как в
минусах, только сделать лучше: они будут искаться на ютуб или ютуб музыке, скачивать
аудио с картинкой и склеиваться, уже готовые пользовательские плейлисты… название трека
и исполнителя накладывать на видос… в описание по таймингам и описание плейлиста… и
софт скидывал мне этот сборник, чтобы я заливал на ютуб, а в вк он публикуется сам».

Чем это лучше сборников Минусов (там `playlists.py` берёт ЧУЖОЙ готовый ролик целиком
и подменяет фон): здесь сборник собирается из отдельных треков, поэтому у нас есть
честные тайминги, подписи на каждом треке и своя обложка — чужой видеоряд не
переиспользуется вообще.

Один тик = один сборник. Тик короткий и идемпотентный по состоянию БД — его безопасно
дёргать таймером.
"""
from __future__ import annotations

import random
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.album_db import AlbumQueue
from app.album_scheduler import is_quiet_hour, now_msk, to_msk
from app.config import Config
from app.delivery import cleanup_ready, deliver
from app.logger import get_logger
from app.media import MediaError, concat_videos, render_track_video
from app.notifier import Notifier
from app.overlay import TrackCaption
from app.post_builder import build_tracklist
from app.seo import build_hashtags, build_search_line
from app.soundcloud import Track
from app.vk_client import VKClient, VKError, VKTokenBusy
from app.yt_playlist_db import POST_KIND_YT_PLAYLIST, PlaylistQueue, PlaylistRow
from app.yt_source import YouTubeSourceError, discover_playlists, download_playlist


@dataclass(frozen=True)
class Compilation:
    """Готовый сборник: файл + все тексты вокруг него."""

    video_path: Path
    title: str
    post_text: str
    description: str
    tracks: list[Track]


def sync(config: Config, playlists: PlaylistQueue) -> int:
    """Обходит источники и добавляет новые плейлисты в очередь. Возвращает число новых.

    Сломанный источник не рушит остальные: у поисковых запросов выдача меняется, и
    один упавший запрос не повод оставить очередь пустой."""
    settings = config.youtube_playlists
    log = get_logger()
    added = 0
    for source in settings.sources:
        try:
            refs = discover_playlists(source, settings.discover_limit)
        except YouTubeSourceError as exc:
            log.warning("Источник %s не прочитался: %s", source, exc)
            continue
        new = sum(playlists.add(ref.url, ref.title, ref.uploader, source) for ref in refs)
        added += new
        log.info("Источник %s: плейлистов %d, новых %d", source, len(refs), new)
    return added


def tick(
    config: Config,
    playlists: PlaylistQueue,
    posts: AlbumQueue,
    vk: VKClient,
    notifier: Notifier,
    now: datetime | None = None,
) -> str:
    """Один шаг. Возвращает короткое описание сделанного (для логов и CLI)."""
    settings = config.youtube_playlists
    now = to_msk(now) if now else now_msk()

    if not settings.enabled:
        return "поток сборников выключен"
    if _daily_limit_reached(posts, settings.max_posts_per_day, now):
        return "суточный лимит сборников исчерпан"
    if is_quiet_hour(now, settings.quiet_start_hour, settings.quiet_end_hour):
        return "ночная пауза"

    waiting = _minutes_until_due(posts, settings, now)
    if waiting > 0:
        return f"рано: следующий сборник через ~{waiting} мин"

    # Токен спрашиваем ДО скачивания и рендера: сборка сборника — это десятки минут
    # ffmpeg, и делать её ради «токен занят» на финише значит греть VPS впустую
    # (та же грабля, что уже ловили на треках и на Минусах).
    if vk.pool_is_busy():
        return "личный токен занят — ждём следующего тика"

    playlist = playlists.next_pending()
    if playlist is None:
        return "очередь сборников пуста"

    return _process(config, playlists, posts, vk, notifier, playlist, now)


def _daily_limit_reached(posts: AlbumQueue, max_posts_per_day: int, now: datetime) -> bool:
    kinds = (POST_KIND_YT_PLAYLIST,)
    return posts.posts_since(now - timedelta(days=1), kinds) >= max_posts_per_day


def _minutes_until_due(posts: AlbumQueue, settings, now: datetime) -> int:
    """Сколько ещё ждать до следующего сборника. 0 — пора.

    Интервал случайный, но бросок ДЕТЕРМИНИРОВАН временем прошлой публикации. Если
    бросать заново на каждом тике (а тик частый), пост уходит по первому удачному
    броску и диапазон схлопывается в нижнюю границу — этот урок уже оплачен в
    Новостях (`rate_guard.required_interval_minutes`)."""
    last = posts.last_post_at(POST_KIND_YT_PLAYLIST)
    if last is None:
        return 0
    rng = random.Random(int(to_msk(last).timestamp()))
    required = rng.randint(settings.min_interval_minutes, settings.max_interval_minutes)
    elapsed = (now - to_msk(last)).total_seconds() / 60
    return max(0, int(required - elapsed))


def _process(
    config: Config,
    playlists: PlaylistQueue,
    posts: AlbumQueue,
    vk: VKClient,
    notifier: Notifier,
    playlist: PlaylistRow,
    now: datetime,
) -> str:
    settings = config.youtube_playlists
    log = get_logger()
    work_dir = settings.work_dir / f"pl_{playlist.id}"

    try:
        compilation = build_compilation(config, playlists, playlist, work_dir, now)
        attachment = vk.upload_video(
            compilation.video_path, compilation.title, compilation.description
        )
        post_id = vk.post_now(compilation.post_text, attachment)
        posts.log_post(POST_KIND_YT_PLAYLIST)
        playlists.mark_published(playlist.id, f"https://vk.com/wall-{config.group_id}_{post_id}")

        _deliver(config, compilation, notifier)
        return f"опубликован сборник «{compilation.title}» ({len(compilation.tracks)} треков)"
    except VKTokenBusy as exc:
        # НЕ поломка: свободного токена нет прямо сейчас. Попытку не тратим и файлы не
        # выбрасываем — плейлист остаётся в очереди и уйдёт следующим тиком.
        log.warning("Сборник %s отложен: %s", playlist.url, exc)
        return "сборник отложен (нет свободного токена)"
    except (YouTubeSourceError, MediaError, VKError) as exc:
        attempts = playlists.bump_attempt(playlist.id, settings.max_attempts, str(exc))
        log.error("Сборник %s (попытка %d): %s", playlist.url, attempts, exc)
        return f"ошибка сборника, попытка {attempts}"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        cleanup_ready(settings.ready_dir, settings.ready_keep_days)


def build_compilation(
    config: Config,
    playlists: PlaylistQueue,
    playlist: PlaylistRow,
    work_dir: Path,
    now: datetime,
) -> Compilation:
    """Скачать треки, собрать видео и все тексты. Без сети VK — тестируется отдельно."""
    settings = config.youtube_playlists
    tracks = download_playlist(playlist.url, work_dir, settings.max_tracks)
    _ensure_own_covers(tracks)

    title = build_title(settings.title_templates, playlists.recent_titles(10), now)
    video_path = _render(tracks, work_dir)
    tracklist = build_tracklist(
        [f"{track.artist} — {track.title}" if track.artist else track.title for track in tracks],
        [track.duration_s for track in tracks],
    )
    return Compilation(
        video_path=video_path,
        title=title,
        post_text=build_post_text(config, title, tracks),
        description=build_description(config, title, tracklist, tracks),
        tracks=tracks,
    )


def build_title(templates: list[str], recent: list[str], now: datetime) -> str:
    """Название собирается с нуля — название донора не берётся даже частично, чтобы
    в сообщество не утёк чужой брендинг. Уже использованные недавно не повторяем."""
    if not templates:
        return f"Плейлист {now.year}"
    variants = [template.format(year=now.year) for template in templates]
    unused = [variant for variant in variants if variant not in recent]
    return random.choice(unused or variants)


def playlist_artists(tracks: list[Track], limit: int = 8) -> list[str]:
    """Исполнители сборника без повторов — это и есть реальные запросы к нему.

    Название сборника в ключи НЕ идёт: оно придумано нами («Плейлист 2026: музыка на
    каждый день»), никто его не ищет, а в теге вся фраза слипалась в одну нелепую
    простыню `#плейлист_2026_музыка_на_каждый_день`."""
    return list(dict.fromkeys(track.artist for track in tracks if track.artist))[:limit]


def build_post_text(config: Config, title: str, tracks: list[Track]) -> str:
    """Запись на стене — короткая: заголовок, сколько треков, ссылка, теги.

    Треклист с таймингами сюда НЕ идёт (ТЗ 2026-08-10: «в описание по таймингам»):
    под записью он занял бы пол-экрана, а в описании видео он и полезнее, и не мешает."""
    settings = config.youtube_playlists
    style = config.soundcloud.post
    links = [
        f"{label} {url}".strip()
        for label, url in (
            (style.listen_label, style.listen_url),
            (style.channel_label, style.channel_url),
        )
        if url
    ]
    tags = build_hashtags(
        playlist_artists(tracks, 3), style.base_tags, style.hashtag_group, style.post_tag_limit
    )
    blocks = [
        f"{settings.header}\n{title}".strip(),
        f"🎵 Треков в сборнике: {len(tracks)}",
        "\n".join(links),
        " ".join(tags),
    ]
    return "\n\n".join(block for block in blocks if block.strip())


def build_description(config: Config, title: str, tracklist: str, tracks: list[Track]) -> str:
    """Описание видеозаписи — большое: описание сборника, тайминги, ключи, сервис, теги.

    В ленте VK описание свёрнуто, поэтому объём тут бесплатный, а поиск (и внутренний
    VK, и внешние Google/Яндекс) читает его целиком."""
    settings = config.youtube_playlists
    style = config.soundcloud.post
    artists = playlist_artists(tracks)
    blocks = [
        f"{settings.header}\n{title}".strip(),
        settings.playlist_description.strip(),
        tracklist,
        build_search_line(artists, style.search_phrases),
        style.service_block,
        " ".join(
            build_hashtags(
                artists, style.base_tags, style.hashtag_group, style.video_tag_limit
            )
        ),
    ]
    return "\n\n".join(block for block in blocks if block.strip())


def _render(tracks: list[Track], work_dir: Path) -> Path:
    """Каждый трек → сегмент с подписью на первые 10 секунд, потом склейка."""
    segments: list[Path] = []
    for track in tracks:
        segment = work_dir / f"seg_{track.position:03d}.mp4"
        render_track_video(
            track.audio_path, track.cover_path, segment,
            caption=TrackCaption(artist=track.artist, title=track.title),
        )
        segments.append(segment)

    compilation = work_dir / "compilation.mp4"
    concat_videos(segments, compilation)
    for segment in segments:
        segment.unlink(missing_ok=True)
    return compilation


def _ensure_own_covers(tracks: list[Track]) -> None:
    """Бесхозному треку копируем чужую обложку в СВОЙ файл.

    Общий файл на всех был бы миной: сегменты рендерятся по очереди и подчищаются,
    и сосед остался бы без арта (та же причина, что и в альбомном потоке)."""
    donor = next((track.cover_path for track in tracks if track.cover_path), None)
    if donor is None:
        raise MediaError("У треков плейлиста нет ни одной обложки — нечего показывать")
    for track in tracks:
        if track.cover_path:
            continue
        own = track.audio_path.with_suffix(donor.suffix)
        shutil.copyfile(donor, own)
        track.cover_path = own


def _deliver(config: Config, compilation: Compilation, notifier: Notifier) -> None:
    """Отдать готовый файл владельцу. Сбой отдачи НЕ отменяет уже сделанную публикацию."""
    settings = config.youtube_playlists
    try:
        result = deliver(
            compilation.video_path,
            ready_dir=settings.ready_dir,
            file_name=_safe_file_name(compilation.title),
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_admin_chat_id,
            remote_host=settings.remote_host,
            caption=f"{compilation.title}\n\nГотов к заливке на YouTube.",
        )
        notifier.send(
            f"🎬 Сборник «{compilation.title}» опубликован в VK.\n"
            f"Треков: {len(compilation.tracks)}.\nФайл {result.message}\n\n"
            f"Описание для YouTube:\n{compilation.description[:2500]}"
        )
    except Exception as exc:  # noqa: BLE001 — отдача файла не должна ронять тик
        get_logger().warning("Не удалось отдать сборник владельцу: %s", exc)


def _safe_file_name(title: str) -> str:
    """Имя файла из заголовка: без разделителей путей и прочего, что ломает scp."""
    cleaned = "".join(char if char.isalnum() or char in " -_" else "_" for char in title)
    return f"{' '.join(cleaned.split())[:80] or 'playlist'}.mp4"
