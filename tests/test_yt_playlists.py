"""Поток сборников с YouTube: расписание, лимит, тексты записи и описания."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.album_db import AlbumQueue
from app.config import Config, PostStyle, SoundCloudConfig, YoutubePlaylistsConfig
from app.soundcloud import Track
from app.yt_playlist_db import POST_KIND_YT_PLAYLIST, PlaylistQueue
from app.yt_playlists import (
    build_description,
    build_post_text,
    build_title,
    _minutes_until_due,
    tick,
)


class FakeVK:
    def __init__(self, busy=False):
        self.busy = busy
        self.uploads = []
        self.posts = []

    def pool_is_busy(self):
        return self.busy

    def upload_video(self, file_path, name, description):
        self.uploads.append((name, description))
        return "video-1_1"

    def post_now(self, message, attachment):
        self.posts.append(message)
        return len(self.posts)


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send(self, text, chat_id=None):
        self.messages.append(text)
        return True


def _style(**overrides) -> PostStyle:
    base = dict(
        flag="🎧",
        title_suffix="Без цензуры",
        listen_label="♾️ Слушать в Telegram:",
        listen_url="https://t.me/tgram_music_bot",
        channel_label="📢 Канал:",
        channel_url="",
        hashtag_template="{artist}_{name}",
        hashtag_group="tgmusic",
        track_kind="Single",
        album_kind="Album",
        base_tags=["музыка", "плейлист"],
        search_phrases=["{q} слушать онлайн", "{q} скачать бесплатно"],
        post_tag_limit=5,
        video_tag_limit=12,
        service_block="♾️ Infinity Music — бот https://t.me/tgram_music_bot",
    )
    base.update(overrides)
    return PostStyle(**base)


def _playlists_config(tmp_path, **overrides) -> YoutubePlaylistsConfig:
    base = dict(
        enabled=True,
        sources=["тест"],
        discover_limit=5,
        max_tracks=5,
        max_posts_per_day=2,
        min_interval_minutes=420,
        max_interval_minutes=620,
        quiet_start_hour=0,
        quiet_end_hour=0,
        max_attempts=3,
        work_dir=tmp_path / "work",
        ready_dir=tmp_path / "ready",
        ready_keep_days=5,
        remote_host="vps",
        deliver_chat="",
        header="♾️ Плейлисты от Infinity Music",
        playlist_description="Сборник собран вручную.",
        title_templates=["Плейлист {year}: музыка на каждый день"],
    )
    base.update(overrides)
    return YoutubePlaylistsConfig(**base)


def _config(tmp_path, **overrides) -> Config:
    return Config(
        vk_group_token="g",
        vk_user_token="u",
        group_id=240295467,
        channels=["c"],
        max_height=480,
        posts_per_day=3,
        posts_per_run=1,
        publish_times=["09:00"],
        ad_block="",
        retry_delays_minutes=[60],
        database_path=tmp_path / "db.sqlite",
        downloads_dir=tmp_path / "dl",
        log_path=tmp_path / "log.txt",
        soundcloud=SoundCloudConfig(
            min_interval_minutes=180,
            max_interval_minutes=300,
            quiet_start_hour=0,
            quiet_end_hour=0,
            max_posts_per_day=3,
            max_track_attempts=3,
            work_dir=tmp_path / "sc",
            post=_style(),
        ),
        youtube_playlists=_playlists_config(tmp_path, **overrides),
    )


@pytest.fixture
def queues(tmp_path):
    posts = AlbumQueue(tmp_path / "db.sqlite")
    playlists = PlaylistQueue(tmp_path / "db.sqlite")
    yield posts, playlists
    posts.close()
    playlists.close()


def _tracks() -> list[Track]:
    return [
        Track(1, "Штиль", "Ария", 300, Path("a.mp3"), None),
        Track(2, "Осколок льда", "Ария", 240, Path("b.mp3"), None),
    ]


def test_disabled_stream_does_nothing(tmp_path, queues):
    posts, playlists = queues
    config = _config(tmp_path, enabled=False)

    assert tick(config, playlists, posts, FakeVK(), FakeNotifier()) == "поток сборников выключен"


def test_daily_limit_blocks_publishing(tmp_path, queues):
    posts, playlists = queues
    posts.log_post(POST_KIND_YT_PLAYLIST)
    posts.log_post(POST_KIND_YT_PLAYLIST)

    outcome = tick(_config(tmp_path), playlists, posts, FakeVK(), FakeNotifier())

    assert outcome == "суточный лимит сборников исчерпан"


def test_track_posts_do_not_eat_playlist_quota(tmp_path, queues):
    """У каждого потока свой счётчик: три трека не должны блокировать сборник."""
    posts, playlists = queues
    for _ in range(3):
        posts.log_post("track")

    outcome = tick(_config(tmp_path), playlists, posts, FakeVK(), FakeNotifier())

    assert outcome == "очередь сборников пуста"


def test_busy_token_pool_stops_before_heavy_work(tmp_path, queues):
    posts, playlists = queues
    playlists.add("https://youtube.com/playlist?list=1", "t", "u", "src")

    outcome = tick(_config(tmp_path), playlists, posts, FakeVK(busy=True), FakeNotifier())

    assert outcome == "личный токен занят — ждём следующего тика"


def test_quiet_hours_hold_the_stream(tmp_path, queues):
    posts, playlists = queues
    config = _config(tmp_path, quiet_start_hour=23, quiet_end_hour=9)

    outcome = tick(
        config, playlists, posts, FakeVK(), FakeNotifier(), datetime(2026, 8, 10, 3, 0)
    )

    assert outcome == "ночная пауза"


def test_interval_is_measured_from_the_previous_playlist(tmp_path, queues):
    posts, playlists = queues
    posts.log_post(POST_KIND_YT_PLAYLIST)
    settings = _playlists_config(tmp_path)

    from app.album_scheduler import now_msk

    assert _minutes_until_due(posts, settings, now_msk()) > 0
    # Через сутки интервал заведомо выдержан — какой бы бросок ни выпал.
    assert _minutes_until_due(posts, settings, now_msk() + timedelta(days=1)) == 0


def test_interval_roll_is_stable_between_calls(tmp_path, queues):
    """Бросок детерминирован временем прошлой публикации: если бросать заново на
    каждом тике, диапазон схлопывается в нижнюю границу."""
    posts, playlists = queues
    posts.log_post(POST_KIND_YT_PLAYLIST)
    settings = _playlists_config(tmp_path)

    from app.album_scheduler import now_msk

    moment = now_msk()
    assert _minutes_until_due(posts, settings, moment) == _minutes_until_due(
        posts, settings, moment
    )


def test_title_avoids_recently_used_templates():
    templates = ["A {year}", "B {year}"]
    now = datetime(2026, 8, 10)

    assert build_title(templates, ["A 2026"], now) == "B 2026"


def test_title_falls_back_when_all_templates_used():
    now = datetime(2026, 8, 10)
    assert build_title(["A {year}"], ["A 2026"], now) == "A 2026"


def test_title_uses_artists_of_the_compilation():
    """Жалоба 2026-08-11: «у плейлистов одинаковые название одни и те же».
    Имена исполнителей из самого сборника делают названия и кликабельными, и разными."""
    now = datetime(2026, 8, 10)

    title = build_title(["{artists} — музыка без цензуры {year}"], [], now, ["Miyagi", "Скриптонит"])

    assert title == "Miyagi, Скриптонит — музыка без цензуры 2026"


def test_title_skips_artist_templates_when_artists_unknown():
    """Плейлист из роликов без имени артиста в названии не должен дать «, — музыка»."""
    now = datetime(2026, 8, 10)

    title = build_title(["{artists} — микс", "Музыка без цензуры {year}"], [], now, [])

    assert title == "Музыка без цензуры 2026"


def test_title_has_safe_fallback_without_any_usable_template():
    now = datetime(2026, 8, 10)

    # Запасное название тоже обязано читаться как СБОРНИК (ТЗ 2026-08-17), иначе на
    # плейлисте без разобранных артистов пропадает единственный сигнал «тут много треков».
    assert build_title(["{artists} — микс"], [], now, []) == "Музыка без цензуры 2026 — сборник"


def test_post_text_follows_owner_template(tmp_path):
    """Шаблон записи задан владельцем дословно 2026-08-14: заголовок, промо, тайминги.

    «Без лишних тегов и надписей» — поэтому в записи не должно остаться ни хештегов,
    ни служебного заголовка потока, ни строки «Треков в сборнике»."""
    config = _config(tmp_path)
    text = build_post_text(config, "Плейлист 2026", _tracks(), "00:00 1. Ария — Штиль")

    assert text.startswith("Плейлист 2026")
    assert "https://t.me/muz_damn_bot" in text
    assert "00:00 1. Ария — Штиль" in text
    assert "#" not in text
    assert "Треков в сборнике" not in text
    assert "♾️ Плейлисты от Infinity Music" not in text


def test_post_text_never_carries_the_dead_bot_link(tmp_path):
    """`tgram_music_bot` не существует, а ссылка уезжала в публикации."""
    config = _config(tmp_path)

    assert "tgram_music_bot" not in build_post_text(config, "Плейлист", _tracks(), "00:00 1. Х")


def test_invented_title_never_becomes_a_hashtag(tmp_path):
    """Название сборника придумано нами — в теге вся фраза слипалась в простыню."""
    config = _config(tmp_path)
    title = "Плейлист 2026: музыка на каждый день"

    for text in (
        build_post_text(config, title, _tracks()),
        build_description(config, title, "00:00 1. Ария — Штиль", _tracks()),
    ):
        assert "#плейлист_2026" not in text


def test_search_phrases_are_built_from_artists_not_from_the_title(tmp_path):
    config = _config(tmp_path)
    description = build_description(
        config, "Плейлист 2026", "00:00 1. Ария — Штиль", _tracks()
    )

    # ТЗ 2026-08-16: поисковые фразы идут ХЭШТЕГАМИ, а не строкой через точку —
    # строка была просто текстом, по которому VK не даёт перехода.
    assert "#ария_слушать_онлайн" in description
    assert "плейлист_2026_слушать_онлайн" not in description


def test_description_has_timings_search_phrases_and_service(tmp_path):
    config = _config(tmp_path)
    description = build_description(
        config, "Плейлист 2026", "00:00 1. Ария — Штиль\n05:00 2. Ария — Осколок льда", _tracks()
    )

    assert "00:00 1. Ария — Штиль" in description
    assert "#ария_слушать_онлайн" in description
    assert "Infinity Music" in description
    assert description.rstrip().splitlines()[-1].startswith("#")


def test_description_keeps_search_keys_out_of_the_post(tmp_path):
    """Запись теперь длинная (тайминги в ней), поэтому сравнивать длины бессмысленно.
    Важно другое: поисковые ключи и теги живут ТОЛЬКО в описании ролика — в ленте оно
    свёрнуто, а запись владелец просил держать чистой."""
    config = _config(tmp_path)
    text = build_post_text(config, "Плейлист 2026", _tracks(), "00:00 1. Ария — Штиль")
    description = build_description(config, "Плейлист 2026", "00:00 1. Ария — Штиль", _tracks())

    assert "#" in description
    assert "#" not in text


def test_title_names_at_most_three_artists():
    """ТЗ владельца 2026-08-17: «кликабельные названия». Пять имён подряд читаются как
    свалка и обрезаются в ленте на середине перечисления."""
    from app.yt_playlists import render_title

    title = render_title(
        "{artists} — {count} треков",
        datetime(2026, 8, 17),
        ["Егор Крид", "Xcho", "Джиган", "Artik & Asti", "NILETTO"],
        15,
    )

    assert title == "Егор Крид, Xcho, Джиган и другие — 15 треков"


def test_title_without_extra_artists_has_no_dangling_phrase():
    from app.yt_playlists import render_title

    title = render_title("{artists} — {count} треков", datetime(2026, 8, 17), ["Ария"], 5)

    assert title == "Ария — 5 треков"


def test_month_has_two_cases():
    """«Что слушают в августа» — не опечатка, а мусор в заголовке, который читают тысячи."""
    from app.yt_playlists import render_title

    now = datetime(2026, 8, 17)

    assert render_title("Сборник {month} {year}", now) == "Сборник августа 2026"
    assert render_title("Что слушают в {month_in}", now) == "Что слушают в августе"


def test_templates_needing_a_count_are_skipped_without_one():
    """«ТОП-0 треков» хуже, чем безликое название."""
    from app.yt_playlists import choose_title_template

    chosen = choose_title_template(
        ["ТОП-{count} треков", "Музыка без цензуры {year}"], [], datetime(2026, 8, 17), [], 0
    )

    assert chosen == "Музыка без цензуры {year}"


def test_window_start_is_todays_opening():
    """Окно Музыки 00:00–09:00 МСК: тишина 9..24, работа с полуночи."""
    from app.yt_playlists import window_start

    now = datetime(2026, 8, 18, 3, 45)

    assert window_start(now, quiet_start_hour=9, quiet_end_hour=0) == datetime(2026, 8, 18, 0, 0)


def test_window_start_rolls_back_when_opening_is_later_today():
    """Окно Кино 09:00–24:00. В 10:00 окно открылось сегодня в 09:00, а не вчера."""
    from app.yt_playlists import window_start

    assert window_start(
        datetime(2026, 8, 18, 10, 0), quiet_start_hour=0, quiet_end_hour=9
    ) == datetime(2026, 8, 18, 9, 0)


def test_window_start_without_quiet_hours_is_midnight():
    """Тишина не задана → сутки целиком, прежнее поведение."""
    from app.yt_playlists import window_start

    assert window_start(
        datetime(2026, 8, 18, 15, 0), quiet_start_hour=0, quiet_end_hour=0
    ) == datetime(2026, 8, 18, 0, 0)


def test_quota_is_counted_from_the_window_opening():
    """🔴 Счётчик был скользящим за 24 часа при окне в 9 часов, и это давало НЕ два
    сборника в сутки, а один: первый выходил в 00:00, второй в 03:45, а следующей ночью
    в 00:00 оба ещё «в сутках» — ночь пропускалась целиком, и публикации уползали всё
    позже, рискуя вывалиться из окна совсем."""
    from app.yt_playlists import _daily_limit_reached

    class _Settings:
        quiet_start_hour = 9
        quiet_end_hour = 0

    class _Posts:
        def __init__(self):
            self.boundary = None

        def posts_since(self, moment, kinds=None):
            self.boundary = moment
            return 0

    posts = _Posts()
    _daily_limit_reached(posts, 2, datetime(2026, 8, 18, 3, 45), _Settings)

    # Граница — открытие окна этой ночи, а не «сутки назад».
    assert posts.boundary == datetime(2026, 8, 18, 0, 0)


def test_quota_without_settings_stays_on_rolling_day():
    """Старый вызов без окна обязан работать ровно как раньше."""
    from app.yt_playlists import _daily_limit_reached

    class _Posts:
        def __init__(self):
            self.boundary = None

        def posts_since(self, moment, kinds=None):
            self.boundary = moment
            return 0

    posts = _Posts()
    now = datetime(2026, 8, 18, 3, 45)
    _daily_limit_reached(posts, 2, now)

    assert posts.boundary == now - timedelta(days=1)


def test_genre_title_is_capitalised():
    """Жанр приходит поисковым запросом («фонк»), а в заголовке стоит первым словом."""
    from app.yt_playlists import render_title

    title = render_title("{genre} — ТОП-{count} треков", datetime(2026, 8, 17), [], 15, "фонк")

    assert title == "Фонк — ТОП-15 треков"


def test_genre_templates_are_skipped_without_a_genre():
    """У чужого плейлиста жанра нет — шаблон с {genre} дал бы заголовок с дырой."""
    from app.yt_playlists import choose_title_template

    chosen = choose_title_template(
        ["{genre} — ТОП-{count}", "ТОП-{count} треков"], [], datetime(2026, 8, 17), [], 15, ""
    )

    assert chosen == "ТОП-{count} треков"
