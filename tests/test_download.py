import json

import pytest

from spotarchive.backends import Song
from spotarchive.download import (
    RECORD_FILENAME,
    download_playlist,
    expected_stems,
    find_present,
    load_record,
    setlist_songs,
)
from spotarchive.setlist import write_setlist
from spotarchive.spotify.models import Track


def track(name, artists=("Fake Artist",), url=None):
    return Track(name, list(artists), album="Fake Album", spotify_url=url or f"https://example.com/{name}")


@pytest.fixture
def folder(tmp_path):
    folder = tmp_path / "Road Trip"
    folder.mkdir()
    write_setlist(folder, [track("Song A"), track("Song B", ("First", "Second")), track("Song C")])
    return folder


def test_song_from_row_splits_artists():
    song = Song.from_row({"name": "Song", "artist": "First; Second", "duration_ms": "1000", "spotify_url": ""})
    assert song.artists == ("First", "Second")
    assert song.duration_ms == 1000
    assert song.key == "Song | First; Second | "
    assert song.label == "First - Song"


def test_setlist_songs_drops_duplicates(tmp_path):
    write_setlist(tmp_path, [track("Song A"), track("Song A"), track("Song B")])
    assert [s.name for s in setlist_songs(tmp_path)] == ["Song A", "Song B"]


def test_downloads_everything_then_nothing(folder, backend):
    result = download_playlist(backend, "Road Trip", folder)
    assert (result.total, result.downloaded, result.already_present) == (3, 3, 0)
    assert backend.requested == ["Song A", "Song B", "Song C"]

    backend.requested.clear()
    result = download_playlist(backend, "Road Trip", folder)
    assert (result.downloaded, result.already_present) == (0, 3)
    assert backend.requested == []


def test_record_maps_songs_to_files(folder, backend):
    download_playlist(backend, "Road Trip", folder)
    record = json.loads((folder / RECORD_FILENAME).read_text(encoding="utf-8"))
    assert record["songs"]["https://example.com/Song B"] == {
        "label": "First - Song B", "backend": "fake", "files": ["YT First - Song B.mp3"],
    }


def test_deleted_file_is_downloaded_again(folder, backend):
    download_playlist(backend, "Road Trip", folder)
    (folder / "YT Fake Artist - Song C.mp3").unlink()
    backend.requested.clear()
    download_playlist(backend, "Road Trip", folder)
    assert backend.requested == ["Song C"]


def test_file_named_like_the_song_counts_as_present(folder, backend):
    (folder / "fake artist - song a.mp3").write_bytes(b"x")
    (folder / "First, Second - Song B.mp3").write_bytes(b"x")
    # Wrong extension for this backend: not a song.
    (folder / "Fake Artist - Song C.txt").write_bytes(b"x")
    result = download_playlist(backend, "Road Trip", folder)
    assert backend.requested == ["Song C"]
    assert result.already_present == 2
    # Found files are recorded so later runs don't rely on the name match.
    assert load_record(folder)["https://example.com/Song A"]["files"] == ["fake artist - song a.mp3"]


def test_expected_stems_sanitize_like_file_names():
    assert "ac_dc - what_" in expected_stems(Song("What?", ("AC/DC",)))


def test_failures_are_reported_and_not_recorded(folder, backend):
    backend.failing = {"Song B"}
    done = []
    result = download_playlist(backend, "Road Trip", folder, on_done=lambda s, f, e: done.append((s.name, e)))
    assert [s.name for s, _ in result.failed] == ["Song B"]
    assert done == [("Song A", None), ("Song B", "not found"), ("Song C", None)]
    assert "https://example.com/Song B" not in load_record(folder)


def test_progress_counts_only_pending_songs(folder, backend):
    download_playlist(backend, "Road Trip", folder)
    (folder / "YT Fake Artist - Song A.mp3").unlink()
    started = []
    download_playlist(backend, "Road Trip", folder, on_start=lambda s, i, n: started.append((s.name, i, n)))
    assert started == [("Song A", 1, 1)]


def test_corrupt_record_falls_back_to_file_names(folder, backend):
    (folder / RECORD_FILENAME).write_text("{not json", encoding="utf-8")
    (folder / "Fake Artist - Song A.mp3").write_bytes(b"x")
    present = find_present(setlist_songs(folder), folder, load_record(folder), backend.audio_extensions)
    assert list(present) == ["https://example.com/Song A"]


def test_missing_setlist_raises(tmp_path, backend):
    with pytest.raises(FileNotFoundError):
        download_playlist(backend, "Nothing", tmp_path)
