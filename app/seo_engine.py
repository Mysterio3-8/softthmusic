"""SEO-движок сборников: название, описание и ключи из параметров подборки.

ТЗ владельца 2026-08-18: «сделай не набор шаблонов, а SEO-конструктор» — у сборника есть
жанр, год, количество треков, артисты, настроение и ситуация, и из них софт собирает
полный пакет: заголовок + описание + ключевые слова.

Почему конструкции, а не список готовых названий. Двести статичных строк кончаются на
двухсотом сборнике, и дальше начинаются повторы — ровно та жалоба, с которой всё
началось («у плейлистов одинаковые название одни и те же», 2026-08-11). Тридцать
конструкций плюс словари дают тысячи непохожих названий и, главное, растут от правки
словаря, а не кода.

Три правила, которые движок обязан соблюдать (и на каждое есть тест):

* **не врать.** Артист попадает в заголовок, только если он есть в треклисте; число
  треков — только настоящее. Заголовок «Macan, Miyagi и другие» на сборнике без них —
  это обман читателя и повод для жалобы, а не SEO;
* **не спамить.** «РЭП 2026 РУССКИЙ РЭП ЛУЧШИЙ РЭП НОВИНКИ RAP HIP HOP» — набор запросов,
  а не название. Заголовок проверяется на повторы слов и длину;
* **не повторяться.** Уже выходившие названия известны (`recent_titles`), и конструкция,
  дающая такое же, отбраковывается.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field, replace

TITLE_ARTIST_LIMIT = 3
"""Сколько имён влезает в заголовок. Дальше — «и другие»: пять имён читаются как свалка
и обрезаются лентой VK на середине перечисления."""

TITLE_LENGTH_LIMIT = 100
"""Потолок заголовка. Длиннее VK и поиск всё равно обрежут, а обрезанный хвост — это
потерянный ключ и рваная фраза в ленте."""

WORD_REPEAT_LIMIT = 2
"""Сколько раз одно и то же слово может встретиться в заголовке. Три «рэпа» подряд —
это уже переспам, по которому поиск понижает, а человек не кликает."""

KEYWORDS_LIMIT = 20


@dataclass(frozen=True)
class GenreProfile:
    """Словарь одного жанра. Правится данными, а не кодом — в этом весь смысл базы."""

    label: str
    """Как жанр называется в заголовке: «Русский рэп», «Фонк»."""
    genitive: str = ""
    """Родительный падеж: «Лучшие треки РУССКОГО РЭПА». Без него конструкции с
    зависимым словом дают «Лучшие треки русский рэп» — это читается как машинный
    перевод и убивает клик. Пусто → такие конструкции не участвуют."""
    aliases: tuple[str, ...] = ()
    """По каким строкам жанр узнаётся: поисковый запрос владельца («русский рэп»),
    имя кнопки («Рэп»), латиница («rap»)."""
    moods: tuple[str, ...] = ()
    situations: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    """Базовые запросы жанра. Длинный хвост движок достроит сам."""


GENRES: tuple[GenreProfile, ...] = (
    GenreProfile(
        label="Русский рэп",
        genitive="русского рэпа",
        aliases=("рэп", "реп", "русский рэп", "русский реп", "rap", "russian rap"),
        moods=("Энергичные треки", "Жёсткий бит", "Атмосферный вечер"),
        situations=("для машины", "для тренировок", "для вечеринки", "для дороги"),
        keywords=(
            "русский рэп", "рэп новинки", "лучший рэп", "русский хип хоп",
            "рэп для машины", "популярный рэп", "russian rap",
        ),
    ),
    GenreProfile(
        label="Хип-хоп",
        genitive="хип-хопа",
        aliases=("хип-хоп", "хип хоп", "hip-hop", "hip hop", "русский хип хоп"),
        moods=("Энергичные треки", "Классика жанра"),
        situations=("для машины", "для тренировок", "для вечеринки"),
        keywords=("хип хоп", "hip hop", "рэп и хип хоп", "лучший хип хоп", "hip hop новинки"),
    ),
    GenreProfile(
        label="Фонк",
        genitive="фонка",
        aliases=("фонк", "phonk", "русский фонк", "drift phonk", "дрифт фонк"),
        moods=("Энергичные треки", "Тёмный вайб"),
        situations=("для машины", "для дороги", "для тренировок"),
        keywords=(
            "фонк", "русский фонк", "phonk", "фонк для машины", "дрифт фонк",
            "фонк новинки", "best phonk",
        ),
    ),
    GenreProfile(
        label="Trap",
        genitive="trap",
        aliases=("trap", "трэп", "трап", "русский trap"),
        moods=("Энергичные треки", "Тёмный вайб"),
        situations=("для машины", "для тренировок"),
        keywords=("trap", "русский trap", "trap music", "best trap", "трэп новинки"),
    ),
    GenreProfile(
        label="Поп",
        genitive="русского попа",
        aliases=("поп", "pop", "русский поп", "русский поп хиты", "попса"),
        moods=("Лёгкое настроение", "Хиты, которые все знают"),
        situations=("для дороги", "для вечеринки", "на каждый день"),
        keywords=("русский поп", "поп музыка", "популярные песни", "хиты", "pop 2026"),
    ),
    GenreProfile(
        label="Рок",
        genitive="русского рока",
        aliases=("рок", "rock", "русский рок"),
        moods=("Живой звук", "Энергичные треки"),
        situations=("для дороги", "для вечера"),
        keywords=("русский рок", "рок музыка", "лучший рок", "rock", "рок хиты"),
    ),
    GenreProfile(
        label="Шансон",
        genitive="шансона",
        aliases=("шансон", "chanson", "русский шансон"),
        moods=("Для души", "Спокойный вечер"),
        situations=("для дороги", "для вечера"),
        keywords=("русский шансон", "шансон", "шансон новинки", "лучший шансон"),
    ),
    GenreProfile(
        label="Клубная музыка",
        genitive="клубной музыки",
        aliases=("клубный", "клубная", "club", "клубный микс", "танцевальный", "dance"),
        moods=("Энергичные треки", "Танцпол"),
        situations=("для вечеринки", "для тренировок"),
        keywords=("клубная музыка", "танцевальная музыка", "club mix", "музыка для вечеринки"),
    ),
    GenreProfile(
        label="Лирика",
        genitive="лирики",
        aliases=("лирика", "лирические", "лирический", "грустные песни"),
        moods=("Для души", "Спокойный вечер"),
        situations=("для вечера", "для дороги"),
        keywords=("лирические песни", "песни для души", "грустные песни", "спокойная музыка"),
    ),
    GenreProfile(
        label="Атмосферная музыка",
        genitive="атмосферной музыки",
        aliases=("атмосферный", "атмосферная", "chill", "чилл", "ambient"),
        moods=("Спокойный вечер", "Фон для работы"),
        situations=("для вечера", "для работы", "для дороги"),
        keywords=("атмосферная музыка", "chill музыка", "музыка для работы", "спокойная музыка"),
    ),
)

DEFAULT_PROFILE = GenreProfile(
    label="Музыка",
    genitive="музыки",
    moods=("Лучшее за месяц", "Хиты, которые все знают"),
    situations=("для дороги", "на каждый день"),
    keywords=("музыка", "лучшие треки", "новинки музыки", "популярные песни"),
)
"""Запас на жанр, которого нет в базе: владелец добавляет жанры из бота, и незнакомое
имя обязано давать нормальный заголовок, а не пустое место."""


# --- конструкции заголовков ---------------------------------------------------
#
# Слоты: {genre} {year} {count} {artists} {mood} {situation}. Конструкция, которой не
# хватило данных, просто не участвует — поэтому список можно пополнять свободно.

UNIVERSAL_TITLES = (
    "{genre} {year} — Лучшие треки | Новинки и хиты",
    "Лучший {genre_lower} {year} — Популярные песни и новинки",
    "{genre} — Лучшие песни {year} | Популярные треки",
    "{genre} {year} — Новинки, хиты и лучшие треки",
    "Популярный {genre_lower} {year} — Лучшие песни",
    "{genre} {year} — Что слушают прямо сейчас",
    "Музыка {year} — Лучший {genre_lower} плейлист",
)

NOVELTY_TITLES = (
    "Новинки {genre_gen} {year} — Новые треки и лучшие песни",
    "{genre} Новинки — Новая музыка {year}",
    "Свежий {genre_lower} {year} — Новинки и хиты",
    "{genre} — Новые треки {year} | Лучшие релизы",
)

BEST_TITLES = (
    "Лучшие треки {genre_gen} {year}",
    "Лучшие песни {genre_gen} {year} | Популярное",
    "ТОП {genre_gen} {year} — Самые популярные треки",
    "{genre} — ТОП треков {year}",
)

QUANTITY_TITLES = (
    "{count} лучших треков {genre_gen} {year}",
    "{count} песен {genre_gen} — Лучший плейлист {year}",
    "{genre} {year} — {count} треков нон-стоп",
)

MOOD_TITLES = (
    "{genre} {year} — {mood}",
    "{mood} — {genre} {year}",
    "{genre} — {mood} | Лучшие треки {year}",
)

SITUATION_TITLES = (
    "{genre} {situation} — Лучшие треки {year}",
    "Музыка {situation} {year} — {genre} и новинки",
    "{genre} {situation} {year} — Популярные песни",
)

ARTIST_TITLES = (
    "{genre} {year} — {artists}",
    "{artists} — Лучший {genre_lower} {year}",
    "{genre} {year} — Хиты {artists}",
)

TITLE_GROUPS = (
    UNIVERSAL_TITLES,
    NOVELTY_TITLES,
    BEST_TITLES,
    QUANTITY_TITLES,
    MOOD_TITLES,
    SITUATION_TITLES,
    ARTIST_TITLES,
)

DESCRIPTION_TEMPLATES = (
    "Подборка лучших треков в жанре {genre_lower} за {year} год: новинки, популярные "
    "песни и хиты. Подойдёт {situation} и на каждый день.{artists_part}",
    "Лучшие песни в стиле {genre_lower} {year} — новинки, популярные треки и хиты "
    "в одном плейлисте. Музыка {situation}, для дороги и фоном.{artists_part}",
    "Большая подборка {genre_lower} {year} — {count} треков подряд, без рекламы внутри. "
    "Новинки, лучшие песни и то, что слушают прямо сейчас.{artists_part}",
)
"""Описание НЕ начинается с заголовка: он и так стоит строкой выше, и повтор читался
как «Новые треки… — Лучшие релизы — подборка лучших треков…».

