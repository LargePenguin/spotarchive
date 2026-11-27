"""Importing links: listing, downloading and tagging, with yt-dlp and FFmpeg faked."""

import io
import json
import subprocess
import wave
from pathlib import Path

import mutagen
import pytest
from mutagen.flac import FLAC
from mutagen.id3 import ID3

from conftest import SILENT_MP3
from spotarchive import cli, importer, ui
from spotarchive.backends import DownloadEvents, DownloadFailed, ImportItem, ImportListing, TaggingFailed, util, ytdlp
from spotarchive.backends.ytdlp import YtDlpBackend, parse_listing
from spotarchive.download import RECORD_FILENAME, load_record
from spotarchive.session import PlaylistState, SessionStore, YouTubeSession
from spotarchive.setlist import read_setlist
from spotarchive.tags import embed_cover, write_tags
from spotarchive.spotify.models import Playlist, Track

VIDEO = "https://www.youtube.com/watch?v=fakevideo01"
PLAYLIST = "https://www.youtube.com/playlist?list=fakeplaylist"
OFFICIAL = {"id": "fakevideo01", "title": "Fake Song", "track": "Fake Song", "artists": ["Fake Artist"],
            "album": "Fake Album", "release_date": "20010203"}
PLAIN = {"id": "fakevideo02", "title": "Fake Artist - Other Song (Official Video)", "channel": "Fake Channel"}


def video_listing(url=VIDEO, title="Fake Song"):
    return {"_type": "video", "extractor_key": "Youtube", "webpage_url": url, "title": title}


def playlist_listing(*urls, title="Fake: Playlist"):
    return {"_type": "playlist", "extractor_key": "YoutubeTab", "webpage_url": PLAYLIST, "title": title,
            "entries": [{"_type": "url", "ie_key": "Youtube", "url": u, "title": f"Entry {i}"}
                        for i, u in enumerate(urls)]}


class FakeProcess:
    def __init__(self, output, returncode):
        self.stdout = io.BytesIO(output)
        self.returncode = returncode

    def wait(self):
        return self.returncode

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeYtDlp:
    """Stands in for yt-dlp (listing and downloading) and for FFmpeg's cover crop."""

    def __init__(self):
        self.listing = video_listing()
        self.list_error = ""
        # Link -> the info dict "downloaded" with it; a missing link fails.
        self.infos = {VIDEO: OFFICIAL}
        self.thumbnail = True
        self.cover_works = True
        self.lists, self.downloads, self.crops = [], [], []

    def run(self, cmd, **kw):
        if cmd[0] == "ffmpeg":
            self.crops.append(cmd)
            if self.cover_works:
                Path(cmd[-1]).write_bytes(b"fake jpeg")
            return subprocess.CompletedProcess(cmd, 0 if self.cover_works else 1, b"", b"")
        self.lists.append(cmd)
        if self.list_error:
            return subprocess.CompletedProcess(cmd, 1, "", f"ERROR: {self.list_error}\n")
        return subprocess.CompletedProcess(cmd, 0, json.dumps(self.listing), "")

    def popen(self, cmd, **kw):
        self.downloads.append(cmd)
        info = self.infos.get(cmd[-1])
        if info is None:
            return FakeProcess(b"ERROR: fake video unavailable\n", 1)
        staging = Path(cmd[cmd.index("--paths") + 1])
        (staging / "download.mp3").write_bytes(SILENT_MP3)
        (staging / "download.info.json").write_text(json.dumps(info), encoding="utf-8")
        if self.thumbnail:
            (staging / "download.webp").write_bytes(b"fake webp")
        return FakeProcess(b"[download] 100%\n", 101)


@pytest.fixture
def fake(monkeypatch):
    fake = FakeYtDlp()
    monkeypatch.setattr(ytdlp.subprocess, "run", fake.run)
    monkeypatch.setattr(ytdlp.subprocess, "Popen", fake.popen)
    return fake


@pytest.fixture
def ytdlp_backend(monkeypatch):
    backend = YtDlpBackend(executable="yt-dlp")
    monkeypatch.setattr(backend, "check", lambda: None)
    monkeypatch.setattr(backend, "warnings", lambda: [])
    monkeypatch.setattr(cli, "make_importer", lambda settings: backend)
    return backend


