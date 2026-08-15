"""Контракт менеджера: настройки из бота реально меняют поведение Музыки.

До сих пор бот писал `manager_contract.yaml` в пустоту — софт его не читал, и «полное
управление из бота» оставалось декларацией.
"""
from app.manager_contract import apply_contract, read_contract


def _write(tmp_path, text: str):
    (tmp_path / "manager_contract.yaml").write_text(text, encoding="utf-8")


def test_no_contract_leaves_config_untouched(tmp_path):
    raw = {"soundcloud": {"max_posts_per_day": 3, "min_interval_minutes": 150}}

    assert apply_contract(raw, tmp_path) == {}
    assert raw["soundcloud"]["max_posts_per_day"] == 3


def test_contract_overrides_config(tmp_path):
    """Контракт сильнее config.yaml — иначе кнопки в боте были бы декорацией."""
    _write(
        tmp_path,
        "limits:\n  max_posts_per_day: 5\n  min_interval_minutes: 120\n"
        "  max_interval_minutes: 180\n  quiet_start_hour: 9\n  quiet_end_hour: 0\n",
    )
    raw = {"soundcloud": {"max_posts_per_day": 3, "min_interval_minutes": 150}}

    apply_contract(raw, tmp_path)

    assert raw["soundcloud"]["max_posts_per_day"] == 5
    assert raw["soundcloud"]["min_interval_minutes"] == 120
    assert raw["soundcloud"]["quiet_start_hour"] == 9


def test_compilations_keep_their_own_limits(tmp_path):
    """У сборников другая цена публикации (15 треков + склейка) и свой темп —
    общий лимит съедал бы квоту одного потока другим."""
    _write(tmp_path, "limits:\n  max_posts_per_day: 5\n")
    raw = {"soundcloud": {}, "youtube_playlists": {"max_posts_per_day": 2}}

    apply_contract(raw, tmp_path)

    assert raw["youtube_playlists"]["max_posts_per_day"] == 2


def test_broken_contract_does_not_stop_publishing(tmp_path):
    """Сломанный контракт — это настройка, а не разрешение работать."""
    _write(tmp_path, "limits: [это не словарь\n")
    raw = {"soundcloud": {"max_posts_per_day": 3}}

    assert apply_contract(raw, tmp_path) == {}
    assert raw["soundcloud"]["max_posts_per_day"] == 3


def test_empty_limits_change_nothing(tmp_path):
    _write(tmp_path, "limits: {}\n")

    assert read_contract(tmp_path) == {}
