"""Metadata for imported links, from made-up yt-dlp info dicts."""

import pytest

from spotarchive.backends.ytinfo import clean_title, track_info


def test_named_track_and_artists_are_used_as_they_are():
    track = track_info({
        "title": "Fake Song (Official Audio)", "track": "Fake Song - Part 2",
        "artists": ["Fake Artist", "Other Artist", "Fake Artist"], "album": "Fake Album",
        "release_date": "20010203", "release_year": 2001, "upload_date": "20240101",
        "track_number": 3, "genres": ["Fake Genre"], "channel": "Someone Else",
    })
    assert track.official
    assert track.tags() == {
        "title": ["Fake Song - Part 2"], "artist": ["Fake Artist", "Other Artist"], "album": ["Fake Album"],
        "date": ["2001-02-03"], "tracknumber": ["3"], "genre": ["Fake Genre"],
    }
    assert track.label == "Fake Artist - Fake Song - Part 2"


def test_single_artist_and_genre_fields_and_release_year():
    track = track_info({"track": "Fake Song", "artist": "Fake Artist", "genre": "Fake Genre", "release_year": 1999})
    assert track.official and track.artists == ("Fake Artist",)
    assert (track.release_date, track.genres) == ("1999", ("Fake Genre",))


@pytest.mark.parametrize("dash", ["-", "\u2013", "\u2014"])
def test_video_title_is_split_and_cleaned(dash):
    track = track_info({"title": f"Fake Artist {dash} Fake Song (Official Video) [4K Remaster]",
                        "channel": "Fake Channel", "upload_date": "20240101"})
    assert not track.official
    assert (track.artists, track.title) == (("Fake Artist",), "Fake Song")
    assert track.tags() == {"title": ["Fake Song"], "artist": ["Fake Artist"]}


@pytest.mark.parametrize("label", [
    "(Official Music Video)", "[Official Lyric Video]", "(Visualizer)", "(Official Audio)", "[HD]",
    "\u3010MV\u3011", "(Official HD Video)", "(Lyrics)", "(Video Oficial)",
])
def test_video_labels_are_dropped(label):
    assert clean_title(f"Fake Song {label}") == "Fake Song"


@pytest.mark.parametrize("label", [
    "(Live)", "(Official Live Video)", "(Fake Remix)", "(Video Game Remix)", "(feat. Other Artist)",
    "(2011 Remaster)", "(Remastered)", "(Acoustic Version)", "()",
])
def test_labels_about_the_recording_stay(label):
    assert clean_title(f"Fake Song {label}") == f"Fake Song {label}"


def test_title_that_is_only_a_label_is_kept():
    assert clean_title("(Official Video)") == "(Official Video)"


@pytest.mark.parametrize("channel", ["Fake Artist - Topic", "Fake Artist"])
def test_uploader_is_the_artist_without_a_dash(channel):
    track = track_info({"title": "Fake-Song", "channel": channel})
    assert (track.artists, track.title) == (("Fake Artist",), "Fake-Song")


def test_known_artist_is_only_cut_off_the_front():
    assert track_info({"title": "Fake Artist - Fake Song", "artist": "fake artist"}).title == "Fake Song"
    other = track_info({"title": "Fake Song - Fake Remix", "artists": ["Fake Artist"]})
    assert (other.artists, other.title) == (("Fake Artist",), "Fake Song - Fake Remix")


def test_youtube_premiere_date_is_not_a_release_date():
    info = {"title": "Fake Song", "extractor_key": "Youtube", "release_timestamp": 1700000000,
            "release_date": "20231114", "release_year": 2023}
    assert track_info(info).release_date == ""
    assert track_info({**info, "extractor_key": "Bandcamp"}).release_date == "2023-11-14"


def test_nothing_but_an_id():
    track = track_info({"id": "abc123"})
    assert (track.title, track.artists, track.label) == ("abc123", (), "abc123")
