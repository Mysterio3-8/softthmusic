from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from app.vk_token_pool import DEFAULT_DAILY_CAP, MIN_GAP_MINUTES


class ConfigError(Exception):
    """Проблема в конфигурации или отсутствие обязательного секрета."""


@dataclass(frozen=True)
class PostStyle:
    """Оформление поста-релиза.

    flag задаётся вручную: страны артиста в метаданных SoundCloud нет, вывести
    её автоматически неоткуда — это осознанно настройка, а не пропущенная фича.
    """

    flag: str
    title_suffix: str
    listen_label: str
    listen_url: str
    channel_label: str
    channel_url: str
    hashtag_template: str
    hashtag_group: str
    track_kind: str
    album_kind: str
    # SEO (ТЗ 2026-08-10). Пустые значения = прежнее поведение: один тег и никаких
    # поисковых фраз, то есть новые поля ничего не ломают у существующего конфига.
    base_tags: list[str] = field(default_factory=list)
    search_phrases: list[str] = field(default_factory=list)
    post_tag_limit: int = 6
    video_tag_limit: int = 16
    service_block: str = ""
    """Блок «что это за сервис» в конце описания видео: ссылка на бота и на канал.
    Он и есть та внешняя ссылка, ради которой всё SEO затевалось."""


@dataclass(frozen=True)
class SoundCloudConfig:
    """Настройки альбомного потока. Антибан-имена совпадают с контрактом менеджера."""

    min_interval_minutes: int
    max_interval_minutes: int
    quiet_start_hour: int
    quiet_end_hour: int
    max_posts_per_day: int
    max_track_attempts: int
    work_dir: Path
    post: PostStyle


@dataclass(frozen=True)
class YoutubePlaylistsConfig:
    """Поток сборников с YouTube (ТЗ 2026-08-10). enabled=False → его как будто нет."""

    enabled: bool
    sources: list[str]
    discover_limit: int
    max_tracks: int
    max_posts_per_day: int
    min_interval_minutes: int
    max_interval_minutes: int
    quiet_start_hour: int
    quiet_end_hour: int
    max_attempts: int
    work_dir: Path
    ready_dir: Path
    ready_keep_days: int
    remote_host: str
    header: str
    playlist_description: str
    title_templates: list[str]


@dataclass
class Config:
    vk_group_token: str
    vk_user_token: str
    group_id: int
    channels: list[str]
    max_height: int
    posts_per_day: int
    posts_per_run: int
    publish_times: list[str]
    ad_block: str
    retry_delays_minutes: list[int]
    database_path: Path
    downloads_dir: Path
    log_path: Path
    # Имена переменных общего пула личных токенов; пусто → один свой VK_USER_TOKEN
    # (значения по умолчанию нужны, чтобы старые вызовы Config(...) не ломались).
    vk_upload_token_envs: list[str] = field(default_factory=list)
    vk_token_daily_cap: int = DEFAULT_DAILY_CAP
    vk_token_min_gap_minutes: int = MIN_GAP_MINUTES
    # Альбомный поток необязателен: без секции soundcloud: и Telegram-переменных
    # проект остаётся чистым YouTube-паблишером.
    soundcloud: SoundCloudConfig = field(default_factory=lambda: _build_soundcloud({}))
    youtube_playlists: YoutubePlaylistsConfig = field(
        default_factory=lambda: _build_youtube_playlists({})
    )
    telegram_bot_token: str = ""
    telegram_admin_chat_id: int | None = None
    # MTProto-доставка сборника владельцу. Нужна потому, что Bot API отдаёт максимум
    # 50 МБ, а сборник весит 120-150 МБ. Пусто → остаётся запасной путь (ready/ + scp).
    tg_api_id: int = 0
    tg_api_hash: str = ""
    tg_session_name: str = ""

    @property
    def owner_id(self) -> int:
        """owner_id стены сообщества — отрицательный group_id."""
        return -self.group_id


def load_config(config_path: str | Path = "config.yaml", env_path: str | Path = ".env") -> Config:
    load_dotenv(env_path)

    group_token = os.getenv("VK_GROUP_TOKEN", "").strip()
    if not group_token:
        raise ConfigError("VK_GROUP_TOKEN не задан в .env")

    # Загрузка видео в VK возможна ТОЛЬКО user-токеном (групповой -> [5]).
    # Постинг идёт групповым токеном, user-токен дёргается лишь на upload.
    user_token = os.getenv("VK_USER_TOKEN", "").strip()
    if not user_token:
        raise ConfigError(
            "VK_USER_TOKEN не задан в .env. Он нужен только для загрузки видео "
            "(VK не даёт грузить видео групповым токеном, ошибка [5]). "
            "Постинг всё равно идёт групповым токеном."
        )

    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"Файл конфигурации не найден: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _build_config(raw, group_token, user_token, _telegram_env(), _mtproto_env())


