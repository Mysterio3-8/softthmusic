"""YouTube без cookies отвечает «Sign in to confirm you're not a bot».

Инцидент 2026-08-11/12: поток сборников стоял полтора суток. Очередь была ПОЛНА
(46 плейлистов), таймер тикал каждые 15 минут, а в журнале на каждый трек шло
«Sign in to confirm you're not a bot» и следом «Ни один трек не скачался».
Проверка срабатывает на серверных IP — с домашнего интернета её не видно.
"""
from __future__ import annotations

from app.yt_source import ytdlp_base_options


def test_cookies_are_passed_when_the_file_exists(tmp_path, monkeypatch):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("YT_COOKIES_FILE", str(cookies))

    assert ytdlp_base_options()["cookiefile"] == str(cookies)


def test_missing_file_does_not_break_downloads(tmp_path, monkeypatch):
    """Часть плейлистов скачивается и без cookies — падать на старте хуже, чем
    работать вполсилы."""
    monkeypatch.setenv("YT_COOKIES_FILE", str(tmp_path / "нет-такого.txt"))

    assert "cookiefile" not in ytdlp_base_options()


def test_unset_variable_keeps_previous_behaviour(monkeypatch):
    monkeypatch.delenv("YT_COOKIES_FILE", raising=False)

    assert "cookiefile" not in ytdlp_base_options()


def test_external_js_runtime_is_always_requested(monkeypatch):
    """YouTube требует решать JS-челлендж подписи; без внешнего движка yt-dlp его
    не проходит. Урок оплачен Минусами."""
    monkeypatch.delenv("YT_COOKIES_FILE", raising=False)

    assert ytdlp_base_options()["js_runtimes"] == {"node": {}}


def test_download_options_carry_the_cookies(tmp_path, monkeypatch):
    """Регрессия: cookies должны доехать именно до СКАЧИВАНИЯ, а не только до поиска —
    падало на скачивании треков."""
    from app import yt_source

    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("YT_COOKIES_FILE", str(cookies))
    seen: dict = {}

    class _FakeYDL:
        def __init__(self, options):
            seen.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            return {"entries": []}

    monkeypatch.setattr(yt_source.yt_dlp, "YoutubeDL", _FakeYDL)
    try:
        yt_source.download_playlist("https://youtube.com/playlist?list=x", tmp_path / "w")
    except yt_source.YouTubeSourceError:
        pass  # треков нет — здесь проверяем только переданные опции

    assert seen["cookiefile"] == str(cookies)
