"""Гейт состава плейлиста и короткая версия сборника для Telegram.

Оба механизма родились из одного простоя 14–16.08: поиск приносил подборки ЧАСОВЫХ
диджей-миксов, сборник из них физически не собирался (13 часов видео, юнит убивало по
таймауту на склейке), а владелец видел только «сборники не приходят».
"""
from pathlib import Path

import pytest

from app.yt_playlist_db import PLAYLIST_PENDING, PLAYLIST_REJECTED, PlaylistQueue
from app.yt_playlists import DELIVERY_CAPTION
from app.yt_source import (
    proxy_candidates,
    PlaylistEntry,
    PlaylistUnsuitable,
    YouTubeSourceError,
    download_playlist,
    select_entries,
)


def _entries(*durations: int) -> list[PlaylistEntry]:
    return [
        PlaylistEntry(index=i, title=f"трек {i}", duration_s=d)
        for i, d in enumerate(durations, start=1)
    ]


def test_hour_long_mixes_are_dropped():
    """Живой замер плейлиста «CHILL РОССИЯ»: каждый «трек» — час с лишним."""
    chosen = select_entries(_entries(3514, 3435, 3659), max_track_seconds=900)

    assert chosen == []


def test_normal_songs_pass():
    chosen = select_entries(_entries(210, 185, 240), max_track_seconds=900)

    assert [entry.index for entry in chosen] == [1, 2, 3]


def test_total_budget_stops_the_compilation_from_growing():
    """Треки поодиночке проходят, а вместе дают многочасовой ролик — второй рубеж."""
    chosen = select_entries(
        _entries(600, 600, 600, 600), max_track_seconds=900, max_total_seconds=1300
    )

    assert [entry.index for entry in chosen] == [1, 2]


def test_entry_without_duration_is_skipped():
    """У YouTube это недоступный ролик или трансляция — качать неизвестно что нельзя."""
    assert select_entries(_entries(0, 200), max_track_seconds=900)[0].index == 2


def test_long_track_does_not_block_shorter_ones_behind_it():
    """Не берём — но и не обрываем перебор: следующая короткая песня ещё влезает."""
    chosen = select_entries(
        _entries(200, 1200, 200), max_track_seconds=900, max_total_seconds=600
    )

    assert [entry.index for entry in chosen] == [1, 3]


def test_download_refuses_unsuitable_playlist_without_downloading(tmp_path, monkeypatch):
    """Отказ должен стоить один плоский запрос — иначе на диск приедут гигабайты."""
    calls = []

    def fake_list(url, limit):
        calls.append(url)
        return _entries(3514, 3435, 3659)

    monkeypatch.setattr("app.yt_source.list_playlist_entries", fake_list)

    with pytest.raises(PlaylistUnsuitable) as error:
        download_playlist("https://youtube.com/playlist?list=x", tmp_path / "w")

    assert calls == ["https://youtube.com/playlist?list=x"]
    assert "3659" in str(error.value)


def test_rejected_playlist_is_not_revived(tmp_path):
    """`revive_failed` возвращает временные падения. Состав от повторов не меняется —
    отбракованный плейлист обязан остаться за бортом, иначе он вернётся навсегда."""
    queue = PlaylistQueue(tmp_path / "db.sqlite")
    queue.add("https://youtube.com/playlist?list=mix", "Миксы", "канал", "поиск")
    row = queue.next_pending()

    queue.reject(row.id, "часовые миксы")

    assert queue.revive_failed(older_than_hours=0) == 0
    assert queue.next_pending() is None
    queue.close()


def test_failed_playlist_is_still_revived(tmp_path):
    queue = PlaylistQueue(tmp_path / "db.sqlite")
    queue.add("https://youtube.com/playlist?list=ok", "Плейлист", "канал", "поиск")
    row = queue.next_pending()

    queue.bump_attempt(row.id, limit=1, error="занят токен")

    assert queue.revive_failed(older_than_hours=0) == 1
    assert queue.next_pending().status == PLAYLIST_PENDING
    queue.close()


def test_reject_sets_its_own_status(tmp_path):
    queue = PlaylistQueue(tmp_path / "db.sqlite")
    queue.add("https://youtube.com/playlist?list=mix", "Миксы", "канал", "поиск")
    row = queue.next_pending()

    queue.reject(row.id, "часовые миксы")

    stored = queue._conn.execute(
        "SELECT status FROM yt_playlists WHERE id = ?", (row.id,)
    ).fetchone()
    assert stored["status"] == PLAYLIST_REJECTED
    queue.close()


def test_delivered_file_has_no_caption():
    """ТЗ владельца 2026-08-16: «эту хрень не писать». Подпись с меткой и пояснением про
    короткую версию убрана целиком — метка держалась ради перехвата медиа ботом, а сам
    перехват снят 2026-08-14."""
    assert DELIVERY_CAPTION == ""


