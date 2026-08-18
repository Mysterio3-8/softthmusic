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
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

import yt_dlp

from app.logger import get_logger
from app.soundcloud import Track

# Разбор «Артист — Песня» вынесен в общий модуль: та же задача стоит и у находок
# SoundCloud, где uploader — паблик-перезаливщик, а не исполнитель.
from app.track_naming import clean_artist, clean_title, split_artist_title, track_key

_COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

# Фильтр выдачи YouTube «только плейлисты» (sp=EgIQAw%3D%3D). Обычный ytsearch ищет
# ВИДЕО, а владельцу нужны «уже готовые пользовательские плейлисты» — это разные
# сущности, и без фильтра поиск отдавал бы одиночные ролики.
_SEARCH_URL = "https://www.youtube.com/results?search_query={q}&sp=EgIQAw%3D%3D"

PROXY_PORTS_ENV = "YT_PROXY_PORTS"
"""Запасные выходы через запятую (`10811,10812,...`). Пусто → смены выхода нет."""

PROXY_ENV = "YT_PROXY"
"""Прокси для YouTube. Живой перебор VPN-выходов 2026-08-14 показал, что барьер
«я не бот» зависит от IP, а не от куки: на шведском выходе форматы отдаются без куки
вовсе, на остальных четырёх — барьер даже с куками. Через прокси ходит ТОЛЬКО yt-dlp:
смена страны у запросов к VK — верный способ поймать проверку безопасности."""

COOKIES_MASTER_ENV = "YT_COOKIES_MASTER"
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

    proxy = os.environ.get(PROXY_ENV, "").strip()
    if proxy:
        options["proxy"] = proxy

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

    # Эталон разворачивается ТОЛЬКО когда рабочего файла нет. yt-dlp переписывает
    # выданный ему cookiefile — и это не порча, а ротация: YouTube обновляет
    # `__Secure-1PSIDTS`, и живёт именно свежая банка. Возврат эталона перед каждым
    # вызовом откатывал бы ротацию и ронял авторизацию (проверено на Кино 2026-08-14).
    master = os.environ.get(COOKIES_MASTER_ENV, "").strip()
    if master and Path(master).exists() and not Path(cookies_path).exists():
        try:
            shutil.copyfile(master, cookies_path)
        except OSError as error:
            get_logger().warning("Не удалось восстановить cookies из эталона %s: %s", master, error)

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


MAX_TRACK_SECONDS_DEFAULT = 900
"""Потолок длительности ОДНОГО трека, 15 минут.

🔴 Оплачено простоем 14–16.08. Поиск по «chill плейлист на русском» приносит не
плейлисты песен, а подборки ЧАСОВЫХ диджей-миксов: замер живьём — 3514, 3435, 3659
секунд на «трек». Тринадцать таких = 4 ГБ аудио и почти 13 часов видео; рендер шёл
дольше двух часов, systemd убивал юнит по `TimeoutStartSec` ровно на склейке, и всё
начиналось заново каждые два часа. Ни одного сборника, круглосуточно занятое
единственное ядро — и при этом ни одной ошибки в журнале, потому что с точки зрения
кода ничего не падало.

Гейт стоит ДО скачивания (на плоском списке), поэтому непригодный плейлист стоит один
дешёвый запрос, а не четыре гигабайта трафика."""

MAX_TOTAL_SECONDS_DEFAULT = 5400
"""Потолок ВСЕГО сборника, 90 минут. Второй рубеж на случай, когда треки поодиночке
проходят: пятнадцать десятиминутных «треков» — это снова два с половиной часа видео."""

_COMPILATION_MARKERS = re.compile(
    r"топ[\s-]*\d+|top[\s-]*\d+|сборник|подборка|лучшие\s+песни|лучшие\s+треки|"
    r"хиты|hits|плейлист|playlist|подряд|нон[\s-]?стоп|non[\s-]?stop|"
    r"best\s+of|mix|микс|музыка\s+\d{4}\s*[-–—]|радио\s+\w+",
    re.IGNORECASE,
)
"""Признаки того, что «трек» на самом деле ЧУЖОЙ ГОТОВЫЙ СБОРНИК.

🔴 Жалоба владельца 2026-08-17: «ты взял чужой сборник уже готовый». Софт опубликовал
«ТОП-9: ТОП 30 ЛУЧШИХ ПЕСЕН РАДИО ENERGY, …» — плейлист-донор состоял не из песен, а из
чужих компиляций, и каждая пошла в наш сборник как «трек». Получилась компиляция
компиляций: чужой труд целиком, к тому же с ломаными таймингами.

Гейта по длительности мало: часть таких роликов короче 15 минут и проходит его насквозь.
Ловим по названию — именно в нём это и написано открытым текстом.

⚠️ Ложное срабатывание тут дёшево (потеряли одну песню из подборки), а пропуск дорог:
чужой сборник целиком уезжает в сообщество под нашим именем."""

