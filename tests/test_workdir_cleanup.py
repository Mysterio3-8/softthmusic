"""Брошенные рабочие каталоги: `finally` не выполняется при SIGKILL, а OOM тут штатен."""
from __future__ import annotations

import os
import time

from app.workdir_cleanup import cleanup_stale_workdirs


def _make(work_dir, name: str, age_hours: float):
    directory = work_dir / name
    directory.mkdir(parents=True)
    path = directory / "track.mp3"
    path.write_bytes(b"x" * 10)
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return directory


def test_abandoned_workdir_is_removed(tmp_path):
    stale = _make(tmp_path, "album_1", age_hours=30)

    assert cleanup_stale_workdirs(tmp_path) == 1
    assert not stale.exists()


def test_fresh_workdir_survives(tmp_path):
    fresh = _make(tmp_path, "album_2", age_hours=1)

    assert cleanup_stale_workdirs(tmp_path) == 0
    assert fresh.exists()


def test_active_workdir_is_never_touched(tmp_path):
    """Долгий рендер выглядит старым по mtime самого каталога — активный защищаем явно."""
    active = _make(tmp_path, "pl_7", age_hours=99)

    assert cleanup_stale_workdirs(tmp_path, keep=active) == 0
    assert active.exists()


def test_age_is_taken_from_the_newest_file_inside(tmp_path):
    """mtime папки не меняется от записи в её подпапки — по нему долгая работа
    выглядела бы брошенной и сносилась бы прямо во время рендера."""
    directory = tmp_path / "album_3"
    (directory / "nested").mkdir(parents=True)
    old = directory / "old.mp3"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 99 * 3600,) * 2)
    (directory / "nested" / "fresh.mp4").write_bytes(b"x")

    assert cleanup_stale_workdirs(tmp_path) == 0
    assert directory.exists()


def test_empty_directory_is_garbage(tmp_path):
    (tmp_path / "album_4").mkdir()

    assert cleanup_stale_workdirs(tmp_path) == 1


def test_missing_work_dir_is_not_an_error(tmp_path):
    assert cleanup_stale_workdirs(tmp_path / "нет-такого") == 0
