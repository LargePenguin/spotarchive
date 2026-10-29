"""The SomeDL backend, with subprocess.run faked: SomeDL never actually runs."""

import os
import subprocess
from pathlib import Path

import pytest

from spotarchive.backends import BackendUnavailable, DownloadFailed, Song, TaggingFailed, get_backend, somedl
from spotarchive.backends.somedl import SomeDLBackend

SONG = Song("Song A", ("Fake Artist", "Other"))


class FakeRun:
    """Stands in for subprocess.run; "downloads" a file into the working directory."""

    def __init__(self):
        self.filename = "Fake Artist - Song A.mp3"
        self.returncode = 0
        self.calls = []

    def __call__(self, cmd, cwd=None, check=False):
        self.calls.append((cmd, cwd))
        if self.filename:
            (cwd / self.filename).write_bytes(b"fake audio")
        return subprocess.CompletedProcess(cmd, self.returncode)


@pytest.fixture
def run(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(somedl.subprocess, "run", fake)
    return fake


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
    assert backend().download(SONG, tmp_path) == [tmp_path / "Fake Artist - Song A.mp3"]
    assert run.calls[0][1] == tmp_path


def test_ignores_non_audio_files(tmp_path, run):
    run.filename = "Download Report.html"
    with pytest.raises(DownloadFailed, match="didn't produce a file"):
        backend().download(SONG, tmp_path)


def test_overwritten_file_counts_as_new(tmp_path, run):
    old = tmp_path / "Fake Artist - Song A.mp3"
    old.write_bytes(b"old")
    os.utime(old, ns=(0, 0))
    assert backend().download(SONG, tmp_path) == [old]


def test_nonzero_exit_fails(tmp_path, run):
    run.returncode = 2
    with pytest.raises(DownloadFailed, match="code 2"):
        backend().download(SONG, tmp_path)


def test_unrunnable_program_fails(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(somedl.subprocess, "run", boom)
    with pytest.raises(DownloadFailed, match="couldn't run SomeDL"):
        backend().download(SONG, tmp_path)


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
    (tmp_path / "Fake Artist").mkdir()
    run.filename = "Fake Artist/Song A.mp3"
    assert backend(flat=False).download(SONG, tmp_path) == [tmp_path / "Fake Artist" / "Song A.mp3"]


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
