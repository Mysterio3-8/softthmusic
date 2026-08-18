"""Жанры кнопки «🎼 Сборник по жанру» приходят из контракта менеджера.

ТЗ владельца 2026-08-18: список должен правиться ИЗ БОТА, а не только в config.yaml —
живой конфиг Музыки не версионируется, и правка руками на сервере теряется при любом
недоразумении с деплоем.
"""
import yaml

from app.manager_contract import apply_genres, read_genres


def _write(tmp_path, payload):
    (tmp_path / "manager_contract.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
    )


def test_no_contract_means_config_stays_untouched(tmp_path):
    raw = {"soundcloud": {"genres": [{"name": "Фонк", "query": "русский фонк"}]}}

    assert apply_genres(raw, tmp_path) == 0
    assert raw["soundcloud"]["genres"] == [{"name": "Фонк", "query": "русский фонк"}]


def test_contract_replaces_the_config_list_entirely(tmp_path):
    """Удалённый в боте жанр обязан исчезнуть с кнопки — merge оставил бы его жить."""
    _write(tmp_path, {"genres": [{"name": "Рэп", "query": "русский рэп"}]})
    raw = {"soundcloud": {"genres": [{"name": "Фонк", "query": "русский фонк"}]}}

    assert apply_genres(raw, tmp_path) == 1
    assert raw["soundcloud"]["genres"] == [{"name": "Рэп", "query": "русский рэп"}]


def test_genre_without_query_searches_by_its_own_name(tmp_path):
    _write(tmp_path, {"genres": [{"name": "Шансон"}]})

    assert read_genres(tmp_path) == [{"name": "Шансон", "query": "Шансон"}]


def test_broken_entries_are_skipped_not_fatal(tmp_path):
    """Битая запись не должна ронять загрузку конфига — софт важнее одного жанра."""
    _write(tmp_path, {"genres": ["строка", {"query": "без имени"}, {"name": "Рок"}]})

    assert read_genres(tmp_path) == [{"name": "Рок", "query": "Рок"}]


def test_broken_yaml_leaves_the_config_alone(tmp_path):
    (tmp_path / "manager_contract.yaml").write_text("genres: [{", encoding="utf-8")
    raw = {"soundcloud": {"genres": [{"name": "Фонк", "query": "русский фонк"}]}}

    assert apply_genres(raw, tmp_path) == 0
    assert raw["soundcloud"]["genres"][0]["name"] == "Фонк"
