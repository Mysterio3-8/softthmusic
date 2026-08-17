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
from app.seo import build_hashtags, build_search_tags
from app.soundcloud import Track
from app.thumbnail import build_thumbnail
from app.track_naming import split_artists, track_key
from app.tg_uploader import TelegramUploader
from app.vk_client import VKClient, VKError, VKTokenBusy
from app.workdir_cleanup import cleanup_stale_workdirs
from app.yt_playlist_db import POST_KIND_YT_PLAYLIST, PlaylistQueue, PlaylistRow
from app.sc_compilation import SC_URL_PREFIX, collect_tracks as collect_sc_tracks, is_sc_source
from app.yt_source import (
    PlaylistUnsuitable,
    YouTubeSourceError,
    discover_playlists,
    download_playlist,
)


@dataclass(frozen=True)
class Compilation:
    """Готовый сборник: файл + все тексты вокруг него."""

    video_path: Path
    title: str
    post_text: str
    description: str
    tracks: list[Track]
    delivery_path: Path | None = None
    """Файл для отдачи владельцу в Telegram. ТЗ 2026-08-16: «если в плейлисте много
    треков, фулл плейлист можно в ВК, а в ТГ обрезанный».

    Пусто → отдаём тот же файл, что и в ВК. Короткая версия склеивается из ТЕХ ЖЕ
    сегментов, что и полная, потоковым копированием — второй рендер не нужен, и цена
    вопроса секунды."""
    delivery_tracks: int = 0
    """Сколько треков попало в короткую версию. 0 — версия полная."""
    thumbnail_path: Path | None = None
    """Обложка 1280×720 для YouTube (ТЗ 2026-08-17). None — Pillow или шрифт недоступны;
    сборник без превью хуже, чем с превью, но несравнимо лучше, чем его отсутствие."""
    delivery_description: str = ""
    """Описание ИМЕННО отданного файла. ТЗ владельца 2026-08-16.

    🔴 Без него в Telegram уходил треклист полного сборника — пятнадцать позиций с
    таймингами, тогда как в самом файле их восемь. Владелец заливает этот файл на YouTube
    вместе с описанием, то есть половина таймингов вела бы в пустоту, а последних семи
    треков в ролике не было бы вовсе."""

    @property
    def file_for_owner(self) -> Path:
        return self.delivery_path or self.video_path

    @property
    def description_for_owner(self) -> str:
        return self.delivery_description or self.description


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
    if _daily_limit_reached(posts, settings.max_posts_per_day, now, settings):
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

    # Отбраковка по составу стоит один плоский запрос, поэтому в тике их можно сделать
    # несколько подряд: очередь набита подборками часовых миксов (18 штук на 16.08), и
    # по одной за тик софт разбирал бы их четыре с половиной часа, не выпустив сборника.
    # Потолок нужен, чтобы тик оставался коротким: юнит — oneshot под таймером.
    for _ in range(MAX_REJECTS_PER_TICK):
        playlist = playlists.next_pending() or _own_compilation(config, playlists, now)
        if playlist is None:
            return "очередь сборников пуста"
        result, rejected = _process(config, playlists, posts, vk, notifier, playlist, now)
        if not rejected:
            return result
    return f"подряд отбраковано {MAX_REJECTS_PER_TICK} плейлистов — состав не подходит"


MAX_REJECTS_PER_TICK = 5


def _own_compilation(config: Config, playlists: PlaylistQueue, now: datetime):
    """Своя подборка с SoundCloud, когда чужих плейлистов не осталось.

    ТЗ владельца 2026-08-16: «можешь с sc плейлисты брать или свои создавать софт будет,
    если на ютубе проблемы». Это и есть ответ на все три простоя подряд: поток сборников
    больше не зависит от одного внешнего источника, который то закрывается барьером, то
    отдаёт часовые миксы вместо песен.

    Запись кладём в ТУ ЖЕ очередь: тогда «отдан ли файл», «что уже публиковали» и защита
    от повторов названий работают как у обычного плейлиста, без второго учёта.

    ⚠️ Отбраковка сюда НЕ проваливается: если своя подборка не собралась (SoundCloud
    молчит), она станет `rejected`, и следующий виток цикла попробует создать новую.
    Потолок `MAX_REJECTS_PER_TICK` этот цикл и ограничивает."""
    discovery = config.soundcloud.discovery
    if not config.youtube_playlists.fallback_soundcloud or not discovery.sources:
        return None
    url = f"{SC_URL_PREFIX}{now.strftime('%Y%m%d%H%M%S')}"
    playlists.add(url, "Своя подборка SoundCloud", "SoundCloud", "автосборник")
    get_logger().info("Чужих плейлистов нет — собираю свою подборку с SoundCloud")
    return playlists.next_pending()


