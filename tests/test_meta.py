"""Metadata cleanup, with the fake backend and silent MP3s: nothing is searched or downloaded."""

import mutagen
import pytest

from conftest import SILENT_MP3, FakeBackend
from spotarchive.backends import Song
from spotarchive.download import download_playlist, load_record
from spotarchive.meta import fix_playlist_metadata, setlist_tags, write_setlist_tags
from spotarchive.setlist import write_setlist
from spotarchive.spotify.models import Track


def track(name, artists=("Fake Artist",)):
    return Track(name, list(artists), album="Fake Album", release_date="2001-02-03",
                 spotify_url=f"https://example.com/{name}")


@pytest.fixture
def folder(tmp_path):
    folder = tmp_path / "Road Trip"
    folder.mkdir()
    write_setlist(folder, [track("Song A"), track("Song B", ("First", "Second")), track("Song C")])
    return folder


def tags(path):
    return dict(mutagen.File(path, easy=True))


def test_setlist_tags_skip_empty_fields():
    assert setlist_tags(Song("Song", ("A", "B"))) == {"title": ["Song"], "artist": ["A", "B"]}


@pytest.mark.parametrize("suffix", [".mp3", ".flac"])
def test_write_setlist_tags(tmp_path, suffix):
    path = tmp_path / f"song{suffix}"
    if suffix == ".mp3":
        path.write_bytes(SILENT_MP3)
    else:
        # A FLAC header with an empty stream: 44.1 kHz, stereo, 16 bit.
        info = b"\x10\x00\x10\x00" + b"\x00" * 6 + b"\x0a\xc4\x42\xf0" + b"\x00" * 20
        path.write_bytes(b"fLaC\x80\x00\x00\x22" + info)
    song = Song("Song", ("First", "Second"), album="Album", release_date="2001-02-03")

    assert write_setlist_tags(path, song) is True
    assert tags(path) == {"title": ["Song"], "artist": ["First", "Second"], "album": ["Album"],
                          "date": ["2001-02-03"]}
    assert write_setlist_tags(path, song) is False


def test_setlist_source_tags_downloaded_and_hand_added_files(folder):
    download_playlist(FakeBackend(), "Road Trip", folder)
    (folder / "YT Fake Artist - Song C.mp3").unlink()
    (folder / "fake artist - song c.mp3").write_bytes(SILENT_MP3)  # added by hand

    result = fix_playlist_metadata("Road Trip", folder, None)

    assert (result.updated, result.unchanged, result.failed) == (3, 0, [])
    assert tags(folder / "YT First - Song B.mp3")["artist"] == ["First", "Second"]
    assert tags(folder / "fake artist - song c.mp3")["title"] == ["Song C"]
    assert load_record(folder)["https://example.com/Song C"]["tagged"] == "setlist"

    again = fix_playlist_metadata("Road Trip", folder, None)
    assert (again.updated, again.unchanged) == (0, 3)


def test_missing_songs_are_skipped(folder):
    (folder / "Fake Artist - Song A.mp3").write_bytes(SILENT_MP3)
    result = fix_playlist_metadata("Road Trip", folder, None)
    assert (result.updated, result.unchanged, result.failed) == (1, 0, [])


def test_unreadable_file_fails_alone(folder):
    (folder / "Fake Artist - Song A.mp3").write_bytes(b"not audio")
    (folder / "Fake Artist - Song C.mp3").write_bytes(SILENT_MP3)
    seen = []

    result = fix_playlist_metadata("Road Trip", folder, None, on_done=lambda song, error: seen.append(error))

    assert result.updated == 1
    assert [s.name for s, _ in result.failed] == ["Song A"]
    assert seen[0] and seen[1] is None


def test_backend_skips_files_it_tagged_while_downloading(folder):
    backend = FakeBackend(can_tag=True, tags_downloads=True)
    download_playlist(backend, "Road Trip", folder)
    (folder / "Fake Artist - Song C.mp3").write_bytes(SILENT_MP3)
    (folder / "YT Fake Artist - Song C.mp3").unlink()

    result = fix_playlist_metadata("Road Trip", folder, backend)

    assert backend.tagged == ["Fake Artist - Song C.mp3"]
    assert (result.updated, result.unchanged) == (1, 2)
    assert load_record(folder)["https://example.com/Song C"]["tagged"] == "fake"

    backend.tagged.clear()
    fix_playlist_metadata("Road Trip", folder, backend)
    assert backend.tagged == []


def test_backend_retags_files_the_setlist_tagged(folder):
    backend = FakeBackend(can_tag=True, tags_downloads=True)
    download_playlist(backend, "Road Trip", folder)
    fix_playlist_metadata("Road Trip", folder, None)

    result = fix_playlist_metadata("Road Trip", folder, backend)

    assert result.updated == 3
    assert len(backend.tagged) == 3


def test_backend_failure_is_not_recorded(folder):
    backend = FakeBackend(can_tag=True, failing={"Song A"})
    (folder / "Fake Artist - Song A.mp3").write_bytes(SILENT_MP3)

    result = fix_playlist_metadata("Road Trip", folder, backend)

    assert [s.name for s, _ in result.failed] == ["Song A"]
    assert "tagged" not in load_record(folder)["https://example.com/Song A"]


def test_needs_a_setlist(tmp_path):
    with pytest.raises(FileNotFoundError):
        fix_playlist_metadata("Nope", tmp_path, None)