def test_proxy_candidates_default_to_the_single_configured_exit(monkeypatch):
    """Переменной с портами нет → поведение прежнее, одна попытка."""
    monkeypatch.setenv("YT_PROXY", "socks5://127.0.0.1:10808")
    monkeypatch.delenv("YT_PROXY_PORTS", raising=False)

    assert proxy_candidates() == ["socks5://127.0.0.1:10808"]


def test_proxy_candidates_try_spare_exits_then_direct(monkeypatch):
    """Основной выход первый, дубля нет, прямой путь замыкает: он иногда проходит,
    а «не пробовать вовсе» гарантирует сутки без сборника."""
    monkeypatch.setenv("YT_PROXY", "socks5://127.0.0.1:10808")
    monkeypatch.setenv("YT_PROXY_PORTS", "10808, 10813 ,10811")

    assert proxy_candidates() == [
        "socks5://127.0.0.1:10808",
        "socks5://127.0.0.1:10813",
        "socks5://127.0.0.1:10811",
        None,
    ]


def test_download_switches_exit_when_the_first_one_returns_nothing(tmp_path, monkeypatch):
    """🔴 Живой случай 16.08: выход отдаёт метаданные, но CDN на каждом треке шлёт 403.
    Проверять выход метаданными бесполезно — он ответит «жив», поэтому переключаемся
    именно по пустому результату скачивания."""
    monkeypatch.setenv("YT_PROXY", "socks5://127.0.0.1:10808")
    monkeypatch.setenv("YT_PROXY_PORTS", "10808,10813")
    monkeypatch.setattr("app.yt_source.list_playlist_entries", lambda url, limit: _entries(200, 210, 220, 230, 240))

    used: list[str | None] = []

    class FakeYDL:
        def __init__(self, options):
            used.append(options.get("proxy"))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            if used[-1] == "socks5://127.0.0.1:10808":
                raise RuntimeError("HTTP Error 403: Forbidden")
            return {"entries": [{"id": "a"}]}

    monkeypatch.setattr("app.yt_source.yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr(
        "app.yt_source.collect_tracks",
        # Пять — это минимум по умолчанию: результат обязан быть ПОЛНЫМ сборником,
        # а не просто непустым (см. тест про недобор ниже).
        lambda entries, target: ["трек"] * 5 if entries else [],
    )

    tracks = download_playlist("https://youtube.com/playlist?list=x", tmp_path / "w")

    assert tracks == ["трек"] * 5
    assert used == ["socks5://127.0.0.1:10808", "socks5://127.0.0.1:10813"]


def test_partial_download_is_not_published_as_a_compilation(tmp_path, monkeypatch):
    """🔴 Живой случай 20.08 (запись 308): состав прошёл проверку — 15 годных записей из
    40, — а CDN через прокси отдал РОВНО ОДИН файл. Приёмка была `if tracks:`, и на
    стену ушёл «сборник» из одного трека под заголовком «Лучшие песни 2026».

    Ошибка должна быть ПЕРЕХОДЯЩЕЙ: состав плейлиста хороший, не отдал файлы CDN.
    PlaylistUnsuitable выбросила бы годный источник насовсем."""
    monkeypatch.setenv("YT_PROXY", "socks5://127.0.0.1:10808")
    monkeypatch.setenv("YT_PROXY_PORTS", "10808,10813")
    monkeypatch.setattr(
        "app.yt_source.list_playlist_entries",
        lambda url, limit: _entries(200, 210, 220, 230, 240),
    )

    class FakeYDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            return {"entries": [{"id": "a"}]}

    monkeypatch.setattr("app.yt_source.yt_dlp.YoutubeDL", FakeYDL)
    monkeypatch.setattr("app.yt_source.collect_tracks", lambda entries, target: ["один"])

    with pytest.raises(YouTubeSourceError) as error:
        download_playlist("https://youtube.com/playlist?list=x", tmp_path / "w")

    assert "1 треков при минимуме 5" in str(error.value)


def test_owner_gets_the_description_of_the_file_he_actually_received():
    """🔴 Живая жалоба 16.08: в файле 8 треков, а в описании 15 с таймингами. Владелец
    заливает этот файл на YouTube ВМЕСТЕ с описанием — половина таймингов вела бы в
    пустоту, а последних семи треков в ролике не было бы вовсе."""
    from app.yt_playlists import Compilation

    comp = Compilation(
        video_path=Path("full.mp4"),
        title="Сборник",
        post_text="пост",
        description="полные 15 треков",
        tracks=[],
        delivery_path=Path("short.mp4"),
        delivery_tracks=8,
        delivery_description="первые 8 треков",
    )

    assert comp.description_for_owner == "первые 8 треков"
    assert comp.description == "полные 15 треков"


