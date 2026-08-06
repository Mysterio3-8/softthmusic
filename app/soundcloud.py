"""Граница SoundCloud (yt-dlp): плейлист -> метаданные, треки, обложки."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from app.logger import get_logger

# Обложка yt-dlp кладёт рядом с аудио под тем же stem — расширение заранее не известно.
_COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


class SoundCloudError(Exception):
    """Ошибка чтения плейлиста или скачивания трека."""


@dataclass
class PlaylistMeta:
    """Быстрая сводка по ссылке — чтобы бот сразу ответил, что именно принял."""

    title: str
    artist: str
    track_count: int


@dataclass
class Track:
    position: int
    title: str
    artist: str
    duration_s: int
    audio_path: Path
    cover_path: Path | None


_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_ASSET_PREFIX = "https://a-v2.sndcdn.com/assets/"
_CLIENT_ID_MIN_LEN = 30

MAX_TRACKS_PER_ALBUM = 30
"""Сколько треков берём с одной ссылки.

Ссылка вида `/tracks` у плодовитого артиста отдаёт сотни треков (у kizaru — 358), а тик
качает плейлист ЦЕЛИКОМ, рендерит видео на каждый трек и склеивает всё в один сборник.
На этом VPS (1 ядро, 961 МБ RAM, ~8 ГБ свободно) это не проходит ни по диску, ни по
времени, а сборник из 358 треков — это ~20 часов видео, которые никто не смотрит.
Потолок режет ссылку до размера обычного альбома; хотите продолжение — ставьте ту же
ссылку в очередь повторно."""


def _scrape_client_id(page_url: str) -> str | None:
    """Достать client_id из JS-бандлов страницы SoundCloud.

    Ключ лежит в скриптах, на которые ссылается ЛЮБАЯ страница артиста или трека, —
    а они с этого сервера открываются нормально (200), в отличие от главной."""
    import requests

    headers = {"User-Agent": _BROWSER_UA}
    try:
        page = requests.get(page_url, headers=headers, timeout=30).text
    except Exception as exc:  # noqa: BLE001 — граница сети
        get_logger().warning("client_id: страница %s недоступна: %s", page_url, exc)
        return None

    assets = []
    for chunk in page.split(_ASSET_PREFIX)[1:]:
        end = chunk.find(".js")
        if end > 0:
            assets.append(_ASSET_PREFIX + chunk[: end + 3])

    # Ключ обычно в последних бандлах — идём с конца, чтобы не качать лишнее.
    for asset_url in reversed(assets):
        try:
            script = requests.get(asset_url, headers=headers, timeout=30).text
        except Exception:  # noqa: BLE001 — один битый бандл не повод сдаваться
            continue
        marker = 'client_id:"'
        start = script.find(marker)
        while start != -1:
            value_start = start + len(marker)
            value_end = script.find('"', value_start)
            candidate = script[value_start:value_end]
            if len(candidate) >= _CLIENT_ID_MIN_LEN:
                return candidate
            start = script.find(marker, start + 1)
    return None


def ensure_client_id(sample_url: str) -> None:
    """Положить рабочий client_id в кеш yt-dlp, если его там нет.

    Зачем это вообще: yt-dlp добывает client_id только с главной `soundcloud.com/`,
    а она отдаёт этому серверу **403** (страницы треков при этом открываются, 200 —
    то есть IP не в бане, блокируется именно главная). Без ключа падает КАЖДЫЙ трек,
    и альбом целиком помечается «ни один трек не скачался» — ровно эта поломка
    остановила публикации 2026-08-05.

    Самолечение бесплатно: получив 401/403, yt-dlp сам обнуляет ключ в кеше, поэтому
    следующий вызов увидит пустой кеш и добудет свежий."""
    ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
    if ydl.cache.load("soundcloud", "client_id"):
        return
    client_id = _scrape_client_id(sample_url)
    if not client_id:
        get_logger().warning("client_id: не удалось добыть — скачивание, вероятно, упадёт")
        return
    ydl.cache.store("soundcloud", "client_id", client_id)
    get_logger().info("client_id обновлён (%s...)", client_id[:6])


def fetch_playlist_meta(url: str) -> PlaylistMeta:
    """Название, автор и число треков без скачивания. Дёргается ботом на enqueue."""
    ensure_client_id(url)
    options = {
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "playlistend": MAX_TRACKS_PER_ALBUM,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 — граница внешней библиотеки
        raise SoundCloudError(f"Не удалось прочитать плейлист: {exc}") from exc

    if not info:
        raise SoundCloudError("Плейлист пуст или недоступен")

    entries = [e for e in (info.get("entries") or []) if e]
    if not entries:
        raise SoundCloudError("В плейлисте нет треков (возможно, он приватный)")

    return PlaylistMeta(
        title=(info.get("title") or "").strip(),
        artist=(info.get("uploader") or info.get("channel") or "").strip(),
        track_count=len(entries),
    )


def download_playlist(url: str, target_dir: Path) -> list[Track]:
    """Скачивает все треки плейлиста в mp3 с обложками. Порядок — как в плейлисте."""
    ensure_client_id(url)
    target_dir.mkdir(parents=True, exist_ok=True)
    options = {
        "format": "bestaudio/best",
        "outtmpl": str(target_dir / "%(playlist_index)03d - %(id)s.%(ext)s"),
        "writethumbnail": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "playlistend": MAX_TRACKS_PER_ALBUM,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
        ],
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001 — граница внешней библиотеки
        raise SoundCloudError(f"Не удалось скачать плейлист: {exc}") from exc

    if not info:
        raise SoundCloudError("yt-dlp не вернул данные плейлиста")

    tracks = _collect_tracks(info.get("entries") or [], target_dir)
    if not tracks:
        raise SoundCloudError("Ни один трек не скачался")

    get_logger().info("Скачано треков: %d из %s", len(tracks), url)
    return tracks


def _collect_tracks(entries: list, target_dir: Path) -> list[Track]:
    """Сопоставляет записи yt-dlp со скачанными файлами. Несошедшиеся — пропускает."""
    tracks: list[Track] = []
    position = 0
    for order, entry in enumerate(entries, start=1):
        if not entry:
            continue  # ignoreerrors=True даёт None на упавших треках
        position += 1
        # Имя файла даёт yt-dlp по playlist_index — это номер среди ВСЕХ записей, включая
        # упавшие. Локальный счётчик считает только успешные, поэтому любой пропущенный
        # трек (а DRM-треки их создают регулярно) сдвигал нумерацию, и дальше не находился
        # НИ ОДИН файл: альбом падал с «Ни один трек не скачался» при реально скачанных mp3.
        index = entry.get("playlist_index") or order
        stem = f"{index:03d} - {entry.get('id')}"
        audio_path = target_dir / f"{stem}.mp3"
        if not audio_path.exists():
            get_logger().warning("Трек %s не скачался, пропуск", entry.get("title"))
            continue
        tracks.append(
            Track(
                position=position,
                title=(entry.get("title") or "").strip(),
                artist=(entry.get("uploader") or entry.get("artist") or "").strip(),
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


def covers_are_identical(cover_paths: list[Path]) -> bool:
    """True — у всех треков одна и та же обложка (сравнение по содержимому).

    Плейлист-альбом обычно отдаёт одинаковый арт на каждый трек; сборник — разный.
    От этого зависит, будет компиляция статичной картинкой или слайд-шоу.
    """
    if len(cover_paths) < 2:
        return True
    digests = {_file_digest(path) for path in cover_paths}
    return len(digests) == 1


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
