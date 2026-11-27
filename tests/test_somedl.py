"""The SomeDL backend, with subprocess.Popen and subprocess.run faked: SomeDL never actually runs."""

import io
import os
import subprocess
from pathlib import Path

import pytest

from spotarchive.backends import BackendUnavailable, DownloadEvents, DownloadFailed, Song, TaggingFailed, get_backend, somedl, util
from spotarchive.backends.somedl import SomeDLBackend
from spotarchive.session import YouTubeSession

SONG = Song("Song A", ("Fake Artist", "Other"))


class FakeProcess:
    def __init__(self, chunks, returncode):
        self.stdout = io.BytesIO(b"".join(chunks))
        self.returncode = returncode

    def wait(self):
        return self.returncode

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeRun:
    """Stands in for subprocess.Popen; "downloads" a file into the working directory."""

    def __init__(self):
        self.filename = "Fake Artist - Song A.mp3"
        self.returncode = 0
        # What SomeDL "prints".
        self.output: list[bytes] = []
        self.calls = []

    def __call__(self, cmd, cwd=None, env=None, stdin=None, stdout=None, stderr=None):
        self.calls.append({"cmd": cmd, "cwd": Path(cwd), "env": env, "stdin": stdin})
        if self.filename:
            (Path(cwd) / self.filename).parent.mkdir(parents=True, exist_ok=True)
            (Path(cwd) / self.filename).write_bytes(b"fake audio")
        return FakeProcess(self.output, self.returncode)