Артисты идут отдельным предложением («В сборнике: …»), а не в родительном падеже внутри
фразы: «хиты Macan, Miyagi, Скриптонит» грамматически неверно, а склонять имена
исполнителей нечем."""


@dataclass(frozen=True)
class SeoParams:
    """Параметры сборника. Всё, кроме жанра, необязательно — движок обойдётся."""

    genre: str = ""
    """Как жанр пришёл: имя кнопки («Фонк») или поисковый запрос («русский фонк»)."""
    artists: tuple[str, ...] = ()
    """ТОЛЬКО реальные исполнители сборника. Пусто → конструкции с именами не участвуют."""
    count: int = 0
    year: int = 0
    mood: str = ""
    situation: str = ""


@dataclass(frozen=True)
class SeoPack:
    """Готовый пакет: заголовок, описание, ключи."""

    title: str
    description: str
    keywords: tuple[str, ...] = field(default_factory=tuple)


def find_profile(genre: str) -> GenreProfile:
    """Жанр → словарь. Узнаём по алиасу и по вхождению: жанр приходит и кнопкой
    («Фонк»), и поисковым запросом («русский фонк»), и это одно и то же."""
    text = (genre or "").strip().casefold()
    if not text:
        return DEFAULT_PROFILE
    for profile in GENRES:
        if text in profile.aliases or profile.label.casefold() == text:
            return profile
    for profile in GENRES:
        if any(alias in text for alias in profile.aliases):
            return profile
    return DEFAULT_PROFILE


def _artists_phrase(artists: tuple[str, ...]) -> str:
    names = [name.strip() for name in artists if name and name.strip()][:TITLE_ARTIST_LIMIT]
    if not names:
        return ""
    phrase = ", ".join(names)
    return f"{phrase} и другие" if len(artists) > len(names) else phrase


_WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")


def looks_spammy(title: str) -> bool:
    """Заголовок похож на набор запросов, а не на название.

    Два признака, оба из живых примеров: слово повторяется больше двух раз («РЭП …
    РЭП … РЭП») и длина за пределом, после которого лента всё равно обрежет."""
    if len(title) > TITLE_LENGTH_LIMIT:
        return True
    words = [word.casefold() for word in _WORD.findall(title)]
    return any(words.count(word) > WORD_REPEAT_LIMIT for word in set(words))


def _render(template: str, params: SeoParams, profile: GenreProfile) -> str:
    label = profile.label
    text = template.format(
        genre=label,
        genre_lower=label[:1].lower() + label[1:],
        genre_gen=profile.genitive,
        year=params.year or "",
        count=params.count or "",
        artists=_artists_phrase(params.artists),
        mood=params.mood or (profile.moods[0] if profile.moods else ""),
        situation=params.situation or (profile.situations[0] if profile.situations else ""),
    )
    return " ".join(text.split())


def _usable(template: str, params: SeoParams, profile: GenreProfile) -> bool:
    """Хватает ли данных конструкции. Ключевое правило — НЕ ВРАТЬ: нет артистов в
    треклисте → конструкция с именами не участвует, нет числа треков → не участвует
    конструкция с числом."""
    if "{artists}" in template and not _artists_phrase(params.artists):
        return False
    if "{genre_gen}" in template and not profile.genitive:
        return False
    if "{count}" in template and params.count <= 0:
        return False
    if "{year}" in template and not params.year:
        return False
    if "{mood}" in template and not (params.mood or profile.moods):
        return False
    if "{situation}" in template and not (params.situation or profile.situations):
        return False
    return True


def _with_flavour(params: SeoParams, profile: GenreProfile, rng: random.Random) -> SeoParams:
    """Досыпает настроение и ситуацию из словаря жанра, если их не задали снаружи."""
    mood = params.mood or (rng.choice(list(profile.moods)) if profile.moods else "")
    situation = params.situation or (
        rng.choice(list(profile.situations)) if profile.situations else ""
    )
    return replace(params, mood=mood, situation=situation)


def build_title(
    params: SeoParams,
    recent: tuple[str, ...] = (),
    rng: random.Random | None = None,
) -> str:
    """Заголовок сборника: годная конструкция, которой ещё не пользовались.

    Порядок отбора: сначала выбрасываем конструкции без данных, потом спамные, потом
    уже выходившие. Не осталось ни одной свежей — берём любую годную: сборник с
    повторным названием лучше, чем сборник без названия."""
    rng = rng or random.Random()
    profile = find_profile(params.genre)
    # Настроение и ситуацию выбираем ЖРЕБИЕМ, а не берём первые из словаря: иначе все
    # сборники жанра выходили бы «для машины», и половина конструкций схлопнулась бы в
    # одно название — ровно то, ради чего движок и писался.
    params = _with_flavour(params, profile, rng)
    templates = [
        template
        for group in TITLE_GROUPS
        for template in group
        if _usable(template, params, profile)
    ]
    rendered = [_render(template, params, profile) for template in templates]
    sane = [title for title in rendered if title and not looks_spammy(title)]
    if not sane:
        return f"{profile.label} {params.year}".strip()
    fresh = [title for title in sane if title not in recent]
    return rng.choice(fresh or sane)


def build_description(
    params: SeoParams,
    title: str,
    rng: random.Random | None = None,
) -> str:
    """Описание сборника — человеческим текстом, а не перечислением ключей.

    Артисты называются те же, что в заголовке, и только настоящие: описание читают
    и поисковики, и люди, а несуществующее имя портит и то, и другое."""
    rng = rng or random.Random()
    profile = find_profile(params.genre)
    artists = _artists_phrase(params.artists)
    templates = [
        template
        for template in DESCRIPTION_TEMPLATES
        if "{count}" not in template or params.count > 0
    ]
    template = rng.choice(templates or list(DESCRIPTION_TEMPLATES))
    label = profile.label
    text = template.format(
        title=title,
        genre_lower=label[:1].lower() + label[1:],
        year=params.year or "",
        count=params.count or "",
        artists_part=f" В сборнике: {artists}." if artists else "",
        situation=params.situation or (profile.situations[0] if profile.situations else "на каждый день"),
    )
    return " ".join(text.split())


def build_keywords(params: SeoParams, limit: int = KEYWORDS_LIMIT) -> tuple[str, ...]:
    """Ключи сборника: база жанра + длинный хвост с годом, ситуацией и артистами.

    Длинный хвост даёт то, чего не даёт голый «рэп»: по «рэп для машины 2026» ищут
    реже, но конкуренция там кратно ниже, и такой запрос приводит к нам, а не к
    площадкам первой десятки."""
    profile = find_profile(params.genre)
    year = str(params.year) if params.year else ""
    keys: list[str] = []
    for key in profile.keywords:
        keys.append(key)
        if year:
            keys.append(f"{key} {year}")
    for situation in profile.situations[:2]:
        base = profile.keywords[0] if profile.keywords else profile.label.casefold()
        keys.append(f"{base} {situation}".strip())
    for artist in params.artists[:3]:
        keys.append(artist.casefold())
        if year:
            keys.append(f"{artist.casefold()} {year}")
    if year:
        keys.append(f"лучшие треки {year}")
        keys.append(f"новинки музыки {year}")
    unique = list(dict.fromkeys(key.strip() for key in keys if key.strip()))
    return tuple(unique[:limit])


def build_pack(
    params: SeoParams,
    recent: tuple[str, ...] = (),
    rng: random.Random | None = None,
) -> SeoPack:
    """Полный SEO-пакет одного сборника: заголовок, описание, ключи."""
    rng = rng or random.Random()
    title = build_title(params, recent, rng)
    return SeoPack(
        title=title,
        description=build_description(params, title, rng),
        keywords=build_keywords(params),
    )