def window_start(now: datetime, quiet_start_hour: int, quiet_end_hour: int) -> datetime:
    """Когда открылось ТЕКУЩЕЕ рабочее окно софта (в МСК).

    Рабочее окно — это `[quiet_end, quiet_start)`: тишина задаётся конфигом, работа — то,
    что осталось. Окно на все сутки (тишины нет) → начало суток."""
    if quiet_start_hour == quiet_end_hour:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    opened = now.replace(hour=quiet_end_hour % 24, minute=0, second=0, microsecond=0)
    if opened > now:
        opened -= timedelta(days=1)
    return opened


def _daily_limit_reached(
    posts: AlbumQueue, max_posts_per_day: int, now: datetime, settings=None
) -> bool:
    """Исчерпан ли лимит В ТЕКУЩЕМ ОКНЕ.

    🔴 Считалось скользящими сутками, и при девятичасовом окне это давало НЕ два сборника
    в сутки, а один. Арифметика: первый выходит в 00:00 МСК, второй в 03:45; следующей
    ночью в 00:00 оба ещё внутри 24 часов, счётчик показывает 2 — и ночь пропускается
    целиком. Публикации вдобавок уползали всё позже и рисковали вывалиться из окна.

    Считаем от ОТКРЫТИЯ окна: каждую ночь квота начинается заново, дрейфа нет.
    Софт без окна (тишина не задана) получает ровно прежнее поведение — сутки целиком."""
    kinds = (POST_KIND_YT_PLAYLIST,)
    if settings is None:
        since = now - timedelta(days=1)
    else:
        since = window_start(now, settings.quiet_start_hour, settings.quiet_end_hour)
    return posts.posts_since(since, kinds) >= max_posts_per_day


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
) -> tuple[str, bool]:
    """Один плейлист. Второй элемент — «отбракован по составу», можно брать следующий."""
    settings = config.youtube_playlists
    log = get_logger()
    work_dir = settings.work_dir / f"pl_{playlist.id}"

    try:
        compilation = build_compilation(config, playlists, playlist, work_dir, now)

        # Файл уходит владельцу ДО публикации в VK (ТЗ 2026-08-10: «пускай сборники
        # приходят сразу заранее, не после публикации»). Так он получает сборник даже
        # если VK откажет: там дальше и занятый токен пула, и любая ошибка API.
        delivered_path = _deliver(config, playlists, playlist, compilation, notifier)
        # Отдача ПЕРЕНОСИТ файл в ready/. Когда в Telegram ушла короткая версия, полный
        # ролик остался на месте и грузим в ВК именно его; когда версия была одна —
        # исходного пути больше нет, и грузить надо с нового.
        vk_path = (
            compilation.video_path if compilation.video_path.exists() else delivered_path
        )

        attachment = vk.upload_video(vk_path, compilation.title, compilation.description)
        post_id = vk.post_now(compilation.post_text, attachment)
        posts.log_post(POST_KIND_YT_PLAYLIST)
        playlists.mark_published(
            playlist.id,
            f"https://vk.com/wall-{config.group_id}_{post_id}",
            published_title=compilation.title,
        )
        # Запоминаем ПЕСНИ, а не только плейлист: очередь набита разными ссылками с почти
        # одинаковым содержимым, и без этой памяти следующий сборник повторял предыдущий
        # (жалоба владельца 2026-08-17 «почему в сборниках треки одинаковые»).
        playlists.remember_tracks(
            [track_key(track.artist, track.title) for track in compilation.tracks]
        )
        return (
            f"опубликован сборник «{compilation.title}» ({len(compilation.tracks)} треков)",
            False,
        )
    except PlaylistUnsuitable as exc:
        # Состав плейлиста от повторов не изменится — убираем насовсем и в том же тике
        # берём следующий. Попытки тут были бы холостыми запросами к YouTube.
        playlists.reject(playlist.id, str(exc))
        log.info("Плейлист %s отбракован: %s", playlist.url, exc)
        return f"плейлист отбракован: {exc}", True
    except VKTokenBusy as exc:
        # НЕ поломка: свободного токена нет прямо сейчас. Попытку не тратим и файлы не
        # выбрасываем — плейлист остаётся в очереди и уйдёт следующим тиком.
        log.warning("Сборник %s отложен: %s", playlist.url, exc)
        return "сборник отложен (нет свободного токена)", False
    except (YouTubeSourceError, MediaError, VKError) as exc:
        attempts = playlists.bump_attempt(playlist.id, settings.max_attempts, str(exc))
        log.error("Сборник %s (попытка %d): %s", playlist.url, attempts, exc)
        return f"ошибка сборника, попытка {attempts}", False
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        cleanup_ready(settings.ready_dir, settings.ready_keep_days)
        # Каталоги сборников, брошенных убитым процессом (OOM во время рендера), свой
        # `finally` не отработали и лежат мёртвым грузом по сотне мегабайт каждый.
        cleanup_stale_workdirs(settings.work_dir, keep=work_dir)


