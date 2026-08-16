"""Разбор «Артист — Песня» из названия ролика/трека. Чистые строковые функции.

Общий модуль, а не часть `yt_source.py`, потому что задача одна и та же на двух
площадках. У YouTube это плейлисты чужих пабликов, у SoundCloud — популярные треки из
поиска: и там, и там `uploader` — это КАНАЛ-ПЕРЕЗАЛИВЩИК, а настоящий исполнитель
зашит в НАЗВАНИЕ.

Живой замер SoundCloud 2026-08-11 — почему это важно: у хита на 12 млн прослушиваний
`uploader = "Русский Рэп"`, а `title = "TARAS - Тебя Нежно Грубо"`. Без разбора пост
вышел бы «🎧 Русский Рэп — TARAS - Тебя Нежно Грубо», и в теги/ключи уехал бы паблик
вместо исполнителя — то есть SEO работало бы на чужое имя.
"""
from __future__ import annotations

import re

# «Big Baby Tape - Topic» — служебный канал автогенерённых аудиодорожек YouTube Music.
# В подпись такое имя ставить нельзя, а исполнитель в нём как раз правильный.
_TOPIC_SUFFIX = re.compile(r"\s*-\s*Topic\s*$", re.IGNORECASE)
# Хвосты в названии, которые в подписи и в теге только мешают.
_NOISE_WORDS = (
    r"official|lyric|audio|video|hd|4k|remaster\w*|"
    r"премьера|премьеры|премьеру|клип\w*|аудио|видео|"
    r"текст\s+песни|слова\s+песни|минус\w*|"
    r"трек\s+\d{4}|новинка|"
    r"prod\.?\s*by|prod\.|музыка\s+\d{4}"
)
"""Слова, по которым скобка или хвост признаются промо-мусором.

ТЗ владельца 2026-08-16 «вычищай» — по живому сборнику: «(ПРЕМЬЕРА КЛИПА 2024)»,
«[аудио 2024]», «КЛИП», «prod by DRZ», «| Премьера трека (Текст песни)». В треклисте
это шум, а в хэштеге — мёртвый ключ, который никто не набирает."""

_TITLE_NOISE = re.compile(rf"\s*[\(\[][^)\]]*(?:{_NOISE_WORDS})[^)\]]*[\)\]]", re.IGNORECASE)

_TAIL_NOISE = re.compile(rf"\s*[|·]\s*[^|·]{{0,60}}?(?:{_NOISE_WORDS})[^|·]*$", re.IGNORECASE)
"""Хвост после вертикальной черты: «Алая | Премьера трека».

⚠️ Дефис и тире сюда НЕ входят, хотя соблазн есть. Именно ими отделяют артиста от
песни — «Егор Крид - MALO 2.0 (ft. …) КЛИП», — и правило с дефисом съедало НАЗВАНИЕ
целиком, оставляя «Егор Крид — Егор Крид» (поймано живым прогоном 16.08). Мусор после
дефиса убирают более узкие правила: `_PROD_TAIL` и `_BARE_NOISE`."""

_BARE_NOISE = re.compile(
    rf"\s+(?:{_NOISE_WORDS})(?:\s+\d{{4}})?\s*$", re.IGNORECASE
)
"""Голое слово в конце без скобок: «… Худи КЛИП», «… Пароль Премьера 2026»."""

_PROD_TAIL = re.compile(r"\s*\bprod\.?\s*by\b.*$", re.IGNORECASE)
"""Кредит продюсера в любом месте хвоста — «Буйно Голова 5 (2 АВТОРИТЕТА) prod by DRZ»."""

_TRAILING_JUNK = re.compile(r"[\s,;:•\-–—|]+$")

_SEPARATORS = (" — ", " – ", " - ", " | ")

_ARTIST_SPLIT = re.compile(r"\s*,\s*|\s+(?:feat\.?|ft\.?|при\s+участии)\s+", re.IGNORECASE)
"""Разделители СПИСКА исполнителей.

⚠️ Амперсанд сюда НЕ входит: «Artik & Asti» — это один дуэт, а не два артиста, и
разрезав его, мы получили бы теги `#artik` и `#asti`, по которым не ищут."""


def clean_artist(raw: str) -> str:
    """«Big Baby Tape - Topic» → «Big Baby Tape»."""
    return _TOPIC_SUFFIX.sub("", (raw or "").strip()).strip()


def clean_title(raw: str) -> str:
    """«Худи (ПРЕМЬЕРА КЛИПА 2024)» → «Худи», «Алая | Премьера трека» → «Алая».

    Скобки БЕЗ служебных слов не трогаем: «(2 АВТОРИТЕТА)» и «(ft. …)» — часть названия,
    а не мусор. Чистка идёт от частного к общему и повторяется, пока строка меняется:
    хвосты бывают вложенными («| Премьера трека (Текст песни)»), и одного прохода мало."""
    value = (raw or "").strip()
    for _ in range(4):
        before = value
        value = _PROD_TAIL.sub("", value)
        value = _TITLE_NOISE.sub("", value)
        value = _TAIL_NOISE.sub("", value)
        value = _BARE_NOISE.sub("", value)
        value = _TRAILING_JUNK.sub("", value)
        if value == before:
            break
    return " ".join(value.split())


def split_artists(raw: str, limit: int = 4) -> list[str]:
    """«Джиган, Artik & Asti, NILETTO» → три отдельных исполнителя.

    ТЗ владельца 2026-08-16: слипшийся тег `#джиган_artik_asti_niletto` — мёртвый ключ,
    по нему никто не ищет, а каждое имя по отдельности ищут постоянно.

    Потолок нужен на треки с длинным списком приглашённых: восемь фитов дали бы восемь
    ключей на один трек и вытеснили бы всех остальных артистов сборника."""
    parts = [part.strip(" -–—&") for part in _ARTIST_SPLIT.split(raw or "")]
    return [part for part in dict.fromkeys(parts) if part][:limit]


def split_artist_title(title: str, uploader: str) -> tuple[str, str]:
    """Достаёт исполнителя и название из «Артист — Песня».

    Разделителя нет — берём исполнителя из канала (для «- Topic» это верный ответ,
    а у альбома SoundCloud загрузчик и есть артист)."""
    # Делим СНАЧАЛА, чистим ПОТОМ. Обратный порядок стоил живой регрессии 16.08: чистка
    # работает по всей строке и способна снести хвост вместе с названием песни, если
    # артист отделён тем же дефисом, что и мусор.
    raw = (title or "").strip()
    for separator in _SEPARATORS:
        artist, found, name = raw.partition(separator)
        if found and artist.strip() and clean_title(name).strip():
            return clean_artist(artist.strip()), clean_title(name)
    return clean_artist(uploader), clean_title(raw)
