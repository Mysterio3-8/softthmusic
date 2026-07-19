from datetime import datetime, timezone
from pathlib import Path

from app import publisher
from app.config import Config
from app.database import Database


def _config(channels):
    return Config(
        vk_group_token="g",
        vk_user_token="u",
        group_id=1,
        channels=channels,
        max_height=480,
        posts_per_day=3,
        posts_per_run=1,
        publish_times=["09:00", "15:00", "21:00"],
        ad_block="ad",
        retry_delays_minutes=[60, 180],
        database_path=Path("x"),
        downloads_dir=Path("d"),
        log_path=Path("l"),
    )


def test_selects_across_channels_in_order(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    listings = {
        "chan1": ["a", "b"],
        "chan2": ["c", "d"],
    }
    monkeypatch.setattr(publisher, "list_channel_video_ids", lambda url: listings[url])

    config = _config(["chan1", "chan2"])
    result = publisher.select_candidates(config, db, 3, datetime.now(timezone.utc))

    assert [c.youtube_id for c in result] == ["a", "b", "c"]
    assert result[2].channel == "chan2"


def test_skips_already_published(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    db.mark_published("a", "chan1", "Title")
    monkeypatch.setattr(publisher, "list_channel_video_ids", lambda url: ["a", "b", "c"])

    config = _config(["chan1"])
    result = publisher.select_candidates(config, db, 2, datetime.now(timezone.utc))

    assert [c.youtube_id for c in result] == ["b", "c"]


def test_broken_channel_does_not_stop_others(tmp_path, monkeypatch):
    from app.youtube import YouTubeError

    db = Database(tmp_path / "t.db")

    def fake_list(url):
        if url == "bad":
            raise YouTubeError("boom")
        return ["c", "d"]

    monkeypatch.setattr(publisher, "list_channel_video_ids", fake_list)

    config = _config(["bad", "good"])
    result = publisher.select_candidates(config, db, 2, datetime.now(timezone.utc))

    assert [c.youtube_id for c in result] == ["c", "d"]