def build_compilation(
    config: Config,
    playlists: PlaylistQueue,
    playlist: PlaylistRow,
    work_dir: Path,
    now: datetime,
) -> Compilation:
    """Скачать треки, собрать видео и все тексты. Без сети VK — тестируется отдельно."""
    settings = config.youtube_playlists
    if is_sc_source(playlist.url):
        # Своя подборка с SoundCloud: донора нет, треки набираются поиском.
        tracks = collect_sc_tracks(
            config.soundcloud.discovery.sources,
            work_dir,
            wanted=settings.max_tracks,
            min_tracks=settings.min_tracks,
            max_track_seconds=settings.max_track_seconds,
            min_plays=config.soundcloud.discovery.min_plays,
        )
        if not tracks:
            raise PlaylistUnsuitable("SoundCloud не отдал достаточно треков для сборника")
    else:
        tracks = download_playlist(
            playlist.url,
            work_dir,
            settings.max_tracks,
            max_track_seconds=settings.max_track_seconds,
            max_total_seconds=settings.max_total_seconds,
            min_tracks=settings.min_tracks,
            skip_keys=playlists.recent_track_keys(),
        )
    _ensure_own_covers(tracks)

    artists = playlist_artists(tracks)
    template = choose_title_template(
        settings.title_templates, playlists.recent_titles(20), now, artists, len(tracks)
    )
    title = render_title(template, now, artists, len(tracks))
    video_path, delivery_path = _render(tracks, work_dir, settings.tg_max_tracks)
    tracklist = _tracklist_of(tracks)
    delivery_description = ""
    if delivery_path is not None:
        # Треклист пересобирается по первым N трекам, а не режется по строкам: тайминги
        # считаются из длительностей, и обрезка готового текста оставила бы верные
        # подписи при неверном хронометраже.
        short_tracks = tracks[: settings.tg_max_tracks]
        # Название то же, но с числом треков ОТДАННОГО файла: «ТОП-15» на файле из
        # восьми песен — то же расхождение, из-за которого поехали тайминги.
        short_title = render_title(
            template, now, playlist_artists(short_tracks), len(short_tracks)
        )
        delivery_description = build_description(
            config, short_title, _tracklist_of(short_tracks), short_tracks
        )

    thumbnail = build_thumbnail(
        work_dir / "thumbnail.jpg",
        count=len(tracks),
        month_index=now.month,
        year=now.year,
        artists=artists,
        cover_path=tracks[0].cover_path if tracks else None,
    )

    return Compilation(
        video_path=video_path,
        thumbnail_path=thumbnail,
        title=title,
        post_text=build_post_text(config, title, tracks, tracklist),
        description=build_description(config, title, tracklist, tracks),
        tracks=tracks,
        delivery_path=delivery_path,
        delivery_tracks=settings.tg_max_tracks if delivery_path else 0,
        delivery_description=delivery_description,
    )