MIN_TRACKS_DEFAULT = 5
"""Меньше — не сборник. Плейлист, из которого после отсева осталось три песни, лучше
пропустить целиком, чем публиковать огрызок."""


class YouTubeSourceError(Exception):
    """Плейлист не читается или ни один трек не скачался."""


class PlaylistUnsuitable(YouTubeSourceError):
    """Плейлист прочитался, но по составу не годится (часовые миксы, слишком мало песен).

    Отдельный тип, потому что это НЕ временный сбой: повторять такой плейлист через сутки
    бессмысленно, состав у него не изменится. `revive_failed` возвращает в очередь именно
    временные падения, и без этого разделения отбракованные миксы возвращались бы вечно."""


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


@dataclass(frozen=True)
class PlaylistEntry:
    """Запись плоского списка плейлиста: позиция, название, длительность."""

    index: int
    title: str
    duration_s: int


def list_playlist_entries(url: str, limit: int) -> list[PlaylistEntry]:
    """Состав плейлиста БЕЗ скачивания — один дешёвый запрос.

    Плоское извлечение отдаёт длительность у YouTube всегда (проверено живьём
    2026-08-16), и это единственный способ узнать состав до того, как на диск приедут
    гигабайты."""
    options = {
        **ytdlp_base_options(),
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
        "playlistend": limit,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 — граница внешней библиотеки
        raise YouTubeSourceError(f"Плейлист {url} не прочитался: {exc}") from exc
    if not info:
        raise YouTubeSourceError(f"Плейлист {url} пуст")

    entries = []
    for order, entry in enumerate(info.get("entries") or [], start=1):
        if not entry:
            continue  # ignoreerrors=True даёт None на недоступных роликах
        entries.append(
            PlaylistEntry(
                index=entry.get("playlist_index") or order,
                title=(entry.get("title") or "").strip(),
                duration_s=int(entry.get("duration") or 0),
            )
        )
    return entries


def select_entries(
    entries: list[PlaylistEntry],
    max_tracks: int = MAX_TRACKS_DEFAULT,
    max_track_seconds: int = MAX_TRACK_SECONDS_DEFAULT,
    max_total_seconds: int = MAX_TOTAL_SECONDS_DEFAULT,
    skip_keys: set[str] | None = None,
) -> list[PlaylistEntry]:
    """Что из плейлиста реально берём в сборник. Чистая функция — тестируется без сети.

    Записи без длительности пропускаются: у YouTube это либо недоступный ролик, либо
    трансляция, и «пропустить» дешевле, чем скачать неизвестно что на единственное ядро."""
    chosen: list[PlaylistEntry] = []
    seen = set(skip_keys or ())
    total = 0
    for entry in entries:
        if len(chosen) >= max_tracks:
            break
        if entry.duration_s <= 0 or entry.duration_s > max_track_seconds:
            continue
        # Песня уже выходила в прошлых сборниках (или повторяется внутри этого) —
        # пропускаем ДО скачивания, иначе платим трафиком за то, что всё равно выбросим.
        # Чужой готовый сборник — не трек. Проверяем СЫРОЕ название: чистка хвостов
        # уже успела бы срезать «| ЛУЧШИЕ ПЕСНИ 2020» и спрятать улику.
        if _COMPILATION_MARKERS.search(entry.title):
            continue
        artist, name = split_artist_title(entry.title, "")
        key = track_key(artist, name)
        if key in seen:
            continue
        seen.add(key)
        if total + entry.duration_s > max_total_seconds:
            continue  # длинный трек в конце не должен закрывать дорогу коротким
        chosen.append(entry)
        total += entry.duration_s
    return chosen


def download_playlist(
    url: str,
    target_dir: Path,
    max_tracks: int = MAX_TRACKS_DEFAULT,
    max_track_seconds: int = MAX_TRACK_SECONDS_DEFAULT,
    max_total_seconds: int = MAX_TOTAL_SECONDS_DEFAULT,
    min_tracks: int = MIN_TRACKS_DEFAULT,
    skip_keys: set[str] | None = None,
) -> list[Track]:
    """Треки плейлиста в mp3 с обложками. Порядок — как в плейлисте.

    Состав сначала проверяется по плоскому списку, и только годные позиции уходят в
    скачивание (`playlist_items`). Из-за этого непригодный плейлист стоит один запрос."""
    target_dir.mkdir(parents=True, exist_ok=True)

    entries = list_playlist_entries(url, max(max_tracks * 4, 40))
    chosen = select_entries(
        entries, max_tracks, max_track_seconds, max_total_seconds, skip_keys
    )
    if len(chosen) < min_tracks:
        longest = max((entry.duration_s for entry in entries), default=0)
        raise PlaylistUnsuitable(
            f"Годных треков {len(chosen)} из {len(entries)} при минимуме {min_tracks}: "
            f"длиннее {max_track_seconds} с не берём (самый длинный тут {longest} с), "
            f"уже выходивших песен в памяти {len(skip_keys or ())}"
        )
    get_logger().info(
        "Плейлист %s: беру %d из %d записей (%d мин)",
        url, len(chosen), len(entries), sum(e.duration_s for e in chosen) // 60,
    )

    options = {
        **ytdlp_base_options(),
        "format": "bestaudio/best",
        "outtmpl": str(target_dir / "%(playlist_index)03d - %(id)s.%(ext)s"),
        "writethumbnail": True,
        "ignoreerrors": True,
        "playlist_items": ",".join(str(entry.index) for entry in chosen),
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
        ],
    }
    last_error = ""
    for attempt, proxy in enumerate(proxy_candidates(), start=1):
        if proxy:
            options["proxy"] = proxy
        else:
            options.pop("proxy", None)
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as exc:  # noqa: BLE001 — граница внешней библиотеки
            last_error = str(exc)
            info = None

        tracks = collect_tracks((info or {}).get("entries") or [], target_dir)
        if tracks:
            get_logger().info(
                "Скачано треков: %d из %s (выход %s)", len(tracks), url, proxy or "прямой"
            )
            return tracks
        get_logger().warning(
            "Выход %s не отдал ни одного трека (попытка %d) — меняю выход",
            proxy or "прямой", attempt,
        )

    raise YouTubeSourceError(f"Ни один трек не скачался ни через один выход. {last_error}"[:400])


