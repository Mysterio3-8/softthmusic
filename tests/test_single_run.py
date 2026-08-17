"""Замок «один тик за раз».

🔴 Живая поломка 17.08: таймер ходит каждые 15 минут, сборка идёт 25–30 — тики
накладывались, в один рабочий каталог писали три процесса, и `finally` одного удалял
временный файл сегмента, который в этот момент писал другой. ffmpeg падал с «Unable to
re-open output file for shifting data», сборник не собирался.
"""
import time

import pytest

from app.single_run import AlreadyRunning, acquire, release


def test_second_run_is_refused(tmp_path):
    lock = tmp_path / "tick.lock"
    acquire(lock)

    with pytest.raises(AlreadyRunning):
        acquire(lock)


def test_lock_is_free_after_release(tmp_path):
    lock = tmp_path / "tick.lock"
    acquire(lock)
    release(lock)

    acquire(lock)  # не должно бросить


def test_stale_lock_is_taken_over(tmp_path):
    """`finally` при SIGKILL не выполняется, а именно так процесс и умирает от OOM.
    Не протухающий замок остановил бы поток до вмешательства человека."""
    lock = tmp_path / "tick.lock"
    acquire(lock)

    acquire(lock, stale_after=0)  # замок считается брошенным


def test_lock_holds_pid(tmp_path):
    """PID в замке — чтобы по зависшему тику было понятно, кого смотреть."""
    lock = tmp_path / "tick.lock"
    acquire(lock)

    assert lock.read_text().strip().isdigit()


def test_release_without_lock_is_not_an_error(tmp_path):
    """Замок мог забрать протухшим другой тик — это штатная ситуация, не сбой."""
    release(tmp_path / "нет-такого.lock")


def test_fresh_lock_survives_a_moment(tmp_path):
    lock = tmp_path / "tick.lock"
    acquire(lock)
    time.sleep(0.01)

    with pytest.raises(AlreadyRunning):
        acquire(lock)