def build_title(
    templates: list[str],
    recent: list[str],
    now: datetime,
    artists: list[str] | None = None,
    count: int = 0,
) -> str:
    """Название собирается с нуля — название донора не берётся даже частично, чтобы
    в сообщество не утёк чужой брендинг.

    Жалоба владельца 2026-08-11: «у плейлистов одинаковые название одни и те же»,
    «надо более кликабельные, больше байта, без цензуры можно добавить». Отсюда две
    вещи:

    * `{artists}` — имена исполнителей ИЗ САМОГО сборника. Это и байт (по ним кликают),
      и SEO (их реально ищут), и главное — естественная уникальность: два сборника с
      одинаковым набором первых трёх артистов почти невозможны, поэтому названия
      перестают повторяться сами собой, а не по остаточному принципу.
    * шаблоны без `{artists}` остаются запасом на случай, когда исполнителей не
      разобрали (плейлист из роликов без имени артиста в названии).

    Уже использованные недавно варианты не берём; все заняты — берём любой, потому что
    сборник без названия хуже, чем сборник с повторным."""
    template = choose_title_template(templates, recent, now, artists, count)
    return render_title(template, now, artists, count)


TITLE_ARTIST_LIMIT = 3
"""Сколько исполнителей влезает в НАЗВАНИЕ.

Для ключей их берём восемь, а в заголовке пять имён («Егор Крид, Xcho, Джиган, Artik &
Asti, NILETTO — то, что играет у всех») читаются как свалка и обрезаются в ленте до
середины перечисления. Три — предел, после которого добавляется «и другие»."""

MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
"""Для «Сборник августа 2026»."""

MONTHS_PREPOSITIONAL = (
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
)
"""Для «Что слушают в августе». Две формы, а не одна, потому что «в августа 2026» —
это не опечатка, а мусор в заголовке, который читают тысячи человек."""


def render_title(
    template: str, now: datetime, artists: list[str] | None = None, count: int = 0
) -> str:
    """Подставляет значения в шаблон названия.

    Вынесено из выбора шаблона, чтобы короткая версия для Telegram могла получить ТО ЖЕ
    название со своим числом треков: файл на восемь песен с заголовком «ТОП-15» — это
    ровно то расхождение, из-за которого владелец уже ловил неверные тайминги."""
    names = list(artists or [])[:TITLE_ARTIST_LIMIT]
    joined = ", ".join(names)
    if artists and len(artists) > TITLE_ARTIST_LIMIT:
        joined += " и другие"
    return " ".join(
        template.format(
            year=now.year,
            month=MONTHS_GENITIVE[now.month - 1],
            month_in=MONTHS_PREPOSITIONAL[now.month - 1],
            artists=joined,
            count=count,
        ).split()
    )


def choose_title_template(
    templates: list[str],
    recent: list[str],
    now: datetime,
    artists: list[str] | None = None,
    count: int = 0,
) -> str:
    """Выбирает шаблон, избегая недавно использованных названий."""
    if not templates:
        return "Музыка без цензуры {year} — сборник"
    usable = [
        template
        for template in templates
        if (artists or "{artists}" not in template) and (count or "{count}" not in template)
    ]
    if not usable:  # все шаблоны требуют того, чего у нас нет
        return "Музыка без цензуры {year} — сборник"
    unused = [
        template
        for template in usable
        if render_title(template, now, artists, count) not in recent
    ]
    return random.choice(unused or usable)


def playlist_artists(tracks: list[Track], limit: int = 8) -> list[str]:
    """Исполнители сборника без повторов — это и есть реальные запросы к нему.

    Название сборника в ключи НЕ идёт: оно придумано нами («Плейлист 2026: музыка на
    каждый день»), никто его не ищет, а в теге вся фраза слипалась в одну нелепую
    простыню `#плейлист_2026_музыка_на_каждый_день`."""
    names: list[str] = []
    for track in tracks:
        # Перечисление в поле артиста разбираем: «Джиган, Artik & Asti, NILETTO» — это
        # три исполнителя, и одним слипшимся ключом они бесполезны (ТЗ 2026-08-16).
        names.extend(split_artists(track.artist))
    return list(dict.fromkeys(names))[:limit]


BOT_USERNAME = "muz_damn_bot"
"""⚠️ Рабочий бот Музыки. `tgram_music_bot` — НЕ он: эта ссылка ведёт в никуда и
уезжала в публикации (владелец поправил 2026-08-14). Имя держим одной константой,
чтобы правка была в одном месте, а не в промо-тексте, конфиге и документации порознь."""

DEFAULT_POST_PROMO = f"""♾️ Infinity Music — вся музыка прямо в Telegram

🤖 @{BOT_USERNAME} найдёт и пришлёт любой трек за секунды:

🔎 поиск по названию или строчке из песни

⬇️ скачивание в один тап

🎼 плейлисты, новинки и минусовки

📲 Открыть бота: https://t.me/{BOT_USERNAME}"""
"""Промо-блок записи. Текст задан владельцем дословно 2026-08-14 — правится в
`youtube_playlists.post_promo`, здесь только заводское значение."""


