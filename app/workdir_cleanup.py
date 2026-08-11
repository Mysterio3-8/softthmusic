"""Уборка брошенных рабочих каталогов.

Тик удаляет за собой сам (`shutil.rmtree` в `finally`), но `finally` НЕ выполняется при
SIGKILL — а именно так процесс и умирает от OOM на этом VPS (961 МБ RAM, ffmpeg рендерит
видео на каждый трек). После такой смерти в `downloads/` остаётся каталог со скачанными
mp3 и отрендеренными mp4 — сотни мегабайт, которые уже никому не нужны и которые никто
не удалит: следующий тик работает под НОВЫМ id и о старом каталоге не знает.

Ровно тем же способом забился диск у Новостей (94%, инцидент 2026-07-28) — там это
чинили отдельной уборкой по возрасту. Здесь то же лекарство.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from app.logger import get_logger

STALE_HOURS = 12
"""Через сколько часов каталог считается брошенным.

Один тик — это максимум час работы ffmpeg (15 треков на одном ядре). Двенадцать часов
дают десятикратный запас: живой каталог под это правило не попадёт, а осиротевший
уберётся в тот же день, а не через неделю."""


def cleanup_stale_workdirs(
    work_dir: Path, *, keep: Path | None = None, stale_hours: int = STALE_HOURS
) -> int:
    """Удалить подкаталоги старше порога. `keep` — каталог текущей работы, его не трогаем.

    Возраст берём по самому свежему файлу внутри, а не по самому каталогу: mtime папки
    не меняется от записи в её подпапки, и долгая работа выглядела бы брошенной."""
    if not work_dir.exists():
        return 0

    deadline = time.time() - stale_hours * 3600
    removed = 0
    for item in work_dir.iterdir():
        if not item.is_dir() or (keep is not None and item == keep):
            continue
        if _newest_mtime(item) >= deadline:
            continue
        shutil.rmtree(item, ignore_errors=True)
        removed += 1

    if removed:
        get_logger().warning(
            "Уборка: удалено брошенных рабочих каталогов в %s: %d", work_dir, removed
        )
    return removed


def _newest_mtime(directory: Path) -> float:
    """Самый свежий файл внутри. Пустой каталог считаем древним — он и есть мусор."""
    times = [path.stat().st_mtime for path in directory.rglob("*") if path.is_file()]
    return max(times) if times else 0.0
