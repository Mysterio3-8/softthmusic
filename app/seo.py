"""SEO-обвязка публикаций Infinity Music.

Цель владельца (ТЗ 2026-08-10): «чтобы даже мой бот в тг выходил в поисковой строке —
мы по сути делаем внешние ссылки на моего бота через ВК». Механика такая:

* запись VK и видеозапись VK — публичные страницы, их индексируют Google и Яндекс;
* в каждой стоит ссылка на `t.me/muz_damn_bot` — это внешняя ссылка на бота, по ней
  бот и «подтягивается» в выдаче по нашим запросам;
* тег `#артист@группа` работает витриной внутри VK: тап показывает только наши записи.

Отсюда разделение: у записи короткий текст и немного тегов, у видеозаписи — большое
описание с поисковыми фразами и полным набором тегов (в ленте оно свёрнуто).

Артист и название приходят из метаданных отдельными полями, поэтому частотный разбор
текста, как в Новостях, здесь не нужен — ключи уже структурные.
"""
from __future__ import annotations

import re

VK_VIDEO_DESCRIPTION_LIMIT = 4000

_TAG_JUNK = re.compile(r"[^\w]", re.UNICODE)
_UNDERSCORE_RUN = re.compile(r"_{2,}")


def slugify(phrase: str) -> str:
    """«Big Baby Tape» → «big_baby_tape». В теге VK живут только буквы, цифры и `_`."""
    lowered = phrase.strip().lower().replace("ё", "е").replace(" ", "_")
    return _UNDERSCORE_RUN.sub("_", _TAG_JUNK.sub("", lowered)).strip("_")


def build_hashtags(subjects: list[str], base_tags: list[str], group: str, limit: int) -> list[str]:
    """Теги по артисту/треку/альбому + постоянные теги сообщества, без повторов.

    Конкретика впереди: при маленьком лимите постоянные теги вытеснили бы имя
    артиста — то единственное, что человек реально набирает в поиске."""
    seen: set[str] = set()
    tags: list[str] = []
    for phrase in [*subjects, *base_tags]:
        slug = slugify(phrase)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        tags.append(f"#{slug}@{group}" if group else f"#{slug}")
        if len(tags) >= limit:
            break
    return tags


def build_search_line(
    subjects: list[str], phrases: list[str], channel_phrases: list[str] | None = None
) -> str:
    """«Big Baby Tape слушать онлайн · … · музыка без цензуры · инфинити мьюзик».

    Фразами, а не отдельными словами: поисковики ранжируют точное вхождение запроса
    выше, чем совпадение слов вразнобой.

    Два сорта фраз, и путать их нельзя:

    * `phrases` — шаблоны с `{q}` вокруг артиста и релиза. Приводят тех, кто ищет
      конкретный трек, но работают только когда артист известен.
    * `channel_phrases` — постоянные запросы САМОГО сообщества («музыка без цензуры»,
      «инфинити музыка»). ТЗ владельца 2026-08-11: «чтобы люди писали музыка без цензуры
      или инфинити музыка — и мой канал выходил». По ним ищут гораздо чаще, чем по имени
      артиста, и они не зависят от того, разобрались ли метаданные, поэтому идут в КАЖДОЙ
      публикации — в том числе там, где артист не определился и `phrases` пусты."""
    built = [
        template.format(q=subject)
        for subject in subjects
        if subject.strip()
        for template in phrases
    ]
    built.extend(phrase for phrase in (channel_phrases or []) if phrase.strip())
    return " · ".join(dict.fromkeys(built))


def build_video_description(
    *,
    header: str,
    body: str,
    subjects: list[str],
    phrases: list[str],
    base_tags: list[str],
    group: str,
    tag_limit: int,
    service_block: str = "",
    channel_phrases: list[str] | None = None,
    limit: int = VK_VIDEO_DESCRIPTION_LIMIT,
) -> str:
    """Описание видеозаписи: шапка → треклист/текст → поисковые фразы → сервис → теги.

    Порядок неслучаен: первые ~150 знаков уходят в сниппет поисковика, там должен быть
    человеческий текст с названием релиза. Теги — в самом низу, внутреннему поиску VK
    их позиция безразлична."""
    blocks = [
        header.strip(),
        body.strip(),
        build_search_line(subjects, phrases, channel_phrases),
        service_block.strip(),
        " ".join(build_hashtags(subjects, base_tags, group, tag_limit)),
    ]
    text = "\n\n".join(block for block in blocks if block.strip()).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"
