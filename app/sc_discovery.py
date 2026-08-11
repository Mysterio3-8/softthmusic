"""Поиск ПОПУЛЯРНЫХ треков на SoundCloud (граница yt-dlp).

ТЗ владельца 2026-08-10: «хочу чтобы софт брал популярные треки и плейлисты не просто
те которые я дал, а популярные и желательно русские треки с ск и ютуба».

До этого альбомный поток публиковал ровно то, что владелец кинул руками
(`soundcloud_cli enqueue <url>`). Кончились ссылки — очередь пуста, и в сообществе
оставались только сборники с YouTube. Здесь очередь наполняется сама.

**Чем ищем и почему именно так.** Проверено живыми вызовами 2026-08-11:

* `scsearch<N>:<запрос>` работает и отдаёт `view_count`/`like_count` уже во flat-режиме —
  то есть популярность видна ДО скачивания, за один дешёвый запрос;
* официальные чарты SoundCloud (`/discover/sets/charts-top:all-music:ru`) в этой версии
  yt-dlp **не читаются** — `soundcloud:set` отдаёт на них HTTP 404. Поэтому «популярное»
  собирается не чартом, а поиском с отсевом по числу прослушиваний;
* ссылка вида `https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A<id>` (именно её
  отдаёт поиск) скачивается штатно — отдельно резолвить страницу трека не нужно.

Порядок выдачи SoundCloud — релевантность, а не популярность: в одном ответе рядом
стоят трек на 12 млн прослушиваний и трек на 748. Поэтому сортировка и порог здесь
обязательны, иначе «популярные треки» превратились бы в случайные.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import yt_dlp

from app.logger import get_logger
from app.track_naming import split_artist_title

_CYRILLIC = re.compile(r"[А-Яа-яЁё]")

DEFAULT_MIN_PLAYS = 100_000
"""Порог популярности. Ниже этого трек считается случайной находкой выдачи.

Живой замер по запросу «русский рэп 2026»: из восьми первых результатов четыре имели
меньше 2000 прослушиваний (загрузки обычных пользователей), а настоящие хиты —
от 100 тысяч до 12 миллионов. Порог отсекает первую группу и не трогает вторую."""


class DiscoveryError(Exception):
    """Источник не прочитался. Не поломка потока: остальные источники ещё есть."""


@dataclass(frozen=True)
class TrackRef:
    """Найденный трек до скачивания: ссылка и то, по чему его отбирают."""

    url: str
    title: str
    artist: str
    plays: int
    russian: bool


def build_source_url(source: str, limit: int) -> str:
    """Строка из конфига → URL для yt-dlp.

    Ссылку берём как есть (можно указать конкретный плейлист или профиль артиста),
    произвольный текст считаем поисковым запросом. Так один список в конфиге принимает
    и «вот этот плейлист», и «ищи популярное по такой теме» — как у сборников YouTube."""
    text = source.strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return f"scsearch{max(1, limit)}:{text}"


def discover_tracks(
    source: str, limit: int = 40, min_plays: int = DEFAULT_MIN_PLAYS
) -> list[TrackRef]:
    """Популярные треки по одному источнику, от самых востребованных к остальным."""
    url = build_source_url(source, limit)
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
        raise DiscoveryError(f"Источник {source} не прочитался: {exc}") from exc

    if not info:
        raise DiscoveryError(f"Источник {source} пуст")

    refs = [ref for ref in map(_to_ref, info.get("entries") or []) if ref is not None]
    return rank_tracks([ref for ref in refs if ref.plays >= min_plays])


def rank_tracks(refs: list[TrackRef]) -> list[TrackRef]:
    """Русские вперёд, внутри — по числу прослушиваний.

    «Желательно русские» из ТЗ — это предпочтение, а не фильтр: запрос на русском всё
    равно приносит зарубежные треки, и выбрасывать хит на 10 млн прослушиваний только
    из-за латиницы в названии было бы хуже, чем поставить его вторым."""
    return sorted(refs, key=lambda ref: (not ref.russian, -ref.plays))


def _to_ref(entry: dict | None) -> TrackRef | None:
    """Запись выдачи → TrackRef. Плейлисты и записи без ссылки пропускаем.

    Исполнитель разбирается из НАЗВАНИЯ: у популярных треков `uploader` — это паблик-
    перезаливщик («Русский Рэп», «RuRap»), а не автор. Без разбора один и тот же трек
    из двух пабликов давал бы два разных ключа дедупа и попадал в очередь дважды."""
    if not entry:
        return None  # ignoreerrors=True даёт None на недоступных треках
    url = (entry.get("url") or entry.get("webpage_url") or "").strip()
    if not url:
        return None
    artist, title = split_artist_title(
        entry.get("title") or "", entry.get("uploader") or entry.get("artist") or ""
    )
    return TrackRef(
        url=url,
        title=title,
        artist=artist,
        plays=int(entry.get("view_count") or 0),
        russian=bool(_CYRILLIC.search(f"{title} {artist}")),
    )


def collect_new_tracks(
    sources: list[str],
    known_urls: set[str],
    known_names: set[str] | None = None,
    *,
    wanted: int,
    limit_per_source: int = 40,
    min_plays: int = DEFAULT_MIN_PLAYS,
) -> list[TrackRef]:
    """Обходит источники по кругу и набирает `wanted` ещё не встречавшихся треков.

    Сломанный источник не рушит набор: у поисковой выдачи состав меняется, и один
    упавший запрос не повод оставить очередь пустой (тот же довод, что в `yt_playlists`).

    Дедуп идёт по ссылке И по паре «исполнитель — название» (`known_names`): один и тот
    же хит лежит на SoundCloud десятками перезаливов с разными id, и без второго ключа
    в очередь набивалось бы пять копий одного трека."""
    log = get_logger()
    found: list[TrackRef] = []
    seen = set(known_urls)
    seen_names = {_name_key(name) for name in (known_names or set())}
    for source in sources:
        if len(found) >= wanted:
            break
        try:
            refs = discover_tracks(source, limit_per_source, min_plays)
        except DiscoveryError as exc:
            log.warning("Поиск треков: %s", exc)
            continue
        fresh = 0
        for ref in refs:
            if len(found) >= wanted:
                break
            name_key = _name_key(f"{ref.artist} {ref.title}")
            if ref.url in seen or (name_key and name_key in seen_names):
                continue
            seen.add(ref.url)
            seen_names.add(name_key)
            found.append(ref)
            fresh += 1
        log.info("Источник «%s»: подходящих %d, новых %d", source, len(refs), fresh)
    return found


def _name_key(value: str) -> str:
    """«TARAS - Тебя Нежно Грубо» → «tarasтебянежногрубо». Ключ дедупа перезаливов."""
    return re.sub(r"[^\w]", "", value.lower().replace("ё", "е"))
