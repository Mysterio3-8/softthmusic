import pytest

from app.config import ConfigError, _build_config, _normalize_times


def _raw():
    return {
        "vk": {"group_id": 555},
        "youtube": {"channels": ["https://youtube.com/@a/videos"], "max_height": 480},
        "publishing": {"posts_per_day": 3, "times": ["09:00", "15:00", "21:00"]},
        "ad_block": "Реклама",
        "retry": {"delays_minutes": [60, 180]},
        "paths": {},
    }


def test_build_config_ok():
    config = _build_config(_raw(), "group_tok", "user_tok")
    assert config.vk_group_token == "group_tok"
    assert config.vk_user_token == "user_tok"
    assert config.group_id == 555
    assert config.owner_id == -555
    assert config.publish_times == ["09:00", "15:00", "21:00"]


def test_missing_group_id_raises():
    raw = _raw()
    raw["vk"] = {}
    with pytest.raises(ConfigError):
        _build_config(raw, "group_tok", "user_tok")


def test_empty_channels_raises():
    raw = _raw()
    raw["youtube"]["channels"] = []
    with pytest.raises(ConfigError):
        _build_config(raw, "group_tok", "user_tok")


def test_normalize_times_truncates_extra():
    assert _normalize_times(["09:00", "15:00", "21:00"], 2) == ["09:00", "15:00"]


def test_normalize_times_fills_missing():
    result = _normalize_times(["09:00"], 3)
    assert len(result) == 3
    assert result[0] == "09:00"


def test_normalize_times_defaults_when_empty():
    result = _normalize_times([], 3)
    assert len(result) == 3
    assert result[0] == "09:00"
