

def test_song_name_survives_when_artist_is_split_by_a_hyphen():
    """🔴 Живая регрессия 16.08: «Егор Крид - MALO 2.0 (ft. …) КЛИП» превращалось в
    «Егор Крид — Егор Крид». Хвостовое правило с дефисом съедало НАЗВАНИЕ целиком,
    потому что тем же дефисом отделён исполнитель."""
    from app.track_naming import split_artist_title

    artist, name = split_artist_title("Егор Крид - MALO 2.0 (ft. OG Buda) КЛИП", "канал")

    assert artist == "Егор Крид"
    assert name == "MALO 2.0 (ft. OG Buda)"


def test_pipe_tail_is_still_stripped():
    from app.track_naming import split_artist_title

    assert split_artist_title("Jaman T - Алая | Премьера трека", "канал") == ("Jaman T", "Алая")
