"""Helpers shared by the backends that run a download program (SomeDL, yt-dlp)."""

from __future__ import annotations

import codecs
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

from spotarchive.backends.base import DownloadFailed
from spotarchive.session import YouTubeSession

# Windows: MAX_PATH is 260 including the terminating NUL; file names are limited to 255 UTF-16 units.
_WINDOWS_PATH_LIMIT = 259
_POSIX_PATH_LIMIT = 4095
_NAME_LIMIT = 255
_SPOTIFY_DOMAINS = ("spotify.com", "spotify.link", "spotify.app.link", "spoti.fi", "scdn.co", "spotifycdn.com")
SPOTIFY_REFUSAL = "Spotarchive never downloads audio from Spotify."
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def find_executable(name: str) -> str | None:
    """Look next to the running Python first (an unactivated virtualenv), then on PATH."""
    return shutil.which(name, path=str(Path(sys.executable).parent)) or shutil.which(name)


def has_deno(executable: str | None) -> bool:
    """Whether yt-dlp, run by *executable* (or from its folder), finds Deno.

    yt-dlp looks for Deno in its own Python's scripts folder, then on PATH;
    the program's folder stands in for the first one (resolved, since pipx
    links programs from their own virtualenv into ~/.local/bin).
    """
    folder = str(Path(executable).resolve().parent) if executable else None
    return bool((folder and shutil.which("deno", path=folder)) or shutil.which("deno"))


def safe_name(name: str) -> str:
    """*name* as a file name that works on Windows too."""
    return " ".join(_INVALID_CHARS.sub("_", name).split()) or "song"


def path_limit() -> int:
    """The longest whole path the file system takes."""
    return _WINDOWS_PATH_LIMIT if os.name == "nt" else _POSIX_PATH_LIMIT


def _size(text: str) -> int:
    """Length of *text* in the unit the file system limits it in (UTF-16 units on Windows, else UTF-8 bytes)."""
    return len(text.encode("utf-16-le")) // 2 if os.name == "nt" else len(text.encode("utf-8"))


def fit_name(folder: Path, name: str, keep: str = "") -> str:
    """*name* cut (keeping its extension) so that ``folder / name`` fits the path and file name limits.

    *keep* is added in front of the extension and is never cut either.
    """
    path = Path(name)
    suffix = keep + path.suffix
    stem = path.stem
    room = min(_NAME_LIMIT, path_limit() - _size(str(folder)) - 1)
    if _size(stem + suffix) <= room:
        return stem + suffix
    room -= _size(suffix)
    if room < 1:
        raise DownloadFailed(f"the playlist folder's path leaves no room for a file name: {folder}")
    while stem and _size(stem) > room:
        stem = stem[:-1]
    return f"{stem.rstrip(' .')}{suffix}"


def is_spotify_url(url: str) -> bool:
    """Whether *url* points to Spotify, which nothing is ever downloaded from."""
    try:
        host = (urlsplit(url.strip()).hostname or "").rstrip(".")
    except ValueError:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in _SPOTIFY_DOMAINS)


@contextmanager
def cookie_file(login: YouTubeSession | None) -> Iterator[Path | None]:
    """The YouTube login as a ``cookies.txt`` that lives for one program run.

    A fresh file each time: yt-dlp writes the cookies back when it's done,
    and the saved login stays the one the browser handed over. ``None``
    without a login.
    """
    if login is None:
        yield None
        return
    # mkstemp creates the file readable only by the owner.
    fd, name = tempfile.mkstemp(prefix="spotarchive-cookies-", suffix=".txt")
    path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(login.netscape_cookies())
        yield path
    finally:
        path.unlink(missing_ok=True)


def move_into(staging: Path, folder: Path, audio: list[Path], everything: bool = True) -> list[Path]:
    """Move what a program wrote into *staging* over to *folder*; return the audio files' new paths.

    With *everything* the other files (and subfolders) go along, otherwise
    only the *audio* files.
    """
    moved = []
    files = sorted(p for p in staging.rglob("*") if p.is_file()) if everything else sorted(audio)
    for p in files:
        target = folder / p.relative_to(staging)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # The caller already decided this song is missing: replace what's there.
            os.replace(p, target)
        except OSError as exc:
            raise DownloadFailed(f"couldn't move {target.name} into the playlist folder: {exc}") from exc
        if p in audio:
            moved.append(target)
    return sorted(moved)


def is_terminal(stream) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def forward(chunk: bytes) -> None:
    """Pass a program's output (UTF-8) on to our stdout."""
    out = getattr(sys.stdout, "buffer", None)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    if out is None:
        sys.stdout.write(chunk.decode("utf-8", errors="replace"))
        sys.stdout.flush()
        return
    if codecs.lookup(encoding).name != "utf-8":
        chunk = chunk.decode("utf-8", errors="replace").encode(encoding, errors="replace")
    sys.stdout.flush()
    out.write(chunk)
    out.flush()


def enable_ansi() -> bool:
    """Make sure the terminal interprets escape sequences (Windows consoles need switching on)."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_vt = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(mode.value & enable_vt or kernel32.SetConsoleMode(handle, mode.value | enable_vt))
    except (AttributeError, OSError):
        return False
