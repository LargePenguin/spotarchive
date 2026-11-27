"""``--overwrite``. The "download" writes a silent MP3 with an extra marker byte."""

import mutagen
import pytest
from conftest import SILENT_MP3, FakeBackend

from spotarchive import cli, override
from spotarchive.backends import DownloadFailed, ImportItem, ImportListing
from spotarchive.download import load_record, prepare_playlist, save_record
from spotarchive.meta import fix_playlist_metadata
from spotarchive.session import YouTubeSession
from spotarchive.setlist import write_setlist
from spotarchive.spotify.models import Track

URL = "https://video.example.com/watch?v=dummy"
QUERY = "Fake Artist - Right Song"
NEW_AUDIO = SILENT_MP3 + SILENT_MP3[:417]


class LinkBackend(FakeBackend):
    can_import = True

    def __init__(self, error=None, playlist=None):
        super().__init__()
        self.error = error
        self.playlist = playlist
        self.urls = []

    def list_url(self, url):
        return ImportListing((ImportItem(url, "Video Title"),), self.playlist)

    def import_item(self, item, folder, events=None):
        self.urls.append(item.url)
        if self.error:
            raise DownloadFailed(self.error)
        file = folder / "Uploader - Video Title.mp3"
        file.write_bytes(NEW_AUDIO)
        return [file]


@pytest.fixture(autouse=True)
def url_backend(monkeypatch):
    backend = LinkBackend()
    monkeypatch.setattr(cli, "make_importer", lambda settings: backend)
    return backend


def track(name, album="Fake Album", artists=("Fake Artist",), track_id=None):
    track_id = track_id or name.replace(" ", "")
    return Track(name, list(artists), album=album, spotify_url=f"https://open.spotify.com/track/{track_id}")


def playlist(data_dir, name, tracks, files=(), record=None):
    folder = data_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    write_setlist(folder, tracks)
    for file in files:
        (folder / file).parent.mkdir(parents=True, exist_ok=True)
        (folder / file).write_bytes(SILENT_MP3)
    if record:
        save_record(folder, {
            t.spotify_url: {"label": f"{t.artists[0]} - {t.name}", "backend": "somedl", "files": list(f)}
            for t, f in record
        })
    return folder


def run(data_dir, query=QUERY, url=URL):
    return cli.main(["-o", query, url], data_dir=data_dir)


