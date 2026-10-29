import json

import pytest

from spotarchive import cli, ui
from spotarchive.backends import BackendUnavailable
from spotarchive.download import RECORD_FILENAME
from spotarchive.session import PlaylistState, SessionStore, SpotifySession
from spotarchive.setlist import read_setlist
from spotarchive.spotify.auth import LoginRequired
from spotarchive.spotify.client import RateLimited, SpotifyApiError
from spotarchive.spotify.models import Playlist, Track


class FakeClient:
    def __init__(self, playlists, tracks, failing=(), user_ok=True):
        self._playlists = playlists
        self._tracks = tracks
        self._failing = set(failing)
        self._user_ok = user_ok

    def current_user(self):
        if not self._user_ok:
            raise LoginRequired("The saved Spotify login has expired.")
        return {"id": "fake-user", "display_name": "Fake User"}

    def playlists(self):
        return self._playlists

    def playlist_tracks(self, playlist, on_progress=None):
        if playlist.id in self._failing:
            raise SpotifyApiError(403, "Forbidden")
        tracks = self._tracks.get(playlist.id, [])
        if on_progress:
            on_progress(len(tracks), len(tracks))
        return tracks


REAL_MAKE_BACKEND = cli.make_backend
PLAYLISTS = [Playlist("p1", "Road Trip", total=2), Playlist("p2", "Focus/Work", total=1)]
TRACKS = {"p1": [Track("Song A", ["Fake Artist"]), Track("Song B", ["Fake Artist"])], "p2": [Track("Song C")]}


@pytest.fixture
def logged_in(tmp_path):
    store = SessionStore.load(tmp_path)
    store.spotify = SpotifySession(cookies=[], user_agent="dummy-agent", access_token="dummy-token")
    store.save()
    return tmp_path


@pytest.fixture(autouse=True)
def fake_backend(backend, monkeypatch):
    # Tests never run a real download helper.
    monkeypatch.setattr(cli, "make_backend", lambda settings: backend)
    return backend


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient(PLAYLISTS, TRACKS)
    monkeypatch.setattr(cli, "make_client", lambda store: client)
    return client


def choose(monkeypatch, ids):
    seen = {}

    def fake_choose(playlists, preselected):
        seen["preselected"] = preselected
        return ids

    monkeypatch.setattr(ui, "choose_playlists", fake_choose)
    return seen


def test_fetch_writes_setlists_and_selection(logged_in, fake_client, monkeypatch):
    choose(monkeypatch, ["p1", "p2"])
    assert cli.main(["--fetch"], data_dir=logged_in) == 0

    assert [r["name"] for r in read_setlist(logged_in / "Road Trip")] == ["Song A", "Song B"]
    assert [r["name"] for r in read_setlist(logged_in / "Focus_Work")] == ["Song C"]
    store = SessionStore.load(logged_in)
    assert store.selected_ids() == ["p1", "p2"]
    assert store.playlists["p2"].folder == "Focus_Work"


def test_fetch_preselects_previous_choice(logged_in, fake_client, monkeypatch):
    choose(monkeypatch, ["p1"])
    cli.main(["-f"], data_dir=logged_in)
    seen = choose(monkeypatch, ["p1"])
    cli.main(["-f"], data_dir=logged_in)
    assert seen["preselected"] == ["p1"]


def test_noselect_reuses_previous_selection(logged_in, fake_client, monkeypatch):
    choose(monkeypatch, ["p2"])
    cli.main(["-f"], data_dir=logged_in)
    (logged_in / "Focus_Work" / "setlist.csv").unlink()
    monkeypatch.setattr(ui, "choose_playlists", lambda *a: pytest.fail("should not prompt"))

    assert cli.main(["-fn"], data_dir=logged_in) == 0
    assert (logged_in / "Focus_Work" / "setlist.csv").exists()
    assert not (logged_in / "Road Trip").exists()


def test_noselect_without_previous_selection_fails(logged_in, fake_client):
    assert cli.main(["-fn"], data_dir=logged_in) == 1


def test_noselect_requires_fetch(logged_in):
    with pytest.raises(SystemExit):
        cli.main(["--noselect"], data_dir=logged_in)


