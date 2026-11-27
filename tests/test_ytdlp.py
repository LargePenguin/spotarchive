"""The yt-dlp backend, with subprocess.Popen faked: yt-dlp never actually runs."""

import io
import os
from pathlib import Path

import mutagen
import pytest

from conftest import SILENT_MP3
from spotarchive.backends import BackendUnavailable, DownloadEvents, DownloadFailed, Song, get_backend, ytdlp
from spotarchive.backends.ytdlp import OutputParser, YtDlpBackend
from spotarchive.session import YouTubeSession

SONG = Song("Song A", ("Fake Artist", "Other"), album="Fake Album", release_date="2001-02-03", duration_ms=200_000)
YOUTUBE = YouTubeSession(
    cookies=[{"name": "SAPISID", "value": "dummy-cookie", "domain": ".youtube.com", "path": "/",
              "expires": 4_000_000_000, "secure": True}],
    user_agent="dummy-agent",
)


class FakeProcess:
    def __init__(self, output, returncode):
        self.stdout = io.BytesIO(b"".join(output))
        self.returncode = returncode

    def wait(self):
        return self.returncode

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeRun:
    """Stands in for subprocess.Popen; "downloads" a file into the --paths folder."""

    def __init__(self):
        self.extension = "mp3"
        # yt-dlp's exit code: 101 when --max-downloads stopped it.
        self.returncode = 101
        self.output: list[bytes] = []
        self.calls = []

    def __call__(self, cmd, env=None, stdin=None, stdout=None, stderr=None):
        self.calls.append({"cmd": cmd, "env": env, "stdin": stdin})
        if self.extension:
            folder = Path(cmd[cmd.index("--paths") + 1])
            name = cmd[cmd.index("--output") + 1].replace("%%", "%").replace("%(ext)s", self.extension)
            (folder / name).write_bytes(SILENT_MP3 if self.extension == "mp3" else b"not audio")
            (folder / "thumbnail.webp").write_bytes(b"not audio")
        return FakeProcess(self.output, self.returncode)