def test_without_truncation_the_owner_gets_the_same_description():
    from app.yt_playlists import Compilation

    comp = Compilation(
        video_path=Path("full.mp4"),
        title="Сборник",
        post_text="пост",
        description="все треки",
        tracks=[],
    )

    assert comp.description_for_owner == "все треки"


def test_search_phrases_become_hashtags_not_a_dotted_line():
    """ТЗ владельца 2026-08-16: «делать только хэштегами»."""
    from app.seo import build_search_tags

    tags = build_search_tags(["Егор Крид"], ["{q} слушать онлайн"], ["музыка без цензуры"])

    assert tags == ["#егор_крид_слушать_онлайн", "#музыка_без_цензуры"]


def test_search_hashtags_are_capped():
    """Пятнадцать артистов на четыре шаблона дают шестьдесят тегов — это спам."""
    from app.seo import build_search_tags

    tags = build_search_tags(
        [f"Артист {i}" for i in range(20)],
        ["{q} слушать", "{q} скачать", "{q} музыка"],
        limit=10,
    )

    assert len(tags) == 10


def test_already_published_songs_are_skipped():
    """🔴 Жалоба владельца 2026-08-17: «почему в сборниках треки одинаковые». Очередь
    набита выдачей ОДНОГО запроса — разные ссылки, но состав пересекается на две трети."""
    from app.track_naming import track_key

    entries = [
        PlaylistEntry(index=1, title="Егор Крид - MALO 2.0", duration_s=200),
        PlaylistEntry(index=2, title="Xcho - Уйду", duration_s=200),
        PlaylistEntry(index=3, title="Джиган - Худи", duration_s=200),
    ]
    already = {track_key("Егор Крид", "MALO 2.0"), track_key("Xcho", "Уйду")}

    chosen = select_entries(entries, max_track_seconds=900, skip_keys=already)

    assert [entry.index for entry in chosen] == [3]


def test_same_song_twice_in_one_playlist_is_taken_once():
    """Перезаливы одного хита лежат в подборке рядом — в треклисте это выглядит поломкой."""
    entries = [
        PlaylistEntry(index=1, title="Баста - Сансара (Премьера клипа 2024)", duration_s=200),
        PlaylistEntry(index=2, title="Баста — Сансара [аудио]", duration_s=200),
        PlaylistEntry(index=3, title="Кино - Звезда", duration_s=200),
    ]

    chosen = select_entries(entries, max_track_seconds=900)

    assert [entry.index for entry in chosen] == [1, 3]


def test_track_key_ignores_junk_and_case():
    """Один трек на разных каналах подписан по-разному — по сырому тексту не совпадёт."""
    from app.track_naming import track_key

    assert track_key("Егор Крид", "MALO 2.0 (Премьера клипа)") == track_key(
        "егор крид", "malo 2.0!"
    )


def test_playlists_are_taken_in_random_order(tmp_path):
    """ТЗ владельца 2026-08-17: «сборники пускай рандомные берёт». По порядку id очередь
    разбиралась ровно так, как её насыпал поиск — подряд шли соседние результаты одного
    запроса, то есть самые похожие подборки."""
    queue = PlaylistQueue(tmp_path / "db.sqlite")
    for i in range(30):
        queue.add(f"https://youtube.com/playlist?list={i}", f"П{i}", "канал", "поиск")

    picked = {queue.next_pending().url for _ in range(15)}

    assert len(picked) > 1, "выбор не случайный — всегда один и тот же плейлист"
    queue.close()


def test_foreign_ready_made_compilation_is_not_a_track():
    """🔴 Жалоба владельца 2026-08-17: «ты взял чужой сборник уже готовый». Плейлист-донор
    состоял не из песен, а из чужих компиляций — и вышла компиляция компиляций
    «ТОП-9: ТОП 30 ЛУЧШИХ ПЕСЕН РАДИО ENERGY…»."""
    entries = [
        PlaylistEntry(index=1, title="ТОП 30 ЛУЧШИХ ПЕСЕН РАДИО ENERGY | ХИТЫ NRG", duration_s=600),
        PlaylistEntry(index=2, title="Лучшие песни 2026 подряд", duration_s=700),
        PlaylistEntry(index=3, title="Егор Крид - MALO 2.0", duration_s=200),
    ]

    chosen = select_entries(entries, max_track_seconds=900)

    assert [entry.index for entry in chosen] == [3]


def test_remix_is_a_track_not_a_compilation():
    """«mix» только по границам слова: иначе «Песня (Remix)» улетала бы в отсев."""
    entries = [PlaylistEntry(index=1, title="Xcho - Уйду (Remix)", duration_s=200)]

    assert len(select_entries(entries, max_track_seconds=900)) == 1