def test_renamed_playlist_moves_its_folder(logged_in, fake_client, monkeypatch):
    choose(monkeypatch, ["p1"])
    cli.main(["-f"], data_dir=logged_in)
    (logged_in / "Road Trip" / "song.mp3").write_bytes(b"fake")

    fake_client._playlists = [Playlist("p1", "Road Trip 2024"), PLAYLISTS[1]]
    cli.main(["-fn"], data_dir=logged_in)
    assert (logged_in / "Road Trip 2024" / "song.mp3").exists()
    assert not (logged_in / "Road Trip").exists()


def test_failed_playlist_doesnt_stop_others(logged_in, fake_client, monkeypatch):
    fake_client._failing = {"p1"}
    choose(monkeypatch, ["p1", "p2"])
    assert cli.main(["-f"], data_dir=logged_in) == 1
    assert (logged_in / "Focus_Work" / "setlist.csv").exists()


def test_cancelled_selection_exits_130(logged_in, fake_client, monkeypatch):
    choose(monkeypatch, None)
    assert cli.main(["-f"], data_dir=logged_in) == 130


def test_vanished_playlist_is_dropped(logged_in, fake_client, monkeypatch):
    store = SessionStore.load(logged_in)
    store.playlists = {"gone": PlaylistState("Deleted Mix", selected=True), "p1": PlaylistState("Road Trip", True)}
    store.save()
    assert cli.main(["-fn"], data_dir=logged_in) == 0
    assert "gone" not in SessionStore.load(logged_in).playlists


def test_fetch_without_login_asks_to_log_in(tmp_path, capsys):
    assert cli.main(["-f"], data_dir=tmp_path) == 1
    assert "--login" in capsys.readouterr().out


def fake_login(monkeypatch, calls):
    def login(profile_dir, on_status=None):
        calls.append(1)
        return SpotifySession(cookies=[], user_agent="dummy-agent", access_token="dummy-token")

    monkeypatch.setattr(cli, "interactive_login", login)


def test_login_saves_user(tmp_path, fake_client, monkeypatch):
    calls = []
    fake_login(monkeypatch, calls)
    assert cli.main(["-l"], data_dir=tmp_path) == 0
    assert calls == [1]
    assert SessionStore.load(tmp_path).spotify.display_name == "Fake User"


def test_default_run_logs_in_when_needed_then_fetches(tmp_path, fake_client, monkeypatch):
    calls = []
    fake_login(monkeypatch, calls)
    choose(monkeypatch, ["p1"])
    assert cli.main([], data_dir=tmp_path) == 0
    assert calls == [1]
    assert (tmp_path / "Road Trip" / "setlist.csv").exists()


def test_default_run_skips_login_when_it_works(logged_in, fake_client, monkeypatch):
    monkeypatch.setattr(cli, "interactive_login", lambda **kw: pytest.fail("should not log in"))
    choose(monkeypatch, ["p1"])
    assert cli.main([], data_dir=logged_in) == 0


def test_default_run_relogs_in_when_login_expired(logged_in, fake_client, monkeypatch):
    calls = []
    fake_login(monkeypatch, calls)
    fake_client._user_ok = False
    choose(monkeypatch, [])

    def login(profile_dir, on_status=None):
        calls.append(1)
        fake_client._user_ok = True
        return SpotifySession(cookies=[], user_agent="dummy-agent", access_token="dummy-token")

    monkeypatch.setattr(cli, "interactive_login", login)
    assert cli.main([], data_dir=logged_in) == 0
    assert calls == [1]


def rate_limited():
    raise RateLimited("API rate limit exceeded", retry_after_s=7200)


def test_login_is_kept_when_rate_limited(tmp_path, fake_client, monkeypatch, capsys):
    fake_login(monkeypatch, [])
    fake_client.current_user = rate_limited
    assert cli.main(["-l"], data_dir=tmp_path) == 1
    assert SessionStore.load(tmp_path).spotify.access_token == "dummy-token"
    out = capsys.readouterr().out
    assert "rate-limiting" in out
    assert "about 2 hours" in out


