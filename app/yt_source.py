"""Граница YouTube / YouTube Music (yt-dlp): поиск готовых плейлистов и скачивание.

Отдельный модуль от `soundcloud.py`, хотя оба обёртки над yt-dlb: у SoundCloud своя
беда с `client_id`, у YouTube — свои (служебные каналы «Artist - Topic», плейлисты
находятся не поиском видео, а отдельным фильтром выдачи). Смешивать их в одном
адаптере значит держать в голове обе.

Тип `Track` берём из soundcloud.py — он общий контракт для сборщика видео, и второй
такой же класс развёл бы два несовместимых «трека» по коду.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

import yt_dlp

from app.logger import get_logger
from app.soundcloud import Track

# Разбор «Артист — Песня» вынесен в общий модуль: та же задача стоит и у находок
# SoundCloud, где uploader — паблик-перезаливщик, а не исполнитель.
from app.track_naming import clean_artist, clean_title, split_artist_title  # noqa: F401

_COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

# Фильтр выдачи YouTube «только плейлисты» (sp=EgIQAw%3D%3D). Обычный ytsearch ищет
# ВИДЕО, а владельцу нужны «уже готовые пользовательские плейлисты» — это разные
# сущности, и без фильтра поиск отдавал бы одиночные ролики.
_SEARCH_URL = "https://www.youtube.com/results?search_query={q}&sp=EgIQAw%3D%3D"

POT_SCRIPT_ENV = "YT_POT_SCRIPT"
DEFAULT_POT_SCRIPT = "/opt/bgutil-pot/server/build/generate_once.js"


def ytdlp_base_options() -> dict:
    """Общие опции yt-dlp для YouTube: cookies и внешний JS-движок.

    🔴 Без cookies YouTube отвечает **«Sign in to confirm you're not a bot»** на КАЖДЫЙ
    трек, и сборник падает с «Ни один трек не скачался». Ровно это остановило поток
    сборников 2026-08-11: очередь была полна (46 плейлистов), таймер тикал, а
    публикаций не было полтора суток. Проверка срабатывает на серверных IP — с
    домашнего интернета того же кода не видно.

    Путь к файлу — в `YT_COOKIES_FILE` (то же имя переменной, что у Новостей: софты
    разные, но грабля одна, и держать для неё два имени незачем). Файл машинно-
    специфичный, в git его нет и быть не должно.

    `js_runtimes` — YouTube требует решать JS-челлендж подписи; без внешнего движка
    yt-dlp его не проходит. Урок оплачен Минусами, здесь просто повторяем.

    Файла нет → работаем без cookies, как раньше: часть плейлистов всё же скачается,
    и это лучше, чем падать на старте."""
    options: dict = {"quiet": True, "no_warnings": True, "js_runtimes": {"node": {}}}

    # PO-token: без него YouTube отдаёт ответ, но в нём ОДНИ РАСКАДРОВКИ — ни одного
    # медиа-потока, и yt-dlp честно говорит «формат недоступен». Куки эту часть не
    # закрывают: они снимают только проверку «я не бот» (разобрано живыми вызовами
    # 2026-08-14 на Кино, у Музыки барьер тот же). Скрипта нет → идём как раньше.
    pot_script = os.environ.get(POT_SCRIPT_ENV, DEFAULT_POT_SCRIPT).strip()
    if pot_script and Path(pot_script).exists():
        options["extractor_args"] = {
            "youtubepot-bgutilscript": {"script_path": [pot_script]}
        }

    cookies_path = os.environ.get("YT_COOKIES_FILE", "").strip()
    if not cookies_path:
        return options
    if Path(cookies_path).exists():
        options["cookiefile"] = cookies_path
    else:
        get_logger().warning(
            "YT_COOKIES_FILE указывает на несуществующий файл: %s — идём без cookies",
            cookies_path,
        )
    return options


MAX_TRACKS_DEFAULT = 15
"""Сколько треков берём из одного плейлиста.