def build_post_text(
    config: Config, title: str, tracks: list[Track], tracklist: str = ""
) -> str:
    """Запись на стене: заголовок, промо-блок, треклист с таймингами.

    ТЗ владельца 2026-08-14 (шаблон прислан дословно): «без лишних тегов и надписей».
    Поэтому из записи убраны хештеги, строка «Треков в сборнике» и служебный заголовок
    потока — всё это лишь отодвигало вниз то, ради чего запись и открывают.

    Треклист теперь идёт В ЗАПИСЬ, а не только в описание ролика. Прежний довод
    (2026-08-10: «под записью он занял бы пол-экрана») владелец снял явно: тайминги
    и есть главная ценность сборника. В описании ролика он тоже остаётся — это разные
    индексируемые поля VK."""
    settings = config.youtube_playlists
    promo = (settings.post_promo or DEFAULT_POST_PROMO).strip()
    blocks = [title.strip(), promo, tracklist.strip()]
    return "\n\n".join(block for block in blocks if block.strip())


def _tracklist_of(tracks: list[Track]) -> str:
    """Треклист с таймингами для набора треков. Вынесено, чтобы короткая версия для
    Telegram считала свои тайминги, а не наследовала чужие."""
    return build_tracklist(
        [f"{track.artist} — {track.title}" if track.artist else track.title for track in tracks],
        [track.duration_s for track in tracks],
    )


def build_description(config: Config, title: str, tracklist: str, tracks: list[Track]) -> str:
    """Описание видеозаписи — большое: описание сборника, тайминги, ключи, сервис, теги.

    В ленте VK описание свёрнуто, поэтому объём тут бесплатный, а поиск (и внутренний
    VK, и внешние Google/Яндекс) читает его целиком."""
    settings = config.youtube_playlists
    style = config.soundcloud.post
    artists = playlist_artists(tracks)
    # ТЗ владельца 2026-08-16: шапка «♾️ Плейлисты от Infinity Music» и абзац «сборник
    # собран вручную…» убраны — первым идёт само название, оно и работает заголовком.
    # Поисковые фразы ушли из строки через точку в ХЭШТЕГИ: строка была просто текстом,
    # по которому VK не даёт перехода.
    blocks = [
        f"{settings.header}\n{title}".strip() if settings.header.strip() else title,
        settings.playlist_description.strip(),
        tracklist,
        style.service_block,
        " ".join(
            build_hashtags(
                artists, style.base_tags, style.hashtag_group, style.video_tag_limit
            )
        ),
        " ".join(build_search_tags(artists, style.search_phrases, style.channel_phrases)),
    ]
    return "\n\n".join(block for block in blocks if block.strip())


def _ready(path: Path) -> bool:
    """Файл существует и не пуст. Атомарная запись гарантирует: значит, он досчитан."""
    return path.exists() and path.stat().st_size > 0


def _render(
    tracks: list[Track], work_dir: Path, tg_max_tracks: int = 0
) -> tuple[Path, Path | None]:
    """Каждый трек → сегмент с подписью на первые 10 секунд, потом склейка.

    Возвращает полный сборник и (если треков больше `tg_max_tracks`) короткую версию для
    отдачи в Telegram. ТЗ владельца 2026-08-16: «фулл плейлист можно в ВК, а в ТГ
    обрезанный». Короткая версия — это ещё одна склейка ТЕХ ЖЕ сегментов потоковым
    копированием: второго рендера не нужно, и стоит она секунды.

    Готовый сегмент от прошлого тика переиспользуется. Рендер — самая долгая часть, и
    когда юнит убивают по таймауту, начинать всё заново значит не доделать никогда."""
    compilation = work_dir / "compilation.mp4"
    short_expected = 0 < tg_max_tracks < len(tracks)
    short = work_dir / "compilation_tg.mp4" if short_expected else None

    # Готовый сборник от прошлого тика не пересобираем. Случай не теоретический: файл
    # отдан владельцу, а публикация в ВК ждёт окна — между ними часы, и рендер пятнадцати
    # треков заново это ещё полчаса единственного ядра ради того же самого файла.
    # Имя `compilation.mp4` означает «досчитан» — см. атомарную запись в `concat_videos`.
    if _ready(compilation) and (short is None or _ready(short)):
        get_logger().info("Сборник уже собран (%s) — пропускаю рендер", compilation.name)
        return compilation, short

    segments: list[Path] = []
    for track in tracks:
        segment = work_dir / f"seg_{track.position:03d}.mp4"
        if segment.exists() and segment.stat().st_size > 0:
            get_logger().info("Сегмент %s уже готов — пропускаю", segment.name)
        else:
            render_track_video(
                track.audio_path, track.cover_path, segment,
                caption=TrackCaption(artist=track.artist, title=track.title),
            )
        segments.append(segment)

    concat_videos(segments, compilation)
    if short is not None:
        concat_videos(segments[:tg_max_tracks], short)

    for segment in segments:
        segment.unlink(missing_ok=True)
    return compilation, short


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