def audio(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*.mp3"))


def title(path):
    return mutagen.File(path, easy=True)["title"]


def is_new(path):
    # Retagging changes the header, not the audio at the end.
    return path.read_bytes().endswith(NEW_AUDIO[-1000:]) and path.stat().st_size >= len(NEW_AUDIO)


def test_replaces_a_wrongly_named_match_everywhere(tmp_path, url_backend):
    right = track("Right Song")
    one = playlist(tmp_path, "One", [right], ["Other Band - Wrong Song.mp3"],
                   [(right, ["Other Band - Wrong Song.mp3"])])
    # Not selected, no record, found by its name.
    two = playlist(tmp_path, "Two", [track("Right Song", album="Second Album")], [f"{QUERY}.mp3"])

    assert run(tmp_path) == 0

    assert url_backend.urls == [URL]
    for folder, album in ((one, "Fake Album"), (two, "Second Album")):
        assert audio(folder) == [f"{QUERY}.mp3"]
        assert is_new(folder / f"{QUERY}.mp3")
        tags = mutagen.File(folder / f"{QUERY}.mp3", easy=True)
        assert (tags["title"], tags["artist"], tags["album"]) == (["Right Song"], ["Fake Artist"], [album])
        assert load_record(folder)[right.spotify_url] == {
            "label": QUERY, "files": [f"{QUERY}.mp3"], "override": URL, "tagged": "setlist",
        }
        # The next download run takes it as there; no leftovers.
        assert prepare_playlist(url_backend, folder.name, folder).pending == []
        assert not list(folder.glob(".override-*"))
    assert not list(tmp_path.glob(".override-*"))


def test_metadata_cleanup_leaves_the_replacement_to_the_setlist(tmp_path):
    folder = playlist(tmp_path, "One", [track("Right Song")], [f"{QUERY}.mp3"])
    assert run(tmp_path) == 0

    helper = FakeBackend(can_tag=True)
    result = fix_playlist_metadata("One", folder, helper)

    assert helper.tagged == []
    assert (result.updated, result.unchanged) == (0, 1)


def test_override_mark_is_dropped_when_the_file_changes(tmp_path, url_backend):
    right = track("Right Song")
    folder = playlist(tmp_path, "One", [right], [f"{QUERY}.mp3"])
    run(tmp_path)
    (folder / f"{QUERY}.mp3").rename(folder / "Fake Artist - Right Song.flac")
    url_backend.audio_extensions = frozenset({".mp3", ".flac"})

    prepare_playlist(url_backend, "One", folder)

    assert load_record(folder)[right.spotify_url] == {"label": QUERY, "files": [f"{QUERY}.flac"]}


def test_matches_ignoring_case_and_any_of_the_artists_forms(tmp_path):
    song = track("Right Song", artists=("Fake Artist", "Second Artist"))
    folder = playlist(tmp_path, "One", [song], ["wrong.mp3"], [(song, ["wrong.mp3"])])

    assert run(tmp_path, "fake artist, second artist - right song") == 0

    assert audio(folder) == [f"{QUERY}.mp3"]


def test_keeps_subfolders_and_merges_duplicate_files(tmp_path):
    song = track("Right Song")
    folder = playlist(tmp_path, "One", [song], ["Album/wrong.mp3", f"Extra/{QUERY}.mp3"],
                      [(song, ["Album/wrong.mp3"])])

    assert run(tmp_path) == 0

    assert audio(folder) == [f"Album/{QUERY}.mp3"]
    assert load_record(folder)[song.spotify_url]["files"] == [f"Album/{QUERY}.mp3"]


def test_the_name_of_the_wrong_file_gives_the_recorded_song(tmp_path):
    song = track("Right Song")
    folder = playlist(tmp_path, "One", [song], ["Other Band - Wrong Song.mp3"],
                      [(song, ["Other Band - Wrong Song.mp3"])])

    assert run(tmp_path, "Other Band - Wrong Song") == 0

    assert audio(folder) == [f"{QUERY}.mp3"]
    assert title(folder / f"{QUERY}.mp3") == ["Right Song"]


def test_a_file_recorded_as_another_song_is_left_alone(tmp_path, capsys):
    right, other = track("Right Song"), track("Other Song")
    # SomeDL gave "Other Song" a file named like "Right Song", which another playlist has.
    one = playlist(tmp_path, "One", [right, other], [f"{QUERY}.mp3"], [(other, [f"{QUERY}.mp3"])])
    two = playlist(tmp_path, "Two", [right], [f"{QUERY}.mp3"])

    assert run(tmp_path) == 0

    assert (one / f"{QUERY}.mp3").read_bytes() == SILENT_MP3
    assert is_new(two / f"{QUERY}.mp3")
    output = " ".join(capsys.readouterr().out.split())
    assert "alone" in output and "Not downloaded yet in One" in output


def test_never_writes_over_another_songs_file(tmp_path):
    right, other = track("Right Song"), track("Other Song")
    folder = playlist(tmp_path, "One", [right, other], ["wrong.mp3", f"{QUERY}.mp3"],
                      [(right, ["wrong.mp3"]), (other, [f"{QUERY}.mp3"])])

    assert run(tmp_path) == 0

    assert (folder / f"{QUERY}.mp3").read_bytes() == SILENT_MP3
    assert is_new(folder / f"{QUERY} (2).mp3")
    assert not (folder / "wrong.mp3").exists()
    record = load_record(folder)
    assert record[right.spotify_url]["files"] == [f"{QUERY} (2).mp3"]
    assert record[other.spotify_url]["files"] == [f"{QUERY}.mp3"]


def test_a_song_sharing_the_replaced_file_is_downloaded_again(tmp_path, url_backend):
    right, other = track("Right Song"), track("Other Song")
    folder = playlist(tmp_path, "One", [right, other], ["shared.mp3"],
                      [(right, ["shared.mp3"]), (other, ["shared.mp3"])])

    assert run(tmp_path) == 0

    assert list(load_record(folder)) == [right.spotify_url]
    assert [s.name for s in prepare_playlist(url_backend, "One", folder).pending] == ["Other Song"]


def test_versions_of_a_song_sharing_a_file_both_get_the_new_one(tmp_path):
    single, album = track("Right Song", track_id="single"), track("Right Song", track_id="album")
    folder = playlist(tmp_path, "One", [single, album], ["wrong.mp3"],
                      [(single, ["wrong.mp3"]), (album, ["wrong.mp3"])])

    assert run(tmp_path) == 0

    record = load_record(folder)
    assert record[single.spotify_url]["files"] == record[album.spotify_url]["files"] == [f"{QUERY}.mp3"]


def test_files_without_a_setlist_are_replaced_too(tmp_path):
    (tmp_path / "loose" / "deeper").mkdir(parents=True)
    (tmp_path / "loose" / "deeper" / f"{QUERY}.mp3").write_bytes(SILENT_MP3)
    (tmp_path / f"{QUERY}.mp3").write_bytes(SILENT_MP3)

    assert run(tmp_path) == 0

    assert is_new(tmp_path / f"{QUERY}.mp3")
    assert is_new(tmp_path / "loose" / "deeper" / f"{QUERY}.mp3")
    assert title(tmp_path / f"{QUERY}.mp3") == ["Right Song"]


def test_hidden_folders_and_browser_profiles_are_skipped(tmp_path):
    for name in (".trash", "browser-profile", "One/.sd-leftover"):
        (tmp_path / name).mkdir(parents=True)
        (tmp_path / name / f"{QUERY}.mp3").write_bytes(SILENT_MP3)

    assert override.find_copies(tmp_path, QUERY, frozenset({".mp3"})).copies == []


@pytest.mark.parametrize("tracks", [[], [track("Right Song")]])
def test_nothing_to_replace_downloads_nothing(tmp_path, url_backend, tracks):
    playlist(tmp_path, "One", tracks)

    assert run(tmp_path) == 1

    assert url_backend.urls == []


@pytest.mark.parametrize("failing", [{"error": "video unavailable"}, {"playlist": "Fake Mix"}])
def test_a_failed_download_changes_nothing(tmp_path, monkeypatch, failing):
    monkeypatch.setattr(cli, "make_importer", lambda settings: LinkBackend(**failing))
    song = track("Right Song")
    folder = playlist(tmp_path, "One", [song], ["wrong.mp3"], [(song, ["wrong.mp3"])])
    before = load_record(folder)

    assert run(tmp_path) == 1

    assert audio(folder) == ["wrong.mp3"]
    assert load_record(folder) == before
    assert not list(tmp_path.glob(".override-*"))


def test_a_copy_that_cant_be_written_keeps_its_old_file(tmp_path, monkeypatch):
    one = playlist(tmp_path, "One", [track("Right Song")], [f"{QUERY}.mp3"])
    two = playlist(tmp_path, "Two", [track("Right Song")], [f"{QUERY}.mp3"])
    copyfile = override.shutil.copyfile

    def failing_copy(source, target):
        if target.parent == one:
            raise OSError("disk full")
        return copyfile(source, target)

    monkeypatch.setattr(override.shutil, "copyfile", failing_copy)

    assert run(tmp_path) == 1

    assert (one / f"{QUERY}.mp3").read_bytes() == SILENT_MP3
    assert not list(one.glob(".override-*"))
    assert is_new(two / f"{QUERY}.mp3")


def test_the_youtube_login_is_handed_over(tmp_path, url_backend, monkeypatch):
    playlist(tmp_path, "One", [track("Right Song")], [f"{QUERY}.mp3"])
    monkeypatch.setattr(cli.youtube_auth, "is_logged_in", lambda login: True)
    store = cli.SessionStore.load(tmp_path)
    store.youtube = YouTubeSession(cookies=[], user_agent="dummy-agent")
    store.save()

    assert run(tmp_path) == 0

    assert url_backend.youtube_login is not None


@pytest.mark.parametrize("argv", [
    ["-o", "Right Song", URL],
    ["-o", URL, QUERY],
    ["-o", QUERY, "ytsearch:something"],
    ["-o", QUERY, "https://open.spotify.com/track/dummy"],
    ["-o", QUERY],
    ["-o", QUERY, URL, "-d"],
    ["-o", QUERY, URL, "-m"],
    ["-o", QUERY, URL, "-t", "2"],
    ["-o", QUERY, URL, "-u"],
    ["-o", QUERY, URL, URL],
])
def test_bad_command_lines(tmp_path, url_backend, argv):
    with pytest.raises(SystemExit):
        cli.main(argv, data_dir=tmp_path)
    assert url_backend.urls == []


def test_imported_files_are_replaced_with_their_folders_record(tmp_path):
    folder = tmp_path / "imports" / "Fake Mix"
    folder.mkdir(parents=True)
    (folder / f"{QUERY}.mp3").write_bytes(SILENT_MP3)
    save_record(folder, {"https://video.example.com/one": {"label": "Video", "files": [f"{QUERY}.mp3"]}})
    # A playlist with the song doesn't make the import "another song".
    playlist(tmp_path, "One", [track("Right Song")], [f"{QUERY}.mp3"])

    assert run(tmp_path) == 0

    assert is_new(folder / f"{QUERY}.mp3")
    # Still keyed by the link, so importing it again finds the file there.
    entry = load_record(folder)["https://video.example.com/one"]
    assert (entry["files"], entry["override"]) == ([f"{QUERY}.mp3"], URL)
    assert not (tmp_path / "imports" / ".downloads.json").exists()
