"""SomeDL (https://github.com/ChemistryGull/SomeDL), the default backend.

SomeDL is run as a separate program, once per song, from inside the playlist
folder. Its own colorful output is shown as-is. It searches YouTube Music for
"Artist - Song" and tags the file with metadata it collects itself.

Fixing a file's metadata goes through ``somedl import``, which only has an
interactive wizard: its answers are piped in (written against SomeDL 1.7).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from spotarchive.backends.base import (
    BackendUnavailable,
    DownloadBackend,
    DownloadFailed,
    Song,
    TaggingFailed,
)

INSTALL_HINT = (
    'Install it with `pip install "spotarchive[somedl]"` (or `pipx install somedl`). '
    "See docs/backends.md#somedl."
)
# A query that is exactly one of these runs a SomeDL subcommand instead of a search.
_SUBCOMMANDS = {"import", "new-template", "update-metadata", "download", "sync", "web"}
# `somedl import` lists the files it handled in this file, one path per line.
_IMPORT_CACHE = "somedl_import_cache"
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def find_executable(name: str = "somedl") -> str | None:
    """Look next to the running Python first (an unactivated virtualenv), then on PATH."""
    return shutil.which(name, path=str(Path(sys.executable).parent)) or shutil.which(name)


class SomeDLBackend(DownloadBackend):
    name = "somedl"
    shows_own_output = True
    audio_extensions = frozenset({".mp3", ".m4a", ".opus", ".ogg", ".flac"})
    can_tag = True
    tags_downloads = True

    def __init__(self, executable: str | None = None, audio_format: str | None = None,
                 extra_args: tuple[str, ...] = (), flat: bool = True):
        # None means: find `somedl` / use SomeDL's own configured format.
        self.executable = executable
        self.audio_format = audio_format
        self.extra_args = tuple(extra_args)
        # False: SomeDL's own output template (e.g. artist/album subfolders)
        # inside the playlist folder instead of flat "Artist - Song" names.
        self.flat = flat

    def check(self) -> None:
        if self.executable is None:
            self.executable = find_executable()
        if self.executable is None:
            raise BackendUnavailable(f"SomeDL isn't installed. {INSTALL_HINT}")
        if shutil.which("ffmpeg") is None:
            raise BackendUnavailable(
                "SomeDL needs FFmpeg, which isn't installed or not on PATH. "
                "See docs/backends.md#somedl."
            )

    def warnings(self) -> list[str]:
        # yt-dlp looks for Deno in its own Python's scripts folder, then on
        # PATH; SomeDL's folder stands in for the first one (resolved, since
        # pipx links `somedl` from its own virtualenv into ~/.local/bin).
        folder = str(Path(self.executable).resolve().parent) if self.executable else None
        if (folder and shutil.which("deno", path=folder)) or shutil.which("deno"):
            return []
        return [
            "Deno isn't installed. yt-dlp, which SomeDL downloads with, needs it for YouTube; "
            "without it downloads may fail with HTTP 403. "
            'Install it with `pip install "yt-dlp[default,deno]"` next to SomeDL. '
            "See docs/backends.md#troubleshooting."
        ]

    def command(self, song: Song) -> list[str]:
        query = song_query(song)
        if query in _SUBCOMMANDS:
            raise DownloadFailed(f"can't search for {query!r} without an artist name")
        # --here: flat "{artist} - {song}" names in the working directory,
        # whatever the user's SomeDL config says; otherwise its template, but
        # still under the working directory. --skip-file-check: the caller
        # already decided this song is missing.
        where = ["--here"] if self.flat else ["--output", "."]
        cmd = [self.executable or "somedl", *where, "--skip-file-check"]
        if self.audio_format:
            cmd += ["--format", self.audio_format]
        # "--" so a query starting with "-" isn't read as an option.
        return [*cmd, *self.extra_args, "--", query]

    def download(self, song: Song, folder: Path) -> list[Path]:
        cmd = self.command(song)
        before = _audio_files(folder, self.audio_extensions)
        try:
            result = subprocess.run(cmd, cwd=folder, check=False)
        except OSError as exc:
            raise DownloadFailed(f"couldn't run SomeDL: {exc}") from exc
        # SomeDL exits with 0 even when a song fails, so look for a new file.
        new = [p for p, mtime in _audio_files(folder, self.audio_extensions).items() if before.get(p) != mtime]
        if result.returncode != 0:
            raise DownloadFailed(f"SomeDL exited with code {result.returncode}")
        if not new:
            raise DownloadFailed("SomeDL didn't produce a file (not found, or the download failed)")
        return sorted(new)

    def tag(self, song: Song, file: Path) -> None:
        # The wizard works on a whole folder and leaves its cache and an HTML
        # report there, so the file is moved alone into a temporary folder,
        # named after the setlist row: SomeDL searches with the file name.
        staging = Path(tempfile.mkdtemp(prefix=".somedl-import-", dir=file.parent))
        staged = staging / f"{_safe_name(song_query(song))}{file.suffix.lower()}"
        try:
            file.rename(staged)
        except OSError as exc:
            staging.rmdir()
            raise TaggingFailed(f"couldn't move {file.name}: {exc}") from exc
        # If moving it back fails, the staging folder (and the file) is kept.
        try:
            try:
                result = subprocess.run(
                    [self.executable or "somedl", "import"],
                    cwd=staging,
                    input=import_answers(staging),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    check=False,
                )
            except OSError as exc:
                raise TaggingFailed(f"couldn't run SomeDL: {exc}") from exc
            cache = staging / _IMPORT_CACHE
            handled = cache.read_text(encoding="utf-8").splitlines() if cache.is_file() else []
            if result.returncode != 0 or not any(Path(line).name == staged.name for line in handled):
                raise TaggingFailed(f"SomeDL couldn't identify the song{_last_line(result.stdout)}")
        finally:
            staged.rename(file)
            shutil.rmtree(staging, ignore_errors=True)


def import_answers(staging: Path) -> str:
    """Answers to the `somedl import` wizard: update the metadata of the files in *staging*."""
    folder = str(staging)
    return "\n".join([
        folder,               # 1/6 source folder
        folder,               # 2/6 destination (unused when updating)
        "{artist} - {song}",  # 3/6 output template (unused when updating)
        "n",                  # 4/6 recursive
        "update",             # 5/6 mode: rewrite the metadata in place
        "n",                  # 6/6 A: YouTube ID in the file name
        "y",                  #     B: the file name is "Artist - Song"
        "y",                  # start
    ]) + "\n"


def song_query(song: Song) -> str:
    return f"{song.artists[0]} - {song.name}" if song.artists else song.name


def _safe_name(name: str) -> str:
    return " ".join(_INVALID_CHARS.sub("_", name).split()) or "song"


def _last_line(output: str | None) -> str:
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    return f" ({lines[-1]})" if lines else ""


def _audio_files(folder: Path, extensions: frozenset[str]) -> dict[Path, int]:
    # Recursive: without --here, SomeDL's template may create subfolders.
    return {
        p: p.stat().st_mtime_ns
        for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in extensions
    }