@pytest.fixture(autouse=True)
def no_spotify(monkeypatch):
    monkeypatch.setattr(cli, "make_client", lambda store: pytest.fail("importing must not use Spotify"))
    monkeypatch.setattr(cli, "make_backend", lambda settings: pytest.fail("importing uses the importer"))


class Events(DownloadEvents):
    def __init__(self):
        super().__init__(show_output=False)
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)


def tags(path):
    return {k: list(v) for k, v in mutagen.File(path, easy=True).tags.items()}


# --- the listing


def test_single_video_is_one_item():
    assert parse_listing(video_listing(), "https://youtu.be/fakevideo01") == ImportListing(
        (ImportItem(VIDEO, "Fake Song"),)
    )


def test_playlist_items_without_duplicates():
    listing = parse_listing(playlist_listing(VIDEO, f"{VIDEO}2", VIDEO), PLAYLIST)
    assert listing.playlist == "Fake: Playlist"
    assert [i.url for i in listing.items] == [VIDEO, f"{VIDEO}2"]


def test_items_without_a_page_of_their_own_are_numbered():
    page = "https://example.com/page"
    entry = {"title": "Embedded", "url": "https://media.example.com/stream", "webpage_url": page}
    listing = parse_listing({"_type": "playlist", "webpage_url": page, "id": "x", "entries": [entry, None, entry]}, page)
    assert listing.items == (ImportItem(page, "Embedded", 1), ImportItem(page, "Embedded", 3))
    assert [i.key for i in listing.items] == [f"{page}#1", f"{page}#3"]


@pytest.mark.parametrize("info, message", [
    ({"_type": "playlist", "entries": []}, "nothing to download"),
    ({"_type": "playlist", "entries": [{"_type": "playlist", "entries": []}]}, "other playlists"),
    ({"_type": "playlist", "entries": [{"_type": "url", "ie_key": "YoutubeTab", "url": PLAYLIST}]},
     "other playlists"),
    ({"extractor_key": "Spotify", "webpage_url": VIDEO}, "never downloads audio from Spotify"),
    ({"extractor_key": "Generic", "webpage_url": "https://open.spotify.com/episode/fake"},
     "never downloads audio from Spotify"),
    ({"_type": "playlist", "entries": [{"_type": "url", "url": "https://open.spotify.com/embed/fake"}]},
     "never downloads audio from Spotify"),
    ({"_type": "playlist", "entries": [{"url": "https://p.scdn.co/fake", "webpage_url": VIDEO}]},
     "never downloads audio from Spotify"),
])
def test_unusable_listings(info, message):
    with pytest.raises(DownloadFailed, match=message):
        parse_listing(info, "https://example.com/page")


def test_list_command(fake, ytdlp_backend):
    ytdlp_backend.extra_args = ("--proxy", "example.com")
    ytdlp_backend.list_url(f"{VIDEO}&list=fake")
    cmd = fake.lists[0]
    assert {"--ignore-config", "--dump-single-json", "--flat-playlist", "--no-playlist"} <= set(cmd)
    assert cmd[-4:] == ["--proxy", "example.com", "--", f"{VIDEO}&list=fake"]


def test_listing_error_is_yt_dlps(fake, ytdlp_backend):
    fake.list_error = "fake link is broken"
    with pytest.raises(DownloadFailed, match="fake link is broken"):
        ytdlp_backend.list_url(VIDEO)


# --- one item


def test_official_audio_is_tagged_with_cover(fake, ytdlp_backend, tmp_path):
    [file] = ytdlp_backend.import_item(ImportItem(VIDEO, "Fake Song"), tmp_path, Events())
    assert file == tmp_path / "Fake Artist - Fake Song.mp3"
    assert tags(file) == {"title": ["Fake Song"], "artist": ["Fake Artist"], "album": ["Fake Album"],
                          "date": ["2001-02-03"]}
    assert ID3(file).getall("APIC")[0].data == b"fake jpeg"
    assert "crop='min(iw,ih)':'min(iw,ih)'" in fake.crops[0]
    assert list(tmp_path.iterdir()) == [file]

    cmd = fake.downloads[0]
    assert {"--no-playlist", "--write-info-json", "--write-thumbnail", "--extract-audio"} <= set(cmd)
    assert "extractor!*=?spotify" in cmd[cmd.index("--match-filters") + 1]
    assert "--playlist-items" not in cmd and cmd[-2:] == ["--", VIDEO]


