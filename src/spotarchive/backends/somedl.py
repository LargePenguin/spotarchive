"""SomeDL (https://github.com/ChemistryGull/SomeDL), the default backend.

SomeDL is run as a separate program, once per song, from inside the playlist
folder. Its own colorful output is shown as-is. It searches YouTube Music for
"Artist - Song" and tags the file with metadata it collects itself.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from spotarchive.backends.base import BackendUnavailable, DownloadBackend, DownloadFailed, Song

INSTALL_HINT = (
    'Install it with `pip install "spotarchive[somedl]"` (or `pipx install somedl`). '
    "See docs/backends.md#somedl."
)
# A query that is exactly one of these runs a SomeDL subcommand instead of a search.
_SUBCOMMANDS = {"import", "new-template", "update-metadata", "download", "sync", "web"}


def find_executable(name: str = "somedl") -> str | None:
    """Look next to the running Python first (an unactivated virtualenv), then on PATH."""
    return shutil.which(name, path=str(Path(sys.executable).parent)) or shutil.which(name)


class SomeDLBackend(DownloadBackend):
    name = "somedl"
    shows_own_output = True
    audio_extensions = frozenset({".mp3", ".m4a", ".opus", ".ogg", ".flac"})

    def __init__(self, executable: str | None = None, audio_format: str | None = None,
                 extra_args: tuple[str, ...] = ()):
        # None means: find `somedl` / use SomeDL's own configured format.
        self.executable = executable
        self.audio_format = audio_format
        self.extra_args = tuple(extra_args)

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

    def command(self, song: Song) -> list[str]:
        query = song_query(song)
        if query in _SUBCOMMANDS:
            raise DownloadFailed(f"can't search for {query!r} without an artist name")
        # --here: flat "{artist} - {song}" names in the working directory,
        # whatever the user's SomeDL config says. --skip-file-check: the
        # caller already decided this song is missing.
        cmd = [self.executable or "somedl", "--here", "--skip-file-check"]
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


def song_query(song: Song) -> str:
    return f"{song.artists[0]} - {song.name}" if song.artists else song.name


def _audio_files(folder: Path, extensions: frozenset[str]) -> dict[Path, int]:
    return {
        p: p.stat().st_mtime_ns
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in extensions
    }
