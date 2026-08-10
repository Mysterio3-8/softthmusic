"""Граница YouTube / YouTube Music (yt-dlp): поиск готовых плейлистов и скачивание.

Отдельный модуль от `soundcloud.py`, хотя оба обёртки над yt-dlb: у SoundCloud своя
беда с `client_id`, у YouTube — свои (служебные каналы «Artist - Topic», плейлисты
находятся не поиском видео, а отдельным фильтром выдачи). Смешивать их в одном
адаптере значит держать в голове обе.

Тип `Track` берём из soundcloud.py — он общий контракт для сборщика видео, и второй
такой же класс развёл бы два несовместимых «трека» по коду.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

import yt_dlp

from app.logger import get_logger
from app.soundcloud import Track

_COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

# Фильтр выдачи YouTube «только плейлисты» (sp=EgIQAw%3D%3D). Обычный ytsearch ищет
# ВИДЕО, а владельцу нужны «уже готовые пользовательские плейлисты» — это разные
# сущности, и без фильтра поиск отдавал бы одиночные ролики.
_SEARCH_URL = "https://www.youtube.com/results?search_query={q}&sp=EgIQAw%3D%3D"

# «Big Baby Tape - Topic» — служебный канал автогенерённых аудиодорожек YouTube Music.
# В подпись такое имя ставить нельзя, а исполнитель в нём как раз правильный.
_TOPIC_SUFFIX = re.compile(r"\s*-\s*Topic\s*$", re.IGNORECASE)
# Хвосты в названии ролика, которые в подписи и в теге только мешают.
_TITLE_NOISE = re.compile(
    r"\s*[\(\[](?:[^)\]]*)(?:official|lyric|audio|video|hd|4k|remaster\w*)[^)\]]*[\)\]]",
    re.IGNORECASE,
)

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
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
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


def clean_artist(raw: str) -> str:
    """«Big Baby Tape - Topic» → «Big Baby Tape»."""
    return _TOPIC_SUFFIX.sub("", (raw or "").strip()).strip()


def clean_title(raw: str) -> str:
    """«Песня (Official Video)» → «Песня». Скобки без служебных слов не трогаем."""
    return " ".join(_TITLE_NOISE.sub("", (raw or "").strip()).split())


def split_artist_title(title: str, uploader: str) -> tuple[str, str]:
    """Достаёт исполнителя и название из «Артист — Песня».

    У пользовательских плейлистов исполнитель почти всегда зашит в НАЗВАНИЕ ролика,
    а канал-загрузчик — это чужой паблик, а не автор трека. Разделителя нет — берём
    исполнителя из канала (для «- Topic» это верный ответ)."""
    cleaned = clean_title(title)
    for separator in (" — ", " – ", " - ", " | "):
        artist, found, name = cleaned.partition(separator)
        if found and artist.strip() and name.strip():
            return artist.strip(), name.strip()
    return clean_artist(uploader), cleaned


def download_playlist(
    url: str, target_dir: Path, max_tracks: int = MAX_TRACKS_DEFAULT
) -> list[Track]:
    """Треки плейлиста в mp3 с обложками. Порядок — как в плейлисте."""
    target_dir.mkdir(parents=True, exist_ok=True)
    options = {
        "format": "bestaudio/best",
        "outtmpl": str(target_dir / "%(playlist_index)03d - %(id)s.%(ext)s"),
        "writethumbnail": True,
        "quiet": True,
        "no_warnings": True,
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
