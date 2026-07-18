from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    """Проблема в конфигурации или отсутствие обязательного секрета."""


@dataclass
class Config:
    vk_group_token: str
    vk_user_token: str
    group_id: int
    channels: list[str]
    max_height: int
    posts_per_day: int
    publish_times: list[str]
    ad_block: str
    retry_delays_minutes: list[int]
    database_path: Path
    downloads_dir: Path
    log_path: Path

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
    return _build_config(raw, group_token, user_token)


def _build_config(raw: dict, group_token: str, user_token: str) -> Config:
    vk = raw.get("vk") or {}
    youtube = raw.get("youtube") or {}
    publishing = raw.get("publishing") or {}
    paths = raw.get("paths") or {}
    retry = raw.get("retry") or {}

    group_id = vk.get("group_id")
    if not isinstance(group_id, int) or group_id <= 0:
        raise ConfigError("vk.group_id должен быть положительным числом")

    channels = [c.strip() for c in (youtube.get("channels") or []) if str(c).strip()]
    if not channels:
        raise ConfigError("youtube.channels пуст — укажите хотя бы один канал")

    posts_per_day = int(publishing.get("posts_per_day", 3))
    if posts_per_day < 1:
        raise ConfigError("publishing.posts_per_day должен быть >= 1")

    times = [str(t).strip() for t in (publishing.get("times") or []) if str(t).strip()]
    publish_times = _normalize_times(times, posts_per_day)

    ad_block = str(raw.get("ad_block", "")).strip()

    return Config(
        vk_group_token=group_token,
        vk_user_token=user_token,
        group_id=group_id,
        channels=channels,
        max_height=int(youtube.get("max_height", 480)),
        posts_per_day=posts_per_day,
        publish_times=publish_times,
        ad_block=ad_block,
        retry_delays_minutes=[int(x) for x in (retry.get("delays_minutes") or [60, 180])],
        database_path=Path(paths.get("database", "data/publisher.db")),
        downloads_dir=Path(paths.get("downloads", "downloads")),
        log_path=Path(paths.get("logs", "logs/publisher.log")),
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