@pytest.fixture
def run(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(somedl.subprocess, "Popen", fake)
    return fake


class Events(DownloadEvents):
    def __init__(self, show_output=False):
        super().__init__(show_output)
        self.seen = []

    def progress(self, fraction):
        self.seen.append(("progress", fraction))

    def warning(self, message):
        self.seen.append(("warning", message))

    def error(self, message):
        self.seen.append(("error", message))


def backend(**kw):
    return SomeDLBackend(executable="somedl", **kw)


def test_default_backend_is_somedl():
    assert isinstance(get_backend(), SomeDLBackend)


def test_unknown_backend():
    with pytest.raises(BackendUnavailable, match="known: somedl"):
        get_backend("nope")


def test_command_searches_first_artist_and_title():
    assert backend().command(SONG) == ["somedl", "--here", "--skip-file-check", "--", "Fake Artist - Song A"]


def test_command_passes_format_and_extra_args():
    cmd = backend(audio_format="opus", extra_args=("--sleep", "5")).command(SONG)
    assert cmd == ["somedl", "--here", "--skip-file-check", "--format", "opus", "--sleep", "5",
                   "--", "Fake Artist - Song A"]


def test_query_never_runs_a_subcommand():
    with pytest.raises(DownloadFailed):
        backend().command(Song("import"))


def test_download_returns_the_new_file(tmp_path, run):
    (tmp_path / "Older - Song.mp3").write_bytes(b"x")
    assert backend().download(SONG, tmp_path, Events()) == [tmp_path / "Fake Artist - Song A.mp3"]
    assert (tmp_path / "Older - Song.mp3").exists()


def test_downloads_in_a_private_folder_that_is_removed(tmp_path, run):
    backend().download(SONG, tmp_path, Events())
    staging = run.calls[0]["cwd"]
    assert staging.parent == tmp_path and staging.name.startswith(".")
    assert not staging.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Fake Artist - Song A.mp3"]


def test_staging_folder_name_is_short(tmp_path, run):
    backend().download(SONG, tmp_path, Events())
    assert len(run.calls[0]["cmd"]) and len(run.calls[0]["cwd"].name) <= 16


def test_fit_name_leaves_a_fitting_name_alone(tmp_path):
    assert util.fit_name(tmp_path, "Fake Artist - Song A.mp3") == "Fake Artist - Song A.mp3"


def test_fit_name_cuts_the_stem_and_keeps_the_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(util, "path_limit", lambda: len(str(tmp_path)) + 1 + 20)
    name = util.fit_name(tmp_path, "A very long song title that goes on.mp3")
    assert name.endswith(".mp3") and util._size(name) <= 20 and name.startswith("A very long")


def test_fit_name_counts_in_the_units_of_the_file_system(tmp_path, monkeypatch):
    monkeypatch.setattr(util, "path_limit", lambda: 1_000_000)
    monkeypatch.setattr(util, "_NAME_LIMIT", 20)
    name = util.fit_name(tmp_path, "ñandú ñandú ñandú ñandú.mp3")
    assert name.endswith(".mp3") and util._size(name) <= 20
    name.encode("utf-8")  # still valid text, never cut in the middle of a character


def test_fit_name_fails_without_room(tmp_path, monkeypatch):
    monkeypatch.setattr(util, "path_limit", lambda: len(str(tmp_path)) + 3)
    with pytest.raises(DownloadFailed, match="no room"):
        util.fit_name(tmp_path, "Song.mp3")


def test_long_file_names_are_shortened_with_a_warning(tmp_path, run, monkeypatch):
    monkeypatch.setattr(util, "path_limit", lambda: len(str(tmp_path)) + 1 + 30)
    run.filename = "Fake Artist - A song title that is far too long.mp3"
    events = Events()
    (moved,) = backend().download(SONG, tmp_path, events)
    assert moved.parent == tmp_path and moved.suffix == ".mp3" and len(moved.name) <= 30
    assert moved.read_bytes() == b"fake audio"
    warnings = [m for kind, m in events.seen if kind == "warning"]
    assert len(warnings) == 1 and "shortened" in warnings[0] and run.filename in warnings[0] and moved.name in warnings[0]


def test_fitting_file_names_give_no_warning(tmp_path, run):
    events = Events()
    backend().download(SONG, tmp_path, events)
    assert not [1 for kind, _ in events.seen if kind == "warning"]


def test_too_long_path_is_retried_in_a_shorter_folder(tmp_path, monkeypatch):
    short = tmp_path / "t"
    short.mkdir()
    folder = tmp_path / "a-playlist-folder-with-a-rather-long-name"
    folder.mkdir()
    monkeypatch.setattr(somedl.tempfile, "gettempdir", lambda: str(short))
    cwds = []

    def popen(cmd, cwd=None, **kw):
        cwds.append(Path(cwd))
        if len(cwds) == 1:
            line = b"1/1 Song: ERROR - yt-dlp: ERROR: unable to open for writing: [Errno 2] No such file\n"
            return FakeProcess([line], 0)
        (Path(cwd) / "Fake Artist - Song A.mp3").write_bytes(b"fake audio")
        return FakeProcess([], 0)

    monkeypatch.setattr(somedl.subprocess, "Popen", popen)
    events = Events()
    assert backend().download(SONG, folder, events) == [folder / "Fake Artist - Song A.mp3"]
    assert cwds[0].parent == folder and cwds[1].parent == short
    assert any(kind == "warning" and "too long" in m for kind, m in events.seen)
    assert list(short.iterdir()) == []


def test_too_long_path_without_a_shorter_folder_fails(tmp_path, run, monkeypatch):
    monkeypatch.setattr(somedl.tempfile, "gettempdir", lambda: str(tmp_path / "much-longer-than-the-folder"))
    run.filename = None
    run.output = [b"1/1 Song: ERROR - yt-dlp: ERROR: unable to open for writing: [Errno 2] No such file\n"]
    with pytest.raises(DownloadFailed, match="path too long"):
        backend().download(SONG, tmp_path, Events())


def test_concurrent_downloads_keep_their_own_files(tmp_path, monkeypatch):
    # Another song finishing in the same folder meanwhile isn't taken for this one.
    def popen(cmd, cwd=None, **kw):
        (tmp_path / "Someone Else - Other Song.mp3").write_bytes(b"other")
        (Path(cwd) / "Fake Artist - Song A.mp3").write_bytes(b"mine")
        return FakeProcess([], 0)

    monkeypatch.setattr(somedl.subprocess, "Popen", popen)
    assert backend().download(SONG, tmp_path, Events()) == [tmp_path / "Fake Artist - Song A.mp3"]


def test_ignores_non_audio_files(tmp_path, run):
    run.filename = "Download Report.html"
    with pytest.raises(DownloadFailed, match="didn't produce a file"):
        backend().download(SONG, tmp_path, Events())


def test_replaces_a_file_that_is_there(tmp_path, run):
    old = tmp_path / "Fake Artist - Song A.mp3"
    old.write_bytes(b"old")
    assert backend().download(SONG, tmp_path, Events()) == [old]
    assert old.read_bytes() == b"fake audio"


def test_nonzero_exit_fails(tmp_path, run):
    run.returncode = 2
    with pytest.raises(DownloadFailed, match="code 2"):
        backend().download(SONG, tmp_path, Events())
    assert list(tmp_path.iterdir()) == []


YOUTUBE = YouTubeSession(
    cookies=[{"name": "SAPISID", "value": "dummy-cookie", "domain": ".youtube.com", "path": "/",
              "expires": 4_000_000_000, "secure": True}],
    user_agent="dummy-agent",
)


def test_command_without_youtube_login_passes_no_cookies():
    assert "--cookies" not in backend().command(SONG)


def test_youtube_login_is_passed_as_a_private_cookie_file(tmp_path, monkeypatch, run):
    seen = {}

    def fake_popen(cmd, **kw):
        path = Path(cmd[cmd.index("--cookies") + 1])
        seen["path"] = path
        seen["text"] = path.read_text(encoding="utf-8")
        seen["mode"] = path.stat().st_mode & 0o777
        seen["before_query"] = cmd.index("--cookies") < cmd.index("--")
        return run(cmd, **kw)

    monkeypatch.setattr(somedl.subprocess, "Popen", fake_popen)
    b = backend()
    b.use_youtube_login(YOUTUBE)
    b.download(SONG, tmp_path)

    assert seen["text"] == YOUTUBE.netscape_cookies()
    assert seen["before_query"]
    if os.name == "posix":
        assert seen["mode"] == 0o600
    # Deleted once SomeDL is done.
    assert not seen["path"].exists()


def test_cookie_file_is_deleted_when_somedl_cant_run(tmp_path, monkeypatch):
    paths = []

    def boom(cmd, **kw):
        paths.append(Path(cmd[cmd.index("--cookies") + 1]))
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(somedl.subprocess, "Popen", boom)
    b = backend()
    b.use_youtube_login(YOUTUBE)
    with pytest.raises(DownloadFailed):
        b.download(SONG, tmp_path)
    assert paths and not paths[0].exists()


def test_unrunnable_program_fails(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(somedl.subprocess, "Popen", boom)
    with pytest.raises(DownloadFailed, match="couldn't run SomeDL"):
        backend().download(SONG, tmp_path, Events())


# Captured SomeDL 1.7 output: messages, and its live table redrawn in place.
LIVE_FRAME = (
    "\x1b[?25l\r\x1b[2K\x1b[1A\x1b[2K  \x1b[1;36mDownloading\x1b[0m  \x1b[1;36mStatus\x1b[0m\n"
    " 1/1 Fake Artist - Song A  yt-dlp: Downloading \x1b[34m 42.5%\x1b[0m of 3.10MiB at \x1b[32m1.2MiB/s\x1b[0m\r"
)


def test_reports_progress_warnings_and_errors(tmp_path, run):
    run.output = [
        b"\x1b[90m1/1 Fake Artist - Song A:\x1b[0m \x1b[33mWARNING - Lyrics not found\x1b[0m\n",
        LIVE_FRAME.encode()[:40], LIVE_FRAME.encode()[40:],
        " 1/1 Fake Artist - Song A  yt-dlp: Postprocessing  (Got 3.10MiB)\n".encode(),
        b"1/1 Fake Artist - Song A: \x1b[31mERROR - Genre lookup failed.\x1b[0m\n",
        b"1/1 Fake Artist - Song A: \x1b[31mERROR - HTTP 503\x1b[0m\n",
        b"  Downloaded successfully:  1/1 Fake Artist - Song A\n",
    ]
    events = Events()
    backend().download(SONG, tmp_path, events)
    assert events.seen == [
        ("warning", "Lyrics not found"),
        ("progress", 0.425),
        ("progress", 1.0),
        ("error", "Genre lookup failed. HTTP 503"),
    ]


def test_wrapped_messages_are_joined(tmp_path, run):
    # On a narrow terminal SomeDL wraps long messages; its table lines are indented.
    run.output = [
        b"1/1 Fake Artist - Song A: WARNING - Lyrics not found for this song, and\n",
        b"this message goes on\n",
        b" 1/1 Fake Artist - Song A  yt-dlp: Downloading  10.0% of 3.10MiB\n",
        b"1/1 Fake Artist - Song A: ERROR - yt-dlp download failed.\n",
        b"\x1b[2K 1/1 Fake Artist - Song A  yt-dlp: Preparing download\n",
        b"1/1 Fake Artist - Song A: ERROR - Perhaps there was a network error, you\n",
        b"can try again.\n",
    ]
    events = Events()
    backend().download(SONG, tmp_path, events)
    assert events.seen == [
        ("warning", "Lyrics not found for this song, and this message goes on"),
        ("progress", 0.1),
        ("error", "yt-dlp download failed. Perhaps there was a network error, you can try again."),
    ]


def test_failure_reason_comes_from_somedls_output(tmp_path, run):
    run.filename = None
    run.output = [
        b"1/1 Fake Artist - Song A: WARNING - Song could not be downloaded, error on metadata fetching.\n",
        "  Downloaded failed:        1/1 Fake Artist - Song A\n".encode(),
    ]
    with pytest.raises(DownloadFailed, match="error on metadata fetching"):
        backend().download(SONG, tmp_path, Events())


def test_captured_output_makes_somedl_draw_its_table(tmp_path, run, capsys):
    run.output = [b"1/1 Fake Artist - Song A: INFO - hello\n"]
    backend().download(SONG, tmp_path, Events(show_output=False))
    call = run.calls[0]
    assert call["env"]["FORCE_COLOR"] == "1" and call["env"]["TTY_COMPATIBLE"] == "1"
    assert call["stdin"] == subprocess.DEVNULL
    assert "hello" not in capsys.readouterr().out


def test_shown_output_is_passed_on(tmp_path, run, capsys):
    run.output = ["1/1 Fake Artist - Song A: INFO - h\u00e9llo\n".encode()]
    backend().download(SONG, tmp_path, Events(show_output=True))
    assert "h\u00e9llo" in capsys.readouterr().out
    # Not a terminal: SomeDL prints plain lines, as when run directly.
    assert run.calls[0]["env"].get("TTY_COMPATIBLE") != "1"
    assert run.calls[0]["stdin"] is None


def test_check_needs_somedl(monkeypatch):
    monkeypatch.setattr(somedl.shutil, "which", lambda name, path=None: None)
    with pytest.raises(BackendUnavailable, match="SomeDL isn't installed"):
        SomeDLBackend().check()


def test_check_needs_ffmpeg(monkeypatch):
    monkeypatch.setattr(somedl.shutil, "which", lambda name, path=None: None if name == "ffmpeg" else "/fake/somedl")
    found = SomeDLBackend()
    with pytest.raises(BackendUnavailable, match="FFmpeg"):
        found.check()
    assert found.executable == "/fake/somedl"


def test_warns_without_deno(monkeypatch):
    monkeypatch.setattr(somedl.shutil, "which", lambda name, path=None: None)
    assert "Deno" in SomeDLBackend("/fake/bin/somedl").warnings()[0]


def test_deno_next_to_somedl_is_enough(monkeypatch):
    # Like yt-dlp, which finds Deno in SomeDL's own environment without PATH.
    folder = str(Path("/fake/bin/somedl").resolve().parent)
    looked_in = []

    def which(name, path=None):
        looked_in.append(path)
        return "/fake/bin/deno" if path == folder else None

    monkeypatch.setattr(somedl.shutil, "which", which)
    assert SomeDLBackend("/fake/bin/somedl").warnings() == []
    assert looked_in == [folder]


def test_deno_on_path_is_enough(monkeypatch):
    monkeypatch.setattr(somedl.shutil, "which", lambda name, path=None: None if path else "/usr/bin/deno")
    assert SomeDLBackend("/fake/bin/somedl").warnings() == []


def test_command_without_flat_uses_somedls_template():
    assert backend(flat=False).command(SONG)[:3] == ["somedl", "--output", "."]


def test_download_finds_files_in_subfolders(tmp_path, run):
    run.filename = "Fake Artist/Song A.mp3"
    assert backend(flat=False).download(SONG, tmp_path, Events()) == [tmp_path / "Fake Artist" / "Song A.mp3"]


class FakeImport:
    """Stands in for `somedl import`: answers the wizard like SomeDL 1.7 would."""

    def __init__(self, identifies=True, returncode=0):
        self.identifies = identifies
        self.returncode = returncode
        self.calls = []

    def __call__(self, cmd, cwd=None, input=None, **kw):
        answers = input.splitlines()
        source = Path(answers[0])
        files = sorted(p.name for p in source.iterdir())
        self.calls.append({"cmd": cmd, "cwd": cwd, "answers": answers, "files": files})
        assert answers[4] == "update"
        (source / "Download Report.html").write_text("report")
        if self.identifies:
            for name in files:
                with open(source / "somedl_import_cache", "a", encoding="utf-8") as f:
                    f.write(f"{source / name}\n")
        return subprocess.CompletedProcess(cmd, self.returncode, stdout="Could not identify song\n")


def test_tag_imports_the_file_alone_named_after_the_setlist(tmp_path, monkeypatch):
    fake = FakeImport()
    monkeypatch.setattr(somedl.subprocess, "run", fake)
    (tmp_path / "other.mp3").write_bytes(b"other")
    file = tmp_path / "Whatever YouTube called it.mp3"
    file.write_bytes(b"audio")

    backend().tag(Song("Song: A", ("AC/DC",)), file)

    call = fake.calls[0]
    assert call["cmd"] == ["somedl", "import"]
    assert call["files"] == ["AC_DC - Song_ A.mp3"]
    assert call["cwd"] == Path(call["answers"][0])
    assert file.read_bytes() == b"audio"
    # Only the two songs are left: no staging folder, cache or report.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Whatever YouTube called it.mp3", "other.mp3"]


@pytest.mark.parametrize("fake", [FakeImport(identifies=False), FakeImport(returncode=1)])
def test_tag_fails_when_somedl_didnt_handle_the_file(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(somedl.subprocess, "run", fake)
    file = tmp_path / "song.mp3"
    file.write_bytes(b"audio")
    with pytest.raises(TaggingFailed, match="Could not identify song"):
        backend().tag(SONG, file)
    assert [p.name for p in tmp_path.iterdir()] == ["song.mp3"]
