"""Загрузка видео берёт токен из общего пула сервера — по одному на КАЖДЫЙ ролик."""
from __future__ import annotations

from pathlib import Path

import pytest

import vk_api

from app import vk_token_pool
from app.vk_client import VKClient, VKTokenBusy
from app.vk_token_pool import VkTokenPool


class _FakeUpload:
    """Подменяет vk_api.VkUpload и запоминает, каким токеном шла каждая загрузка."""

    used_tokens: list[str] = []

    def __init__(self, session) -> None:
        self._token = session.token

    def video(self, **kwargs):
        _FakeUpload.used_tokens.append(self._token)
        return {"owner_id": -1, "video_id": 42}


class _FakeSession:
    def __init__(self, token: str) -> None:
        self.token = token

    def get_api(self):
        return object()


@pytest.fixture(autouse=True)
def fake_vk(monkeypatch, tmp_path):
    _FakeUpload.used_tokens = []
    monkeypatch.setattr(vk_api, "VkApi", _FakeSession)
    monkeypatch.setattr(vk_api, "VkUpload", _FakeUpload)
    accounts: dict[str, str] = {}
    monkeypatch.setattr(
        vk_token_pool,
        "_fetch_account_id",
        lambda token: accounts.setdefault(token, f"acc-{len(accounts) + 1}"),
    )
    monkeypatch.setenv("VK_TOKEN_POOL_DB", str(tmp_path / "pool.db"))
    monkeypatch.setenv("VK_TOKEN_POOL_ENV_FILE", str(tmp_path / "absent.env"))


def _pool(monkeypatch, cap: int = 12, gap: int = 0) -> VkTokenPool:
    # gap=0: защита от всплеска — предмет отдельного теста ниже, здесь смотрим ротацию.
    monkeypatch.setenv("VK_UPLOAD_TOKEN_1", "shared-1")
    monkeypatch.setenv("VK_UPLOAD_TOKEN_2", "shared-2")
    return VkTokenPool(
        ["VK_UPLOAD_TOKEN_1", "VK_UPLOAD_TOKEN_2"], daily_cap=cap, min_gap_minutes=gap
    )


def test_without_pool_uses_own_user_token(tmp_path):
    client = VKClient("group", "own-token", 1)

    client.upload_video(Path("x.mp4"), "name", "desc")

    assert _FakeUpload.used_tokens == ["own-token"]


def test_each_upload_takes_a_fresh_token_from_the_pool(monkeypatch):
    client = VKClient("group", "own-token", 1, token_pool=_pool(monkeypatch))

    for _ in range(4):
        client.upload_video(Path("x.mp4"), "name", "desc")

    # Один объект клиента живёт весь запуск — токен обязан меняться на каждый ролик,
    # иначе весь суточный объём уходит в один аккаунт.
    assert _FakeUpload.used_tokens.count("shared-1") == 2
    assert _FakeUpload.used_tokens.count("shared-2") == 2


def test_exhausted_pool_refuses_to_upload(monkeypatch):
    client = VKClient("group", "own-token", 1, token_pool=_pool(monkeypatch, cap=1))
    client.upload_video(Path("x.mp4"), "name", "desc")
    client.upload_video(Path("x.mp4"), "name", "desc")

    with pytest.raises(VKTokenBusy):
        client.upload_video(Path("x.mp4"), "name", "desc")


def test_min_gap_prevents_two_uploads_in_a_row_on_one_account(monkeypatch):
    """Альбом публикуется треками подряд — без зазора это был бы всплеск на аккаунте."""
    client = VKClient("group", "own-token", 1, token_pool=_pool(monkeypatch, gap=10))

    client.upload_video(Path("x.mp4"), "name", "desc")
    client.upload_video(Path("x.mp4"), "name", "desc")

    with pytest.raises(VKTokenBusy):
        client.upload_video(Path("x.mp4"), "name", "desc")

    assert sorted(_FakeUpload.used_tokens) == ["shared-1", "shared-2"]