def test_other_videos_get_no_cover(fake, ytdlp_backend, tmp_path):
    fake.infos[VIDEO] = PLAIN
    [file] = ytdlp_backend.import_item(ImportItem(VIDEO), tmp_path)
    assert file.name == "Fake Artist - Other Song.mp3"
    assert not ID3(file).getall("APIC") and not fake.crops


@pytest.mark.parametrize("broken", ["thumbnail", "cover_works"])
def test_missing_cover_is_only_a_warning(fake, ytdlp_backend, tmp_path, broken):
    setattr(fake, broken, False)
    events = Events()
    [file] = ytdlp_backend.import_item(ImportItem(VIDEO), tmp_path, events)
    assert tags(file)["title"] == ["Fake Song"]
    assert len(events.warnings) == 1 and "no cover art" in events.warnings[0]


def test_numbered_item_selects_it(fake, ytdlp_backend, tmp_path):
    ytdlp_backend.import_item(ImportItem(VIDEO, "x", 3), tmp_path)
    cmd = fake.downloads[0]
    assert cmd[cmd.index("--playlist-items") + 1] == "3"


def test_other_files_are_never_replaced(fake, ytdlp_backend, tmp_path):
    first = tmp_path / "Fake Artist - Fake Song.mp3"
    first.write_bytes(b"mine")
    names = [ytdlp_backend.import_item(ImportItem(VIDEO), tmp_path)[0].name for _ in range(3)]
    assert names == [
        "Fake Artist - Fake Song [fakevideo01].mp3",
        "Fake Artist - Fake Song [fakevideo01] (2).mp3",
        "Fake Artist - Fake Song [fakevideo01] (3).mp3",
    ]
    assert first.read_bytes() == b"mine"


def test_long_names_keep_what_tells_them_apart(fake, ytdlp_backend, tmp_path, monkeypatch):
    monkeypatch.setattr(util, "_NAME_LIMIT", 30)
    names = [ytdlp_backend.import_item(ImportItem(VIDEO), tmp_path)[0].name for _ in range(2)]
    assert all(len(n) <= 30 and n.endswith(".mp3") for n in names)
    assert names[1].endswith(" [fakevideo01].mp3")


def test_names_are_safe_and_never_hidden(fake, ytdlp_backend, tmp_path):
    fake.infos[VIDEO] = {"id": "x", "track": "a/b: c?", "artists": ["..Fake Artist"]}
    [file] = ytdlp_backend.import_item(ImportItem(VIDEO), tmp_path)
    assert file.name == "Fake Artist - a_b_ c_.mp3" and file.parent == tmp_path


def test_cover_art_in_flac(tmp_path):
    # An empty FLAC stream: just the STREAMINFO block.
    info = bytes([0x10, 0, 0x10, 0]) + bytes(6) + bytes([0x0A, 0xC4, 0x42, 0xF0]) + bytes(20)
    path = tmp_path / "fake.flac"
    path.write_bytes(b"fLaC" + bytes([0x80, 0, 0, 34]) + info)
    embed_cover(path, b"fake jpeg")
    embed_cover(path, b"fake jpeg")
    assert [p.data for p in FLAC(path).pictures] == [b"fake jpeg"]


def test_wav_fails_to_tag_without_crashing(tmp_path):
    path = tmp_path / "fake.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(bytes(1600))
    with pytest.raises(TaggingFailed):
        write_tags(path, {"title": ["Fake Song"]})


# --- the command line


def test_import_single_video(fake, ytdlp_backend, tmp_path):
    assert cli.main(["-d", VIDEO], data_dir=tmp_path) == 0
    folder = tmp_path / "Imports"
    assert (folder / "Fake Artist - Fake Song.mp3").is_file()
    assert load_record(folder)[VIDEO]["files"] == ["Fake Artist - Fake Song.mp3"]
    assert load_record(folder)[VIDEO]["backend"] == "ytdlp"


