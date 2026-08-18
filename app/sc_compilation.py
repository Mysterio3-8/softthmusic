"""Сборник из треков SoundCloud — запасной источник, когда YouTube не отдаёт.

Зачем. Сборники зависели от ОДНОГО источника — пользовательских плейлистов YouTube, и
любая его проблема останавливала поток целиком. За две недели это случилось трижды по
разным причинам: барьер «я не бот» на серверном IP (11.08), подборки часовых диджей-
миксов вместо песен (14–16.08), `403 Forbidden` от CDN на самих данных при исправных
метаданных (16.08). Каждый раз сообщество молчало сутками при полностью исправном коде.

ТЗ владельца 2026-08-16: «можешь с sc плейлисты брать или свои создавать софт будет,
если на ютубе проблемы».

Почему это дёшево. Тип `Track` у обоих потоков ОБЩИЙ (живёт в `soundcloud.py`), поэтому
рендер, склейка, тексты, отдача и публикация переиспользуются как есть — новый здесь
только способ добыть треки. Плюс поиск по SoundCloud уже написан и работает: он же
наполняет очередь одиночных треков (`sc_discovery`).

⚠️ Это НЕ чужой плейлист, а собственная подборка: софт берёт популярные треки поиском и
склеивает их сам. Отсюда и заголовок строится по исполнителям ИЗ САМОЙ подборки, а не по
названию донора — донора попросту нет.
"""
from __future__ import annotations

import random
from pathlib import Path

from app.logger import get_logger
from app.sc_discovery import collect_new_tracks
from app.soundcloud import Track, download_track
from app.track_naming import clean_artist, clean_title, usable_track

SC_URL_PREFIX = "sc:auto:"
"""Метка синтетического плейлиста в очереди.

Своя строка в очереди нужна, чтобы весь учёт (отдан ли файл, что уже публиковали, какие
названия были недавно) работал ровно так же, как у плейлистов YouTube. Без неё пришлось
бы заводить второй, параллельный учёт — и он бы разошёлся с первым."""


def is_sc_source(url: str) -> bool:
    return (url or "").startswith(SC_URL_PREFIX)


def pick_genre(sources: list[str]) -> str:
    """Один источник = один ЖАНР сборника.

    ТЗ владельца 2026-08-17: «можно сделать рэп плейлисты, фонк плейлисты, атмосферные —
    по жанрам». Раньше все источники сливались в один котёл, и подборка выходила
    винегретом: рэп вперемешку с попсой. Тематический сборник и слушается лучше, и в
    поиске находится по своему запросу.

    Список жанров правится ИЗ БОТА (📦 Софты → 📥 Источники): это те же
    `soundcloud.discovery.sources`, которые накрывает контракт менеджера."""
    return random.choice([s for s in sources if s.strip()]) if sources else ""


def genre_query(genres, name: str) -> str:
    """Поисковый запрос жанра по его имени. Нет в конфиге — ищем по самому имени.

    Запас нужен, потому что список жанров правится из бота: строка заказа переживает
    удаление жанра из конфига, и падать из-за этого сборнику незачем."""
    for genre in genres or ():
        if genre.name.casefold() == (name or "").casefold():
            return genre.query or genre.name
    return (name or "").strip()


def collect_tracks(
    sources: list[str],
    target_dir: Path,
    *,
    wanted: int,
    min_tracks: int,
    max_track_seconds: int,
    min_plays: int,
    known_names: set[str] | None = None,
) -> list[Track]:
    """Скачать `wanted` популярных треков в сборник. Возвращает готовые Track.

    Ищем с запасом (`wanted * 2`): часть треков SoundCloud отдаёт под DRM, часть не
    качается вовсе, и без запаса сборник каждый раз выходил бы короче задуманного.

    Гейт длительности тот же, что у YouTube-плейлистов: часовой диджей-микс одинаково
    вреден независимо от того, откуда он приехал."""
    log = get_logger()
    refs = collect_new_tracks(
        sources,
        known_urls=set(),
        known_names=known_names,
        # Запас ВТРОЕ, а не вдвое: с 2026-08-18 к обычным потерям (DRM, битые ссылки)
        # добавился отсев по именам, и на мусорной выдаче он режет заметную долю.
        wanted=wanted * 3,
        min_plays=min_plays,
    )
    if not refs:
        return []

    tracks: list[Track] = []
    seen_names: set[str] = set()
    for ref in refs:
        if len(tracks) >= wanted:
            break
        # 🔴 Гейт имён (ТЗ владельца 2026-08-18 по сборнику 285). В треклист попадали
        # «New Russian Rap Music — Батальон Морской Пехоты», «⊹ — ВАЙБ 2025 (ФОНК…)»,
        # «**** — icarus. - Русский Фонк». Для очереди ТРЕКОВ такие находки терпимы —
        # там подпись поста собирается иначе, — а в сборнике имена видны списком, и
        # мусор в них читается как поломка софта.
        if not ref.artist_from_title:
            log.info("Трек «%s» без разделителя «артист — песня» — пропускаю", ref.title)
            continue
        if not usable_track(ref.artist, ref.title):
            log.info("Трек «%s — %s» не похож на песню — пропускаю", ref.artist, ref.title)
            continue
        # Дедуп внутри ОДНОГО сборника: два перезалива одного хита рядом в треклисте
        # выглядят как поломка, хотя ссылки у них разные.
        name_key = f"{ref.artist.lower().strip()}|{ref.title.lower().strip()}"
        if name_key in seen_names:
            continue
        try:
            track = download_track(ref.url, target_dir)
        except Exception as error:  # noqa: BLE001 — DRM и битые ссылки это норма
            log.info("Трек %s не скачался (%s) — пропускаю", ref.url, str(error)[:80])
            continue
        if track.duration_s <= 0 or track.duration_s > max_track_seconds:
            log.info(
                "Трек «%s» длится %d с — не песня, пропускаю", track.title, track.duration_s
            )
            track.audio_path.unlink(missing_ok=True)
            continue
        seen_names.add(name_key)
        # Позиция задаётся ЗДЕСЬ, а не берётся из download_track: у него каждый трек
        # приходит одиночным и получает позицию 1, а нумерация в треклисте сквозная.
        # 🔴 Имена берём РАЗОБРАННЫЕ (из ref), а не сырые из `download_track`. Там
        # `artist` — это `uploader`, то есть паблик-перезаливщик, а `title` — полное
        # название ролика с хвостами. Ровно они и уехали на стену 2026-08-18, хотя
        # разобранная пара всё это время лежала рядом.
        tracks.append(
            Track(
                position=len(tracks) + 1,
                title=clean_title(ref.title),
                artist=clean_artist(ref.artist),
                duration_s=track.duration_s,
                audio_path=track.audio_path,
                cover_path=track.cover_path,
            )
        )

    if len(tracks) < min_tracks:
        log.warning(
            "Из SoundCloud набралось %d треков при минимуме %d — сборник не собираем",
            len(tracks), min_tracks,
        )
        return []
    log.info("Сборник из SoundCloud: %d треков", len(tracks))
    return tracks
