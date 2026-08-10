from app.post_builder import build_post_text, build_tracklist

def test_tracklist_accumulates_timecodes():
    tracklist = build_tracklist(["First", "Second", "Third"], [90, 150, 60])

    assert tracklist.splitlines() == [
        "00:00 1. First",
        "01:30 2. Second",
        "04:00 3. Third",
    ]


def test_tracklist_switches_to_hours_on_long_albums():
    tracklist = build_tracklist(["A", "B"], [3600, 60])

    assert tracklist.splitlines()[1] == "1:00:00 2. B"


def test_album_post_keeps_order_title_tracklist_ad():
    text = build_post_text("Album — Artist", "00:00 1. First", "реклама")

    assert text == "Album — Artist\n\n00:00 1. First\n\nреклама"


def test_track_post_without_body_has_no_blank_gap():
    text = build_post_text("Track — Artist", "", "реклама")

    assert text == "Track — Artist\n\nреклама"


def _style(**overrides):
    from app.config import PostStyle

    base = {
        "flag": "🇸🇪",
        "title_suffix": "Без цензуры",
        "listen_label": "♾️ Слушать в Telegram бесплатно и без цензуры:",
        "listen_url": "https://t.me/muz_damn_bot",
        "channel_label": "📢 Канал:",
        "channel_url": "",
        "hashtag_template": "{artist}",
        "hashtag_group": "posthardcore",
        "track_kind": "Single",
        "album_kind": "Album",
    }
    base.update(overrides)
    return PostStyle(**base)


def test_single_post_matches_the_requested_layout():
    from app.post_builder import build_release_text

    text = build_release_text(_style(), "Imminence", "False Light", "Single")

    assert text == (
        "🇸🇪 Imminence — False Light (Single) | Без цензуры\n\n"
        "♾️ Слушать в Telegram бесплатно и без цензуры: https://t.me/muz_damn_bot\n"
        "#imminence@posthardcore"
    )


def test_channel_line_appears_only_when_its_url_is_set():
    from app.post_builder import build_release_text

    text = build_release_text(
        _style(channel_url="https://t.me/tgramuzuka"), "Imminence", "False Light", "Single"
    )

    assert "📢 Канал: https://t.me/tgramuzuka" in text


def test_album_post_carries_the_tracklist_between_header_and_links():
    from app.post_builder import build_release_text

    text = build_release_text(_style(), "Big Baby Tape", "Dragonborn", "Album", "00:00 1. Intro")
    lines = text.splitlines()

    assert lines[0] == "🇸🇪 Big Baby Tape — Dragonborn (Album) | Без цензуры"
    assert "00:00 1. Intro" in lines
    assert lines[-1] == "#big_baby_tape@posthardcore"


def test_release_template_gives_each_track_its_own_hashtag():
    """Главное требование владельца: тег уникален для трека и для альбома."""
    from app.post_builder import build_hashtag

    style = "{artist}_{name}"
    album = build_hashtag(style, "Big Baby Tape", "Dragonborn", "tgmusic")
    first = build_hashtag(style, "Big Baby Tape", "Gimme the Loot", "tgmusic")
    second = build_hashtag(style, "Big Baby Tape", "Bandana", "tgmusic")

    assert album == "#big_baby_tape_dragonborn@tgmusic"
    assert len({album, first, second}) == 3


def test_hashtag_drops_characters_that_would_break_it():
    from app.post_builder import build_hashtag

    assert build_hashtag("{artist}", "Big Baby Tape", "", "tgmusic") == "#big_baby_tape@tgmusic"
    assert build_hashtag("{artist}", "Kizaru feat. Big Baby Tape", "", "tgmusic") == "#kizaru_feat_big_baby_tape@tgmusic"
    # Недопустимый символ выбрасывается, а не заменяется: «A$AP» -> «aap».
    assert build_hashtag("{artist}", "A$AP Rocky", "", "tgmusic") == "#aap_rocky@tgmusic"
    assert build_hashtag("{artist}", "Гуф", "", "tgmusic") == "#гуф@tgmusic"


def test_hashtag_without_group_has_no_at_sign():
    from app.post_builder import build_hashtag

    assert build_hashtag("{artist}", "Imminence", "", "") == "#imminence"


def test_artist_made_of_symbols_gives_no_hashtag_line():
    from app.post_builder import build_release_text

    text = build_release_text(_style(), "???", "Track", "Single")

    assert "#" not in text


def test_empty_links_do_not_leave_blank_lines():
    from app.post_builder import build_release_text

    text = build_release_text(_style(listen_url="", channel_url=""), "Imminence", "X", "Single")

    assert text == "🇸🇪 Imminence — X (Single) | Без цензуры\n\n#imminence@posthardcore"


def test_track_without_artist_keeps_the_name_alone():
    from app.post_builder import build_release_text

    assert build_release_text(_style(), "", "False Light", "Single").splitlines()[0] == (
        "🇸🇪 False Light (Single) | Без цензуры"
    )


def test_dropped_symbol_does_not_leave_double_underscore():
    """«ARGUMENTS & FACTS»: «&» выброшен, но щели после себя оставить не должен."""
    from app.post_builder import build_hashtag

    tag = build_hashtag("{artist}_{name}", "Big Baby Tape", "ARGUMENTS & FACTS", "tgmusic")

    assert tag == "#big_baby_tape_arguments_facts@tgmusic"
    assert "__" not in tag