@pytest.fixture
def run(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(ytdlp.subprocess, "Popen", fake)
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
    return YtDlpBackend(executable="yt-dlp", **kw)


def test_registered():
    assert isinstance(get_backend("ytdlp"), YtDlpBackend)


def test_command_searches_youtube_for_a_video_as_long_as_the_song(tmp_path):
    cmd = backend().command(SONG, tmp_path)
    assert cmd[0] == "yt-dlp"
    assert cmd[-2:] == ["--", "ytsearch5:Fake Artist - Song A"]
    assert cmd[cmd.index("--audio-format") + 1] == "mp3"
    # 200 s on Spotify, 30 s either way.
    assert cmd[cmd.index("--match-filters") + 1] == "!is_live & duration>=?170 & duration<=?231"
    assert cmd[cmd.index("--max-downloads") + 1] == "1"
    assert cmd[cmd.index("--paths") + 1] == str(tmp_path)
    assert cmd[cmd.index("--output") + 1] == "Fake Artist - Song A.%(ext)s"
    assert "--ignore-config" in cmd
    assert "--cookies" not in cmd


def test_length_filter_can_be_turned_off_and_needs_a_length(tmp_path):
    assert backend(length_tolerance=0).match_filter(SONG) == "!is_live"
    assert backend().match_filter(Song("Song A", ("Fake Artist",))) == "!is_live"


def test_command_options(tmp_path):
    song = Song("100% / Live: Mix", ())
    cmd = backend(audio_format="opus", search_results=3, extra_args=["--sleep-requests", "1"]).command(
        song, tmp_path, cookies=tmp_path / "cookies.txt")
    assert cmd[cmd.index("--audio-format") + 1] == "opus"
    # "%" would start a template field; "/" and ":" don't work in Windows file names.
    assert cmd[cmd.index("--output") + 1] == "100%% _ Live_ Mix.%(ext)s"
    # Extra options after Spotarchive's own (so they win), before the query.
    assert cmd[-4:] == ["--sleep-requests", "1", "--", "ytsearch3:100% / Live: Mix"]
    assert cmd.index("--cookies") < cmd.index("--sleep-requests")


@pytest.mark.parametrize("options, message", [
    ({"audio_format": "mp4"}, "audio_format"),
    ({"search_results": 0}, "search_results"),
    ({"length_tolerance": -1}, "length_tolerance"),
    ({"length_tolerance": True}, "length_tolerance"),
])
def test_bad_options(options, message):
    with pytest.raises(ValueError, match=message):
        YtDlpBackend(**options)


def test_download_moves_the_audio_in_and_tags_it_from_the_setlist(tmp_path, run):
    events = Events()
    files = backend().download(SONG, tmp_path, events)

    assert files == [tmp_path / "Fake Artist - Song A.mp3"]
    # Only the audio; the staging folder is gone.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Fake Artist - Song A.mp3"]
    tags = dict(mutagen.File(files[0], easy=True))
    assert tags == {"title": ["Song A"], "artist": ["Fake Artist", "Other"], "album": ["Fake Album"],
                    "date": ["2001-02-03"]}
    assert run.calls[0]["stdin"] is not None  # captured: no input
    assert "--newline" in run.calls[0]["cmd"]


def test_exit_code_zero_also_counts(tmp_path, run):
    run.returncode = 0
    assert backend().download(SONG, tmp_path)


def test_no_matching_video_fails(tmp_path, run):
    run.extension = None
    run.returncode = 0
    with pytest.raises(DownloadFailed, match="no YouTube video within 30 s"):
        backend().download(SONG, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_error_is_the_failure_reason(tmp_path, run):
    run.extension = None
    run.returncode = 1
    run.output = [b"WARNING: slow down\n", b"ERROR: [youtube] abc: Video unavailable\n"]
    events = Events()
    with pytest.raises(DownloadFailed, match=r"exited with code 1 \(\[youtube\] abc: Video unavailable\)"):
        backend().download(SONG, tmp_path, events)
    assert events.seen == [("warning", "slow down"), ("error", "[youtube] abc: Video unavailable")]


def test_failed_conversion_fails_even_with_a_file(tmp_path, run):
    run.returncode = 1
    with pytest.raises(DownloadFailed):
        backend().download(SONG, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_untaggable_file_is_only_a_warning(tmp_path, run):
    run.extension = "wav"
    events = Events()
    files = backend(audio_format="wav").download(SONG, tmp_path, events)
    assert [f.name for f in files] == ["Fake Artist - Song A.wav"]
    assert [kind for kind, _ in events.seen] == ["warning"]


def test_unrunnable_program_fails(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(ytdlp.subprocess, "Popen", boom)
    with pytest.raises(DownloadFailed, match="couldn't run yt-dlp"):
        backend().download(SONG, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_youtube_login_is_passed_as_a_private_cookie_file(tmp_path, monkeypatch, run):
    seen = {}

    def fake_popen(cmd, **kw):
        path = Path(cmd[cmd.index("--cookies") + 1])
        seen.update(path=path, text=path.read_text(encoding="utf-8"), mode=path.stat().st_mode & 0o777)
        return run(cmd, **kw)

    monkeypatch.setattr(ytdlp.subprocess, "Popen", fake_popen)
    b = backend()
    b.use_youtube_login(YOUTUBE)
    b.download(SONG, tmp_path)
    assert seen["text"] == YOUTUBE.netscape_cookies()
    if os.name == "posix":
        assert seen["mode"] == 0o600
    assert not seen["path"].exists()


def test_parser_reports_progress_warnings_and_errors():
    events = Events()
    parser = OutputParser(events)
    parser.feed(b"[youtube:search] Extracting URL\n[download]  12.5% of 3.20MiB at 1MiB/s\r[download] 1")
    parser.feed(b"00.0% of 3.20MiB\n\x1b[0;33mWARNING:\x1b[0m slow\n[ExtractAudio] Destination: x.mp3\nERROR: bad")
    parser.close()
    assert events.seen == [("progress", 0.125), ("progress", 1.0), ("warning", "slow"), ("progress", 1.0),
                           ("error", "bad")]
    assert parser.reason() == "bad"


def test_tag_writes_the_setlist_tags(tmp_path):
    path = tmp_path / "x.mp3"
    path.write_bytes(SILENT_MP3)
    backend().tag(SONG, path)
    assert mutagen.File(path, easy=True)["title"] == ["Song A"]


def test_check(monkeypatch):
    monkeypatch.setattr(ytdlp.shutil, "which", lambda name, path=None: None)
    with pytest.raises(BackendUnavailable, match="yt-dlp isn't installed"):
        YtDlpBackend().check()
    monkeypatch.setattr(ytdlp.shutil, "which", lambda name, path=None: None if name == "ffmpeg" else "/fake/yt-dlp")
    with pytest.raises(BackendUnavailable, match="FFmpeg"):
        YtDlpBackend().check()


def test_warns_without_deno(monkeypatch):
    monkeypatch.setattr(ytdlp.shutil, "which", lambda name, path=None: None)
    assert "Deno" in backend().warnings()[0]
    monkeypatch.setattr(ytdlp.shutil, "which", lambda name, path=None: "/usr/bin/deno")
    assert backend().warnings() == []


def test_download_url_gets_exactly_the_link(tmp_path, run):
    url = "https://video.example.com/watch?v=dummy"

    files = backend(extra_args=("--limit-rate", "1M")).download_url(url, tmp_path)

    assert files == [tmp_path / "download.mp3"]
    # Untagged: the caller tags each copy.
    assert files[0].read_bytes() == SILENT_MP3
    assert sorted(p.name for p in tmp_path.iterdir()) == ["download.mp3"]
    cmd = run.calls[0]["cmd"]
    assert cmd[-2:] == ["--", url]
    assert cmd[cmd.index("--match-filters") + 1] == "!is_live & !playlist_index"
    assert "--no-playlist" in cmd and "--limit-rate" in cmd
    assert "--max-downloads" not in cmd and not any(a.startswith("ytsearch") for a in cmd)


def test_download_url_of_a_playlist_fails(tmp_path, run):
    run.extension, run.returncode = None, 0

    with pytest.raises(DownloadFailed, match="not a playlist"):
        backend().download_url("https://video.example.com/playlist?list=dummy", tmp_path)

    assert list(tmp_path.iterdir()) == []