Тот же довод, что и у альбомов SoundCloud: тик рендерит видео на КАЖДЫЙ трек и
склеивает всё в один файл на 1-ядерном VPS. 15 треков — это ~50 минут сборника и
примерно час работы ffmpeg; больше не проходит по времени тика и по диску."""


class YouTubeSourceError(Exception):
    """Плейлист не читается или ни один трек не скачался."""


@dataclass(frozen=True)
class PlaylistRef:
    url: str
    title: str
    uploader: str


def build_source_url(source: str) -> str:
    """Строка из конфига → URL для yt-dlp.

    Ссылку оставляем как есть, произвольный текст считаем поисковым запросом по
    ПЛЕЙЛИСТАМ. Так в конфиге можно писать и то, и другое, не заводя двух списков."""
    text = source.strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return _SEARCH_URL.format(q=quote_plus(text))


def discover_playlists(source: str, limit: int = 20) -> list[PlaylistRef]:
    """Список плейлистов по ссылке-источнику или поисковому запросу.

    Источник сам может быть плейлистом (ссылка `list=`) — тогда возвращаем его одного:
    так один и тот же список в конфиге принимает и «вот конкретный плейлист», и «ищи
    по такой теме»."""
    url = build_source_url(source)
    options = {
        **ytdlp_base_options(),
        "extract_flat": True,
        "skip_download": True,
        "ignoreerrors": True,
        "playlistend": limit,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 — граница внешней библиотеки
        raise YouTubeSourceError(f"Источник {source} не прочитался: {exc}") from exc

    if not info:
        raise YouTubeSourceError(f"Источник {source} пуст")

    if info.get("_type") == "playlist" and "list=" in url:
        return [
            PlaylistRef(
                url=url,
                title=(info.get("title") or "").strip(),
                uploader=(info.get("uploader") or info.get("channel") or "").strip(),
            )
        ]

    refs: list[PlaylistRef] = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        entry_url = entry.get("url") or ""
        if "list=" not in entry_url:
            continue  # одиночный ролик из выдачи — нам нужны именно плейлисты
        refs.append(
            PlaylistRef(
                url=entry_url,
                title=(entry.get("title") or "").strip(),
                uploader=(entry.get("uploader") or entry.get("channel") or "").strip(),
            )
        )
    return refs


def download_playlist(
    url: str, target_dir: Path, max_tracks: int = MAX_TRACKS_DEFAULT
) -> list[Track]:
    """Треки плейлиста в mp3 с обложками. Порядок — как в плейлисте."""
    target_dir.mkdir(parents=True, exist_ok=True)
    options = {
        **ytdlp_base_options(),
        "format": "bestaudio/best",
        "outtmpl": str(target_dir / "%(playlist_index)03d - %(id)s.%(ext)s"),
        "writethumbnail": True,
        "ignoreerrors": True,
        "playlistend": max_tracks,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
        ],
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001 — граница внешней библиотеки
        raise YouTubeSourceError(f"Не удалось скачать плейлист: {exc}") from exc

    if not info:
        raise YouTubeSourceError("yt-dlp не вернул данные плейлиста")

    tracks = collect_tracks(info.get("entries") or [], target_dir)
    if not tracks:
        raise YouTubeSourceError("Ни один трек не скачался")

    get_logger().info("Скачано треков: %d из %s", len(tracks), url)
    return tracks


def collect_tracks(entries: list, target_dir: Path) -> list[Track]:
    """Записи yt-dlp → Track. Несошедшиеся с файлами записи пропускаются.

    Имя файла даёт yt-dlp по playlist_index — номеру среди ВСЕХ записей, включая
    упавшие. Своя нумерация позиций считает только успешные (та же грабля, что уже
    ловили на SoundCloud: сдвиг индекса ронял весь альбом)."""
    tracks: list[Track] = []
    position = 0
    for order, entry in enumerate(entries, start=1):
        if not entry:
            continue  # ignoreerrors=True даёт None на недоступных роликах
        index = entry.get("playlist_index") or order
        stem = f"{index:03d} - {entry.get('id')}"
        audio_path = target_dir / f"{stem}.mp3"
        if not audio_path.exists():
            get_logger().warning("Трек %s не скачался, пропуск", entry.get("title"))
            continue
        position += 1
        artist, name = split_artist_title(
            entry.get("title") or "",
            entry.get("artist") or entry.get("uploader") or entry.get("channel") or "",
        )
        tracks.append(
            Track(
                position=position,
                title=name,
                artist=artist,
                duration_s=int(entry.get("duration") or 0),
                audio_path=audio_path,
                cover_path=_find_cover(target_dir, stem),
            )
        )
    return tracks


def _find_cover(target_dir: Path, stem: str) -> Path | None:
    for suffix in _COVER_SUFFIXES:
        candidate = target_dir / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    return None