def proxy_candidates() -> list[str | None]:
    """Выходы прокси в порядке попыток. Пустая строка в конце = прямой путь.

    🔴 Смена выхода нужна именно на СКАЧИВАНИИ, а не на метаданных. Живая проверка
    2026-08-16: шведский выход отдавал 5 аудиоформатов, то есть барьер «я не бот» на нём
    снят, — и при этом каждый трек падал с `403 Forbidden` на самих данных. CDN отвечает
    отдельно от плеера, и выход, прошедший проверку, всё равно может не отдать файл;
    похоже, срабатывает на всплеск (первые 15 треков скачались, следующая попытка через
    полчаса — ноль). Проверять выход запросом метаданных бессмысленно: он ответит «жив».

    Порядок: сначала основной `YT_PROXY`, затем остальные из `YT_PROXY_PORTS`, затем
    прямой путь — он иногда проходит, а «не пробовать вовсе» гарантирует сутки без
    сборника. Переменных нет → одна попытка тем, что задано (прежнее поведение)."""
    primary = os.environ.get(PROXY_ENV, "").strip() or None
    raw = os.environ.get(PROXY_PORTS_ENV, "").strip()
    if not raw:
        return [primary]

    candidates: list[str | None] = [primary] if primary else []
    for port in (part.strip() for part in raw.split(",")):
        if not port:
            continue
        url = f"socks5://127.0.0.1:{port}"
        if url not in candidates:
            candidates.append(url)
    candidates.append(None)
    return candidates


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
