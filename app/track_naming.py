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
_TITLE_NOISE = re.compile(
    r"\s*[\(\[](?:[^)\]]*)(?:official|lyric|audio|video|hd|4k|remaster\w*)[^)\]]*[\)\]]",
    re.IGNORECASE,
)
_SEPARATORS = (" — ", " – ", " - ", " | ")


def clean_artist(raw: str) -> str:
    """«Big Baby Tape - Topic» → «Big Baby Tape»."""
    return _TOPIC_SUFFIX.sub("", (raw or "").strip()).strip()


def clean_title(raw: str) -> str:
    """«Песня (Official Video)» → «Песня». Скобки без служебных слов не трогаем."""
    return " ".join(_TITLE_NOISE.sub("", (raw or "").strip()).split())


def split_artist_title(title: str, uploader: str) -> tuple[str, str]:
    """Достаёт исполнителя и название из «Артист — Песня».

    Разделителя нет — берём исполнителя из канала (для «- Topic» это верный ответ,
    а у альбома SoundCloud загрузчик и есть артист)."""
    cleaned = clean_title(title)
    for separator in _SEPARATORS:
        artist, found, name = cleaned.partition(separator)
        if found and artist.strip() and name.strip():
            return artist.strip(), name.strip()
    return clean_artist(uploader), cleaned
