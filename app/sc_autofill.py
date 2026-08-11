"""Автодобор очереди треков: кончились ссылки — идём искать популярное сами.

ТЗ владельца 2026-08-10 («брать популярные треки, а не только те, что я дал») и жалоба
2026-08-11 («почему в infinity music только сборники публикуются»). Причина жалобы
ровно эта: очередь альбомов наполнялась ТОЛЬКО руками через `soundcloud_cli enqueue`,
ссылки кончились, и в сообществе остались одни сборники с YouTube.

Каждая находка встаёт в очередь **отдельной строкой с `skip_compilation`**: это один
трек, а не релиз, и сборник из одного трека опубликовал бы то же аудио дважды.
Плейлисты, поставленные руками, работают как раньше — сборник, потом треки.

Добор срабатывает по НИЖНЕЙ границе очереди, а не по «пусто»: поиск ходит в сеть и
занимает секунды, а тик короткий. Держим небольшой запас, чтобы пустая очередь не
совпала с недоступностью SoundCloud.
"""
from __future__ import annotations

from app.album_db import AlbumQueue
from app.config import Config
from app.logger import get_logger
from app.sc_discovery import collect_new_tracks


def refill(config: Config, queue: AlbumQueue) -> int:
    """Долить очередь до `target_queue`. Возвращает число добавленных треков."""
    settings = config.soundcloud.discovery
    log = get_logger()

    if not settings.enabled or not settings.sources:
        return 0

    pending = queue.pending_count()
    if pending >= settings.min_queue:
        return 0

    wanted = settings.target_queue - pending
    if wanted <= 0:
        return 0

    found = collect_new_tracks(
        settings.sources,
        queue.known_urls(),
        queue.known_names(),
        wanted=wanted,
        limit_per_source=settings.limit_per_source,
        min_plays=settings.min_plays,
    )
    if not found:
        log.warning("Автопоиск треков: подходящих новинок не нашлось (порог %d прослушиваний)",
                    settings.min_plays)
        return 0

    for ref in found:
        queue.enqueue(
            ref.url,
            title=ref.title,
            artist=ref.artist,
            tracks_total=1,
            chat_id=None,
            skip_compilation=True,
        )
        log.info(
            "Автопоиск: в очередь «%s — %s» (%d прослушиваний)", ref.artist, ref.title, ref.plays
        )
    return len(found)