DELIVERY_CAPTION = ""
"""Подпись к отданному файлу — ПУСТАЯ. ТЗ владельца 2026-08-16: «эту хрень не писать».

Раньше здесь стояла метка `#сборник`, по которой бот Новостей отличал доставку от
просьбы уникализировать присланное медиа. Держать её больше незачем: сам перехват
медиа снят 2026-08-14 («уникализатор не нужен»), и метка осталась бы шумом в чате,
который владелец читает каждый день."""


def _deliver(
    config: Config,
    playlists: PlaylistQueue,
    playlist: PlaylistRow,
    compilation: Compilation,
    notifier: Notifier,
) -> Path:
    """Отдать готовый файл владельцу и вернуть путь, с которого грузить в VK.

    Отдача переносит файл в `ready/`, поэтому путь после неё МЕНЯЕТСЯ — грузить в VK
    надо именно возвращённый, иначе загрузка не найдёт файл.

    Сбой отдачи не роняет тик: сборник важнее, публикация пойдёт с исходного пути.
    Повторная попытка того же плейлиста файл не дублирует — см. `delivered`."""
    settings = config.youtube_playlists
    if playlist.delivered:
        get_logger().info("Сборник %s уже отдавали владельцу — не дублируем", playlist.url)
        return compilation.file_for_owner
    part_of = (
        (compilation.delivery_tracks, len(compilation.tracks))
        if compilation.delivery_path
        else None
    )
    try:
        uploader = TelegramUploader.from_config(config)
        result = deliver(
            compilation.file_for_owner,
            ready_dir=settings.ready_dir,
            file_name=_safe_file_name(compilation.title),
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_admin_chat_id,
            remote_host=settings.remote_host,
            caption=DELIVERY_CAPTION,
            uploader=uploader,
        )
        playlists.mark_delivered(playlist.id)
        # ТЗ владельца 2026-08-16: «сразу мне готовые описание давай без воды». Ни
        # заголовка, ни счётчика треков, ни «публикую в VK» — всё это он и так видит по
        # самому файлу, а сообщение он копирует в YouTube целиком.
        details = compilation.description_for_owner[:3500]
        # Текст идёт ТЕМ ЖЕ каналом, что и файл, и сразу за ним. Ботом он приходил в
        # другой диалог, и владелец видел «сборник пришёл, а описания и названия к нему
        # нет» (2026-08-15). Сообщение ОДНО: дублировать описание в двух местах значит
        # заставлять владельца сверять, какое из них свежее.
        # Бот остаётся запасным путём — когда MTProto недоступен и файл ушёл через scp.
        if not (result.sent_to_telegram and uploader is not None
                and uploader.send_message(details)):
            notifier.send(details)
        # Обложка уходит ОТДЕЛЬНЫМ файлом и последней: на YouTube превью ставится
        # вручную, и картинка должна лежать в чате рядом с роликом и описанием.
        if compilation.thumbnail_path and uploader is not None:
            uploader.send_file(compilation.thumbnail_path)
        return result.path
    except Exception as exc:  # noqa: BLE001 — отдача файла не должна ронять тик
        get_logger().warning("Не удалось отдать сборник владельцу: %s", exc)
        return compilation.file_for_owner


def _safe_file_name(title: str) -> str:
    """Имя файла из заголовка: без разделителей путей и прочего, что ломает scp."""
    cleaned = "".join(char if char.isalnum() or char in " -_" else "_" for char in title)
    return f"{' '.join(cleaned.split())[:80] or 'playlist'}.mp4"