@pytest.mark.parametrize("threads", ["1", "3"])
def test_import_playlist_skips_what_is_there_and_retries_failures(fake, ytdlp_backend, tmp_path, threads):
    other = f"{VIDEO}2"
    fake.listing = playlist_listing(VIDEO, other)
    assert cli.main(["-d", "-t", threads, PLAYLIST], data_dir=tmp_path) == 1
    folder = tmp_path / "Imports" / "Fake_ Playlist"
    assert (folder / "Fake Artist - Fake Song.mp3").is_file()
    assert list(load_record(folder)) == [VIDEO]

    fake.infos[other] = PLAIN
    fake.downloads.clear()
    assert cli.main(["-d", PLAYLIST, "-t", threads], data_dir=tmp_path) == 0
    assert [cmd[-1] for cmd in fake.downloads] == [other]
    assert (folder / "Fake Artist - Other Song.mp3").is_file()

    # A deleted file is downloaded again.
    (folder / "Fake Artist - Other Song.mp3").unlink()
    fake.downloads.clear()
    assert cli.main(["-d", PLAYLIST], data_dir=tmp_path) == 0
    assert [cmd[-1] for cmd in fake.downloads] == [other]


def test_import_uses_the_youtube_login(fake, ytdlp_backend, tmp_path):
    store = SessionStore.load(tmp_path)
    store.youtube = YouTubeSession(
        cookies=[{"name": "SAPISID", "value": "dummy-cookie", "domain": ".youtube.com", "path": "/",
                  "expires": 4_000_000_000, "secure": True}],
        user_agent="dummy-agent",
    )
    store.save()
    assert cli.main(["-d", VIDEO], data_dir=tmp_path) == 0
    assert "--cookies" in fake.lists[0] and "--cookies" in fake.downloads[0]


def test_unreadable_link_fails(fake, ytdlp_backend, tmp_path):
    fake.list_error = "fake link is broken"
    assert cli.main(["-d", VIDEO], data_dir=tmp_path) == 1
    assert not (tmp_path / "Imports").exists()


@pytest.mark.parametrize("link", [
    "https://open.spotify.com/track/fake", "https://spotify.link/fake", "https://SPOTIFY.com./x",
    "spotify:track:fake", "fake song", "file:///tmp/fake.mp3", "ytsearch:fake", "", "https://[broken",
])
def test_refused_links_never_reach_yt_dlp(fake, ytdlp_backend, tmp_path, link):
    assert cli.main(["-d", link], data_dir=tmp_path) == 1
    assert not fake.lists and not fake.downloads


def test_lookalike_domain_is_not_spotify():
    assert importer.check_link(" https://notspotify.com/x ") == "https://notspotify.com/x"


@pytest.mark.parametrize("argv", [
    [VIDEO], ["-m", VIDEO], ["-dm", VIDEO], ["-fd", VIDEO], ["-d", "-l", "youtube", VIDEO], ["-u", VIDEO],
    ["-u", "-d", VIDEO],
])
def test_refused_combinations(argv, tmp_path):
    with pytest.raises(SystemExit):
        cli.main(argv, data_dir=tmp_path)


@pytest.mark.parametrize("argv, expected", [
    (["-d"], (True, None, None)),
    (["-dm"], (True, None, None)),
    (["-fnd"], (True, None, None)),
    (["-dt", "4"], (True, None, 4)),
    (["-d", VIDEO], (True, VIDEO, None)),
    (["-d", "-t", "4", VIDEO], (True, VIDEO, 4)),
    (["-t", "4", "-d", VIDEO], (True, VIDEO, 4)),
    (["-d", VIDEO, "-t", "4"], (True, VIDEO, 4)),
])
def test_download_arguments(argv, expected):
    args = cli.build_parser().parse_args(argv)
    assert (args.download, args.link, args.threads) == expected


# --- the Imports folder and Spotify playlists


@pytest.mark.parametrize("claim", ["state", "setlist"])
def test_import_refuses_a_playlists_folder(fake, ytdlp_backend, tmp_path, claim):
    if claim == "state":
        store = SessionStore.load(tmp_path)
        store.playlists["p1"] = PlaylistState(name="imports", selected=False, folder="imports")
        store.save()
    else:
        (tmp_path / "Imports").mkdir()
        (tmp_path / "Imports" / "setlist.csv").write_text("name\n", encoding="utf-8")
    assert cli.main(["-d", VIDEO], data_dir=tmp_path) == 1
    assert not fake.lists


class FakeClient:
    def __init__(self, playlists):
        self._playlists = playlists

    def playlists(self):
        return self._playlists

    def playlist_tracks(self, playlist, on_progress=None):
        return [Track("Song A", ["Fake Artist"])]