def test_rate_limit_doesnt_trigger_relogin(logged_in, fake_client, monkeypatch):
    monkeypatch.setattr(cli, "interactive_login", lambda *a, **kw: pytest.fail("should not log in"))
    fake_client.current_user = rate_limited
    assert cli.main([], data_dir=logged_in) == 1


def test_default_run_downloads_after_fetching(logged_in, fake_client, fake_backend, monkeypatch):
    choose(monkeypatch, ["p1"])
    assert cli.main([], data_dir=logged_in) == 0
    assert fake_backend.requested == ["Song A", "Song B"]
    assert (logged_in / "Road Trip" / "YT Fake Artist - Song A.mp3").exists()


def test_download_skips_songs_already_there(logged_in, fake_client, fake_backend, monkeypatch):
    choose(monkeypatch, ["p1", "p2"])
    cli.main(["-fd"], data_dir=logged_in)
    assert fake_backend.requested == ["Song A", "Song B", "Song C"]

    fake_backend.requested.clear()
    (logged_in / "Road Trip" / "YT Fake Artist - Song B.mp3").unlink()
    assert cli.main(["-d"], data_dir=logged_in) == 0
    assert fake_backend.requested == ["Song B"]


def test_download_doesnt_need_spotify(logged_in, fake_client, fake_backend, monkeypatch):
    choose(monkeypatch, ["p2"])
    cli.main(["-f"], data_dir=logged_in)
    monkeypatch.setattr(cli, "make_client", lambda store: pytest.fail("should not talk to Spotify"))
    assert cli.main(["--download"], data_dir=logged_in) == 0
    record = json.loads((logged_in / "Focus_Work" / RECORD_FILENAME).read_text(encoding="utf-8"))
    assert [s["files"] for s in record["songs"].values()] == [["YT Song C.mp3"]]


def test_failed_song_doesnt_stop_others(logged_in, fake_client, fake_backend, monkeypatch, capsys):
    choose(monkeypatch, ["p1"])
    cli.main(["-f"], data_dir=logged_in)
    fake_backend.failing = {"Song A"}
    assert cli.main(["-d"], data_dir=logged_in) == 1
    assert fake_backend.requested == ["Song A", "Song B"]
    assert "1 songs couldn't be downloaded" in capsys.readouterr().out

    fake_backend.failing = set()
    fake_backend.requested.clear()
    assert cli.main(["-d"], data_dir=logged_in) == 0
    assert fake_backend.requested == ["Song A"]


def test_download_before_fetch_fails(logged_in, capsys):
    assert cli.main(["-d"], data_dir=logged_in) == 1
    assert "--fetch" in capsys.readouterr().out


def test_download_reports_missing_setlist(logged_in, fake_client, fake_backend, monkeypatch, capsys):
    choose(monkeypatch, ["p1", "p2"])
    cli.main(["-f"], data_dir=logged_in)
    (logged_in / "Road Trip" / "setlist.csv").unlink()
    assert cli.main(["-d"], data_dir=logged_in) == 1
    assert fake_backend.requested == ["Song C"]
    assert "1 playlists have no setlist" in capsys.readouterr().out


def test_unavailable_backend_is_reported(logged_in, fake_client, fake_backend, monkeypatch, capsys):
    choose(monkeypatch, ["p1"])
    cli.main(["-f"], data_dir=logged_in)
    fake_backend.unavailable = BackendUnavailable("SomeDL isn't installed.")
    assert cli.main(["-d"], data_dir=logged_in) == 1
    assert "SomeDL isn't installed." in capsys.readouterr().out
    assert fake_backend.requested == []


def test_backend_warnings_dont_stop_downloads(logged_in, fake_client, fake_backend, monkeypatch, capsys):
    choose(monkeypatch, ["p1"])
    cli.main(["-f"], data_dir=logged_in)
    monkeypatch.setattr(fake_backend, "warnings", lambda: ["Deno isn't installed."])
    assert cli.main(["-d"], data_dir=logged_in) == 0
    assert "Deno isn't installed." in capsys.readouterr().out
    assert fake_backend.requested == ["Song A", "Song B"]


