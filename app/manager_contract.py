"""Чтение контракта менеджера — настроек, присланных ботом «📦 Софты».

Зачем. Бот-менеджер (в Новостях) умеет писать `manager_contract.yaml` в каталог любого
софта: лимит публикаций, интервал, ночное окно. До сих пор он писал в пустоту — софты
файл не читали, и «полное управление из бота» оставалось декларацией: кнопки нажимались,
значения сохранялись, поведение не менялось.

Правила разрешения:

* контракта нет или он пуст → работает `config.yaml`, ровно как раньше;
* поле задано → оно СИЛЬНЕЕ `config.yaml`, иначе правка из бота не имела бы смысла;
* файл битый → пишем в журнал и работаем по конфигу. Сломанный контракт — это настройка,
  а не разрешение работать.

⚠️ Контракт накрывает ТОЛЬКО поток треков (`soundcloud`). Сборники (`youtube_playlists`)
живут своим счётчиком осознанно: у них другая цена публикации (скачать 15 треков и
склеить видео) и другой темп, и общий лимит съедал бы квоту одного потока другим.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.logger import get_logger

CONTRACT_FILENAME = "manager_contract.yaml"

FIELDS = (
    "max_posts_per_day",
    "min_interval_minutes",
    "max_interval_minutes",
    "quiet_start_hour",
    "quiet_end_hour",
)


def read_contract(project_dir: Path | str = ".") -> dict:
    """Заданные лимиты из контракта. Пустой словарь — контракта нет или он пуст."""
    path = Path(project_dir) / CONTRACT_FILENAME
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        get_logger().warning(
            "Контракт менеджера не прочитан (%s) — работаем по config.yaml", error
        )
        return {}
    limits = data.get("limits") if isinstance(data, dict) else None
    if not isinstance(limits, dict):
        return {}
    return {
        key: int(limits[key])
        for key in FIELDS
        if limits.get(key) is not None and str(limits[key]).lstrip("-").isdigit()
    }


def read_sources(project_dir: Path | str = ".") -> dict:
    """Источники из контракта: {"primary": [...], "secondary": [...]}."""
    path = Path(project_dir) / CONTRACT_FILENAME
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, dict):
        return {}
    return {
        key: [str(item) for item in value]
        for key, value in sources.items()
        if isinstance(value, list) and value
    }


def apply_sources(raw: dict, project_dir: Path | str = ".") -> dict:
    """Источники контракта → конфиг. primary → треки SoundCloud, secondary → сборники.

    Сопоставление делает софт, а не менеджер: контракт нейтрален и не знает, что у
    Музыки два потока с разной ценой публикации."""
    sources = read_sources(project_dir)
    applied: dict = {}
    if sources.get("primary"):
        discovery = raw.setdefault("soundcloud", {}).setdefault("discovery", {})
        discovery["sources"] = sources["primary"]
        applied["soundcloud.discovery.sources"] = len(sources["primary"])
    if sources.get("secondary"):
        raw.setdefault("youtube_playlists", {})["sources"] = sources["secondary"]
        applied["youtube_playlists.sources"] = len(sources["secondary"])
    if applied:
        get_logger().info("Источники из контракта: %s", applied)
    return applied


def apply_contract(raw: dict, project_dir: Path | str = ".") -> dict:
    """Наложить контракт на сырой конфиг ДО сборки Config. Возвращает применённое.

    Работаем с сырым словарём, а не с готовым Config: у Музыки конфиг — frozen-датакласс,
    и подмена после сборки потребовала бы копии всей структуры."""
    limits = read_contract(project_dir)
    if not limits:
        return {}

    section = raw.setdefault("soundcloud", {})
    applied: dict = {}
    for key in FIELDS:
        if key in limits:
            section[key] = limits[key]
            applied[key] = limits[key]

    get_logger().info("Контракт менеджера применён к трекам: %s", applied)
    return applied
