from __future__ import annotations

from datetime import datetime
from pathlib import Path

import vk_api

from app.logger import get_logger
from app.vk_token_pool import TokenLease, VkTokenPool, token_hash

# Коды VK, означающие проблему с самим аккаунтом, а не с запросом: аккаунт нужно
# выводить из ротации надолго, а не на обычные 90 минут.
BAN_CODES = (5, 17, 29)


class VKError(Exception):
    """Ошибка загрузки видео или создания отложенной записи в VK."""


def _looks_like_ban(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    return code in BAN_CODES


def build_token_pool(config) -> VkTokenPool | None:
    """Пул личных токенов из конфига софта. Пул не настроен → None, и клиент работает
    по-старому, одним VK_USER_TOKEN."""
    if not config.vk_upload_token_envs:
        return None
    return VkTokenPool(
        config.vk_upload_token_envs,
        daily_cap=config.vk_token_daily_cap,
        min_gap_minutes=config.vk_token_min_gap_minutes,
    )


class VKClient:
    """Два токена по назначению, чтобы минимизировать нагрузку на user-токен.

    - user-токен: ТОЛЬКО загрузка видео (`video.save`) — групповой токен это не
      умеет (VK возвращает [5] User authorization failed). 1 вызов на ролик.
    - групповой токен: постинг (`wall.post`) — редкий и безопасный путь, не
      провоцирует бан user-токена частыми записями.

    Видео сохраняется в саму группу (`group_id=...`), поэтому владелец вложения —
    сообщество, и групповой токен корректно прикрепляет его к записи на стене.
    """

    def __init__(
        self,
        group_token: str,
        user_token: str,
        group_id: int,
        token_pool: VkTokenPool | None = None,
    ) -> None:
        self._group_id = group_id
        self._group_api = vk_api.VkApi(token=group_token).get_api()

        user_session = vk_api.VkApi(token=user_token)
        self._user_api = user_session.get_api()
        self._user_token = user_token
        self._token_pool = token_pool

    def _lease_upload_token(self) -> TokenLease:
        """Аккаунт для загрузки очередного ролика. Пул общий на весь сервер, поэтому
        нагрузка делится с Новостями, Кино и Минусами (SPEC_TOKEN_BALANCER.md).
        Пул не задан → прежнее поведение, свой VK_USER_TOKEN."""
        if self._token_pool is None:
            return TokenLease("VK_USER_TOKEN", self._user_token, f"h:{token_hash(self._user_token)}")
        lease = self._token_pool.pick()
        if lease is None:
            raise VKError("Все аккаунты пула выбрали суточный лимит загрузок — публикация отложена")
        return lease

    def upload_video(self, file_path: Path, name: str, description: str) -> str:
        """Загружает видео В ГРУППУ user-токеном. Возвращает attachment video-GID_ID.

        Сессия загрузки строится на КАЖДЫЙ ролик, а не один раз в конструкторе: иначе
        балансер выбрал бы аккаунт единожды за запуск процесса и весь суточный объём
        ушёл бы на него — ровно это и приводит к бану."""
        lease = self._lease_upload_token()
        upload = vk_api.VkUpload(vk_api.VkApi(token=lease.token))
        try:
            saved = upload.video(
                video_file=str(file_path),
                name=name[:128] or "video",
                description=description,
                group_id=self._group_id,
                wallpost=0,
            )
        except Exception as exc:  # noqa: BLE001 — граница внешнего API
            if self._token_pool is not None:
                self._token_pool.record_error(lease, blocked=_looks_like_ban(exc))
            raise VKError(f"Не удалось загрузить видео в VK: {exc}") from exc

        owner_id = saved.get("owner_id")
        video_id = saved.get("video_id")
        if owner_id is None or video_id is None:
            raise VKError(f"VK не вернул owner_id/video_id: {saved}")
        return f"video{owner_id}_{video_id}"

    def schedule_post(self, message: str, attachment: str, publish_at: datetime) -> int:
        """Создаёт отложенную запись на стене сообщества групповым токеном."""
        try:
            response = self._group_api.wall.post(
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

    def post_now(self, message: str, attachment: str) -> int:
        """Публикует запись немедленно (без publish_date).

        Отдельный метод, а не флаг у schedule_post: у «сразу» и «отложить» разная
        семантика и разные вызовы VK, флаг только прятал бы это.
        """
        try:
            response = self._group_api.wall.post(
                owner_id=-self._group_id,
                from_group=1,
                message=message,
                attachments=attachment,
            )
        except Exception as exc:  # noqa: BLE001 — граница внешнего API
            raise VKError(f"Не удалось опубликовать запись: {exc}") from exc

        post_id = response.get("post_id")
        get_logger().info("Запись опубликована сразу: post_id=%s", post_id)
        return post_id