@pytest.mark.parametrize("selected", [["p1", "p2"], ["p2"]])
def test_fetch_moves_a_playlist_out_of_the_imports_folder(tmp_path, monkeypatch, selected):
    # As left by a version without imports.
    store = SessionStore.load(tmp_path)
    store.playlists["p1"] = PlaylistState(name="Imports", selected=True, folder="Imports")
    store.save()
    (tmp_path / "Imports").mkdir()
    (tmp_path / "Imports" / "setlist.csv").write_text("name\n", encoding="utf-8")
    (tmp_path / "Imports" / "song.mp3").write_bytes(b"fake")

    client = FakeClient([Playlist("p1", "Imports"), Playlist("p2", "Road Trip")])
    monkeypatch.setattr(cli, "make_client", lambda store: client)
    monkeypatch.setattr(ui, "choose_playlists", lambda playlists, preselected: selected)
    assert cli.main(["-f"], data_dir=tmp_path) == 0

    assert SessionStore.load(tmp_path).playlists["p1"].folder == "Imports (p1)"
    assert (tmp_path / "Imports (p1)" / "song.mp3").is_file()
    assert not (tmp_path / "Imports").exists()
    root = tmp_path / "Imports"
    assert importer.check_imports_root(SessionStore.load(tmp_path), tmp_path, root) == root
    assert read_setlist(tmp_path / "Road Trip")


def test_fetch_leaves_imports_alone(tmp_path, monkeypatch):
    (tmp_path / "Imports").mkdir()
    (tmp_path / "Imports" / RECORD_FILENAME).write_text("{}", encoding="utf-8")
    client = FakeClient([Playlist("p1", "imports")])
    monkeypatch.setattr(cli, "make_client", lambda store: client)
    monkeypatch.setattr(ui, "choose_playlists", lambda playlists, preselected: ["p1"])
    assert cli.main(["-f"], data_dir=tmp_path) == 0
    assert SessionStore.load(tmp_path).playlists["p1"].folder == "imports (p1)"
    assert [p.name for p in (tmp_path / "Imports").iterdir()] == [RECORD_FILENAME]


def use_import_dir(tmp_path, value):
    path = tmp_path / "custom.toml"
    path.write_text(f'import_dir = "{value}"\n', encoding="utf-8")
    return ["-c", str(path)]


@pytest.mark.parametrize("absolute", [False, True])
def test_import_into_a_configured_folder(fake, ytdlp_backend, tmp_path, absolute):
    data = tmp_path / "data"
    target = tmp_path / "outside" / "links" if absolute else data / "music" / "links"
    value = target.as_posix() if absolute else "music/links"
    assert cli.main(["-d", VIDEO, *use_import_dir(tmp_path, value)], data_dir=data) == 0
    assert (target / "Fake Artist - Fake Song.mp3").is_file()
    assert list(load_record(target)) == [VIDEO]
    assert not (data / "Imports").exists()


def test_import_refuses_a_playlists_folder_around_the_import_folder(fake, ytdlp_backend, tmp_path):
    store = SessionStore.load(tmp_path)
    store.playlists["p1"] = PlaylistState(name="Music", selected=False, folder="Music")
    store.save()
    assert cli.main(["-d", VIDEO, *use_import_dir(tmp_path, "music/links")], data_dir=tmp_path) == 1
    assert not fake.lists


def test_unusable_import_folder_is_an_error(fake, ytdlp_backend, tmp_path):
    (tmp_path / "blocked").write_bytes(b"a file, not a folder")
    assert cli.main(["-d", VIDEO, *use_import_dir(tmp_path, "blocked/links")], data_dir=tmp_path) == 1
    assert not fake.downloads


@pytest.mark.parametrize("value, folders", [
    # The folder around a nested import folder is kept free, "Imports" no longer is.
    ("music/links", {"p1": "Music (p1)", "p2": "Imports"}),
    # Outside the data folder: nothing is kept free.
    (None, {"p1": "Music", "p2": "Imports"}),
])
def test_fetch_keeps_the_configured_import_folder_free(tmp_path, monkeypatch, value, folders):
    data = tmp_path / "data"
    value = value or (tmp_path / "outside" / "Music").as_posix()
    client = FakeClient([Playlist("p1", "Music"), Playlist("p2", "Imports")])
    monkeypatch.setattr(cli, "make_client", lambda store: client)
    monkeypatch.setattr(ui, "choose_playlists", lambda playlists, preselected: ["p1", "p2"])
    assert cli.main(["-f", *use_import_dir(tmp_path, value)], data_dir=data) == 0
    assert {pid: s.folder for pid, s in SessionStore.load(data).playlists.items()} == folders
