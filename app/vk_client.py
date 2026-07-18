from __future__ import annotations

from datetime import datetime
from pathlib import Path

import vk_api

from app.logger import get_logger


class VKError(Exception):
    """Ошибка загрузки видео или создания отложенной записи в VK."""


class VKClient:
    def __init__(self, token: str, group_id: int) -> None:
        self._group_id = group_id
        self._session = vk_api.VkApi(token=token)
        self._api = self._session.get_api()
        self._upload = vk_api.VkUpload(self._session)

    def upload_video(self, file_path: Path, name: str, description: str) -> str:
        """Загружает видео в сообщество. Возвращает attachment вида videoOWNER_ID."""
        try:
            saved = self._upload.video(
                video_file=str(file_path),
                name=name[:128] or "video",
                description=description,
                group_id=self._group_id,
                wallpost=0,
            )
        except Exception as exc:  # noqa: BLE001 — граница внешнего API
            raise VKError(f"Не удалось загрузить видео в VK: {exc}") from exc

        owner_id = saved.get("owner_id")
        video_id = saved.get("video_id")
        if owner_id is None or video_id is None:
            raise VKError(f"VK не вернул owner_id/video_id: {saved}")
        return f"video{owner_id}_{video_id}"

    def schedule_post(self, message: str, attachment: str, publish_at: datetime) -> int:
        """Создаёт отложенную запись на стене сообщества. Возвращает post_id."""
        try:
            response = self._api.wall.post(
                owner_id=-self._group_id,
                from_group=1,
                message=message,
                attachments=attachment,
                publish_date=int(publish_at.timestamp()),
            )
        except Exception as exc:  # noqa: BLE001 — граница внешнего API
            raise VKError(f"Не удалось создать отложенную запись: {exc}") from exc

        post_id = response.get("post_id")
        get_logger().info(
            "Отложенная запись создана: post_id=%s на %s", post_id, publish_at.isoformat()
        )
        return post_id