def test_backend_with_own_output_gets_a_line_per_song(logged_in, fake_client, fake_backend, monkeypatch, capsys):
    choose(monkeypatch, ["p1"])
    cli.main(["-f"], data_dir=logged_in)
    fake_backend.shows_own_output = True
    fake_backend.failing = {"Song B"}
    assert cli.main(["-d"], data_dir=logged_in) == 1
    out = capsys.readouterr().out
    assert "[1/2] Fake Artist - Song A" in out
    assert "Fake Artist - Song B: not found" in out


def song_tags(path):
    import mutagen

    return dict(mutagen.File(path, easy=True))


def test_default_run_fixes_metadata_after_downloading(logged_in, fake_client, monkeypatch, capsys):
    choose(monkeypatch, ["p1"])
    assert cli.main([], data_dir=logged_in) == 0
    # The fake backend can't tag, so the setlist's metadata is used.
    assert song_tags(logged_in / "Road Trip" / "YT Fake Artist - Song A.mp3")["title"] == ["Song A"]
    assert "Road Trip: 2 fixed, 0 already fine" in capsys.readouterr().out


def test_meta_uses_the_backend_when_it_can_tag(logged_in, fake_client, fake_backend, monkeypatch):
    fake_backend.can_tag = True
    choose(monkeypatch, ["p1"])
    cli.main(["-fd"], data_dir=logged_in)
    assert cli.main(["-m"], data_dir=logged_in) == 0
    assert fake_backend.tagged == ["YT Fake Artist - Song A.mp3", "YT Fake Artist - Song B.mp3"]


def test_meta_source_from_config(logged_in, fake_client, fake_backend, monkeypatch, tmp_path_factory):
    fake_backend.can_tag = True
    path = tmp_path_factory.mktemp("cfg") / "spotarchive.toml"
    path.write_text('[metadata]\nsource = "setlist"\n', encoding="utf-8")
    choose(monkeypatch, ["p1"])
    assert cli.main(["-fdm", "--config", str(path)], data_dir=logged_in) == 0
    assert fake_backend.tagged == []
    assert song_tags(logged_in / "Road Trip" / "YT Fake Artist - Song B.mp3")["artist"] == ["Fake Artist"]


def test_meta_still_runs_when_a_download_failed(logged_in, fake_client, fake_backend, monkeypatch, capsys):
    choose(monkeypatch, ["p1"])
    cli.main(["-f"], data_dir=logged_in)
    fake_backend.failing = {"Song A"}
    assert cli.main(["-dm"], data_dir=logged_in) == 1
    assert "Road Trip: 1 fixed" in capsys.readouterr().out


def test_meta_before_fetch_fails(logged_in):
    assert cli.main(["-m"], data_dir=logged_in) == 1


def test_config_sets_backend_and_data_dir(tmp_path, monkeypatch, capsys):
    data = tmp_path / "music"
    path = tmp_path / "spotarchive.toml"
    path.write_text(f'data_dir = "{data.as_posix()}"\n[download]\nbackend = "nope"\n', encoding="utf-8")
    store = SessionStore.load(data)
    store.playlists["p1"] = PlaylistState("Road Trip", selected=True, folder="Road Trip")
    store.save()
    monkeypatch.setattr(cli, "make_backend", REAL_MAKE_BACKEND)

    assert cli.main(["-d", "--config", str(path)]) == 1
    # Got past finding the fetched playlist in data_dir, then the backend.
    assert "Unknown download backend 'nope'" in capsys.readouterr().out


def test_bad_config_fails(logged_in, tmp_path, capsys):
    path = tmp_path / "bad.toml"
    path.write_text("[metadata]\nsource = 'spotify'\n", encoding="utf-8")
    assert cli.main(["-m", "--config", str(path)], data_dir=logged_in) == 1
    assert "metadata.source must be one of" in capsys.readouterr().out


def test_unknown_backend_option_fails(tmp_path):
    settings = cli.Config(path=tmp_path / "spotarchive.toml", backend_options={"somedl": {"colour": "blue"}})
    with pytest.raises(cli.ConfigError, match=r"\[backends.somedl\]"):
        REAL_MAKE_BACKEND(settings)
