"""Clearing the download cache, and moving files out of it."""

import errno
import os

import pytest

from spotarchive.backends import STAGING_PREFIX, util
from spotarchive.cache import clear_stale


def test_creates_a_missing_cache_folder(tmp_path):
    assert clear_stale(tmp_path / "a" / "cache") == (0, [])
    assert (tmp_path / "a" / "cache").is_dir()


def test_deletes_only_staging_folders(tmp_path):
    stale = tmp_path / f"{STAGING_PREFIX}abc"
    (stale / "sub").mkdir(parents=True)
    (stale / "sub" / "song.mp3.part").write_bytes(b"x")
    # The user's own things, in case cache_dir points somewhere shared.
    (tmp_path / "Holiday photos").mkdir()
    (tmp_path / "Holiday photos" / "a.jpg").write_bytes(b"x")
    (tmp_path / "song.mp3").write_bytes(b"x")
    (tmp_path / f"{STAGING_PREFIX}a-file").write_bytes(b"x")

    assert clear_stale(tmp_path) == (1, [])
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{STAGING_PREFIX}a-file", "Holiday photos", "song.mp3"]
    assert (tmp_path / "Holiday photos" / "a.jpg").exists()


def test_doesnt_follow_links(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.mp3").write_bytes(b"x")
    try:
        (cache / f"{STAGING_PREFIX}link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("can't create symlinks here")
    assert clear_stale(cache) == (0, [])
    assert (outside / "keep.mp3").exists()


def test_cache_that_is_a_file_is_an_error(tmp_path):
    (tmp_path / "cache").write_bytes(b"x")
    with pytest.raises(OSError):
        clear_stale(tmp_path / "cache")


def other_drive(monkeypatch, source):
    """Make renaming *source* fail like a rename onto another drive."""
    real = os.replace

    def replace(src, dst):
        if src == source:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real(src, dst)

    monkeypatch.setattr(util.os, "replace", replace)


def test_place_copies_across_drives(tmp_path, monkeypatch):
    source, target = tmp_path / "staged.mp3", tmp_path / "song.mp3"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    other_drive(monkeypatch, source)
    util.place(source, target)
    assert target.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["song.mp3"]


def test_interrupted_copy_leaves_no_partial_song(tmp_path, monkeypatch):
    source, target = tmp_path / "staged.mp3", tmp_path / "song.mp3"
    source.write_bytes(b"new")
    other_drive(monkeypatch, source)

    def copy(src, dst):
        dst.write_bytes(b"ne")
        raise KeyboardInterrupt

    monkeypatch.setattr(util.shutil, "copy2", copy)
    with pytest.raises(KeyboardInterrupt):
        util.place(source, target)
    assert [p.name for p in tmp_path.iterdir()] == ["staged.mp3"]


def test_place_passes_other_errors_on(tmp_path):
    with pytest.raises(FileNotFoundError):
        util.place(tmp_path / "missing.mp3", tmp_path / "song.mp3")