def _telegram_env() -> tuple[str, int | None]:
    """Токен и chat_id для уведомлений. Пусто — уведомления просто не шлются."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    raw_chat_id = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
    chat_id = int(raw_chat_id) if raw_chat_id.lstrip("-").isdigit() else None
    return token, chat_id


def _mtproto_env() -> tuple[int, str, str]:
    """Креды пользовательской сессии Telegram — ими отдаётся сборник (файл >50 МБ).
    Не заданы → нули, и доставка молча уходит на запасной путь."""
    raw_id = os.getenv("TG_API_ID", "").strip()
    return (
        int(raw_id) if raw_id.isdigit() else 0,
        os.getenv("TG_API_HASH", "").strip(),
        os.getenv("TG_SESSION_NAME", "").strip(),
    )


def _build_config(
    raw: dict,
    group_token: str,
    user_token: str,
    telegram: tuple[str, int | None] = ("", None),
    mtproto: tuple[int, str, str] = (0, "", ""),
) -> Config:
    vk = raw.get("vk") or {}
    youtube = raw.get("youtube") or {}
    publishing = raw.get("publishing") or {}
    paths = raw.get("paths") or {}
    retry = raw.get("retry") or {}

    group_id = vk.get("group_id")
    if not isinstance(group_id, int) or group_id <= 0:
        raise ConfigError("vk.group_id должен быть положительным числом")

    upload_token_envs = [str(name) for name in (vk.get("upload_token_envs") or [])]
    token_daily_cap = int(vk.get("token_daily_cap", DEFAULT_DAILY_CAP))
    token_min_gap = int(vk.get("token_min_gap_minutes", MIN_GAP_MINUTES))

    channels = [c.strip() for c in (youtube.get("channels") or []) if str(c).strip()]
    if not channels:
        raise ConfigError("youtube.channels пуст — укажите хотя бы один канал")

    posts_per_day = int(publishing.get("posts_per_day", 3))
    if posts_per_day < 1:
        raise ConfigError("publishing.posts_per_day должен быть >= 1")

    posts_per_run = int(publishing.get("posts_per_run", 1))
    if posts_per_run < 1:
        raise ConfigError("publishing.posts_per_run должен быть >= 1")

    times = [str(t).strip() for t in (publishing.get("times") or []) if str(t).strip()]
    publish_times = _normalize_times(times, posts_per_day)

    ad_block = str(raw.get("ad_block", "")).strip()

    return Config(
        vk_group_token=group_token,
        vk_user_token=user_token,
        group_id=group_id,
        vk_upload_token_envs=upload_token_envs,
        vk_token_daily_cap=token_daily_cap,
        vk_token_min_gap_minutes=token_min_gap,
        channels=channels,
        max_height=int(youtube.get("max_height", 480)),
        posts_per_day=posts_per_day,
        posts_per_run=posts_per_run,
        publish_times=publish_times,
        ad_block=ad_block,
        retry_delays_minutes=[int(x) for x in (retry.get("delays_minutes") or [60, 180])],
        database_path=Path(paths.get("database", "data/publisher.db")),
        downloads_dir=Path(paths.get("downloads", "downloads")),
        log_path=Path(paths.get("logs", "logs/publisher.log")),
        soundcloud=_build_soundcloud(raw.get("soundcloud") or {}),
        youtube_playlists=_build_youtube_playlists(raw.get("youtube_playlists") or {}),
        telegram_bot_token=telegram[0],
        telegram_admin_chat_id=telegram[1],
        tg_api_id=mtproto[0],
        tg_api_hash=mtproto[1],
        tg_session_name=mtproto[2],
    )


def _build_soundcloud(raw: dict) -> SoundCloudConfig:
    min_interval = int(raw.get("min_interval_minutes", 180))
    max_interval = int(raw.get("max_interval_minutes", 300))
    if min_interval > max_interval:
        raise ConfigError(
            "soundcloud.min_interval_minutes больше max_interval_minutes — "
            "случайный интервал не построить"
        )

    quiet_start = int(raw.get("quiet_start_hour", 23))
    quiet_end = int(raw.get("quiet_end_hour", 9))
    for name, hour in (("quiet_start_hour", quiet_start), ("quiet_end_hour", quiet_end)):
        if not 0 <= hour <= 23:
            raise ConfigError(f"soundcloud.{name} должен быть в диапазоне 0..23")

    return SoundCloudConfig(
        min_interval_minutes=min_interval,
        max_interval_minutes=max_interval,
        quiet_start_hour=quiet_start,
        quiet_end_hour=quiet_end,
        max_posts_per_day=int(raw.get("max_posts_per_day", 5)),
        max_track_attempts=int(raw.get("max_track_attempts", 3)),
        work_dir=Path(raw.get("work_dir", "downloads/soundcloud")),
        post=_build_post_style(raw.get("post") or {}),
    )


def _build_youtube_playlists(raw: dict) -> YoutubePlaylistsConfig:
    min_interval = int(raw.get("min_interval_minutes", 300))
    max_interval = int(raw.get("max_interval_minutes", 480))
    if min_interval > max_interval:
        raise ConfigError(
            "youtube_playlists.min_interval_minutes больше max_interval_minutes — "
            "случайный интервал не построить"
        )
    quiet_start = int(raw.get("quiet_start_hour", 0))
    quiet_end = int(raw.get("quiet_end_hour", 0))
    for name, hour in (("quiet_start_hour", quiet_start), ("quiet_end_hour", quiet_end)):
        if not 0 <= hour <= 23:
            raise ConfigError(f"youtube_playlists.{name} должен быть в диапазоне 0..23")

    return YoutubePlaylistsConfig(
        enabled=bool(raw.get("enabled", False)),
        sources=[str(item).strip() for item in (raw.get("sources") or []) if str(item).strip()],
        discover_limit=int(raw.get("discover_limit", 20)),
        max_tracks=int(raw.get("max_tracks", 15)),
        max_posts_per_day=int(raw.get("max_posts_per_day", 2)),
        min_interval_minutes=min_interval,
        max_interval_minutes=max_interval,
        quiet_start_hour=quiet_start,
        quiet_end_hour=quiet_end,
        max_attempts=int(raw.get("max_attempts", 3)),
        work_dir=Path(raw.get("work_dir", "downloads/yt_playlists")),
        ready_dir=Path(raw.get("ready_dir", "ready")),
        ready_keep_days=int(raw.get("ready_keep_days", 5)),
        remote_host=str(raw.get("remote_host", "news-rewriter-vps")),
        header=str(raw.get("header", "♾️ Плейлисты от Infinity Music")),
        playlist_description=str(raw.get("playlist_description", "")),
        title_templates=[str(item) for item in (raw.get("title_templates") or [])],
    )


def _build_post_style(raw: dict) -> PostStyle:
    return PostStyle(
        flag=str(raw.get("flag", "🎧")),
        title_suffix=str(raw.get("title_suffix", "Без цензуры")),
        listen_label=str(raw.get("listen_label", "♾️ Слушать в Telegram бесплатно и без цензуры:")),
        listen_url=str(raw.get("listen_url", "")),
        channel_label=str(raw.get("channel_label", "📢 Канал:")),
        channel_url=str(raw.get("channel_url", "")),
        hashtag_template=str(raw.get("hashtag_template", "{artist}_{name}")),
        hashtag_group=str(raw.get("hashtag_group", "")),
        track_kind=str(raw.get("track_kind", "Single")),
        album_kind=str(raw.get("album_kind", "Album")),
        base_tags=[str(tag) for tag in (raw.get("base_tags") or [])],
        search_phrases=[str(phrase) for phrase in (raw.get("search_phrases") or [])],
        post_tag_limit=int(raw.get("post_tag_limit", 6)),
        video_tag_limit=int(raw.get("video_tag_limit", 16)),
        service_block=str(raw.get("service_block", "")).strip(),
    )


def _normalize_times(times: list[str], posts_per_day: int) -> list[str]:
    """Приводит список времён к длине posts_per_day.

    Лишние обрезаются; недостающие доливаются равномерным шагом от последнего.
    """
    if not times:
        return _even_times(posts_per_day)
    if len(times) >= posts_per_day:
        return times[:posts_per_day]

    result = list(times)
    last_minutes = _to_minutes(result[-1])
    step = max(1, (24 * 60) // posts_per_day)
    while len(result) < posts_per_day:
        last_minutes = (last_minutes + step) % (24 * 60)
        result.append(_to_hhmm(last_minutes))
    return result


def _even_times(count: int) -> list[str]:
    step = (24 * 60) // count
    return [_to_hhmm((9 * 60 + i * step) % (24 * 60)) for i in range(count)]


def _to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
