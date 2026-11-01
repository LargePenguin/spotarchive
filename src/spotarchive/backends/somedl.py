"""SomeDL (https://github.com/ChemistryGull/SomeDL), the default backend.

SomeDL is run as a separate program, once per song, from a temporary folder
inside the playlist folder (so songs downloading at once don't mix up their
files). Its output is parsed for progress, warnings and errors, and passed on
to the terminal when downloading one song at a time. It searches YouTube
Music for "Artist - Song" and tags the file with metadata it collects itself.

Fixing a file's metadata goes through ``somedl import``, which only has an
interactive wizard: its answers are piped in (written against SomeDL 1.7).
"""

from __future__ import annotations

import codecs
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
    DownloadEvents,
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
# Per-song download folder inside the playlist folder (hidden: the dot keeps
# it out of the "already there" check).
STAGING_PREFIX = ".somedl-download-"
# Terminal escape sequences (colors, cursor moves of SomeDL's live table).
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
# "1/1 Artist - Song: WARNING - message" (the label is optional).
_MESSAGE = re.compile(r"^\s*(?:.*?:\s+)?(WARNING|ERROR|CRITICAL) - (.+?)\s*$")
# yt-dlp's progress in SomeDL's live table: "yt-dlp: Downloading  42.0% of 3.2MiB at ...".
_PERCENT = re.compile(r"yt-dlp: Downloading\s+(\d+(?:\.\d+)?)%")
_POSTPROCESSING = "yt-dlp: Postprocessing"
# SomeDL's last line about a song.
_FINISHED = re.compile(r"^\s*Downloaded (?:successfully|failed):")
# Wide enough that SomeDL never wraps a message line when its output is captured.
_CAPTURE_COLUMNS = 1000


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

    def download(self, song: Song, folder: Path, events: DownloadEvents | None = None) -> list[Path]:
        events = events or DownloadEvents()
        cmd = self.command(song)
        # Each song downloads into its own hidden folder, so the new files are
        # simply what's in it, even with other songs downloading next to it.
        staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=folder))
        try:
            try:
                returncode, output = run_somedl(cmd, staging, events)
            except OSError as exc:
                raise DownloadFailed(f"couldn't run SomeDL: {exc}") from exc
            new = [p for p in staging.rglob("*") if p.is_file() and p.suffix.lower() in self.audio_extensions]
            if returncode != 0:
                raise DownloadFailed(f"SomeDL exited with code {returncode}{output.reason(' ({})')}")
            # SomeDL exits with 0 even when a song fails, so look for a file.
            if not new:
                raise DownloadFailed(
                    output.reason() or "SomeDL didn't produce a file (not found, or the download failed)"
                )
            return _move_into(staging, folder, new)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

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


def _move_into(staging: Path, folder: Path, audio: list[Path]) -> list[Path]:
    """Move everything SomeDL wrote (subfolders included) into *folder*; return the audio files' new paths."""
    moved = []
    for p in sorted(staging.rglob("*")):
        if not p.is_file():
            continue
        target = folder / p.relative_to(staging)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Like SomeDL itself with --skip-file-check: replace what's there.
            os.replace(p, target)
        except OSError as exc:
            raise DownloadFailed(f"couldn't move {target.name} into the playlist folder: {exc}") from exc
        if p in audio:
            moved.append(target)
    return sorted(moved)


class OutputParser:
    """Reads SomeDL's output as it arrives and reports progress, warnings and errors.

    SomeDL prints messages as ``label: LEVEL - message`` lines, wrapped at the
    terminal width (wrapped lines start at the left edge, unlike its table's
    lines). The download percentage only appears in its live table, which it
    draws only when it believes it writes to a terminal (see
    :func:`run_somedl`). Consecutive error lines (a yt-dlp failure comes as
    several) are reported as one error. A message is reported once the next
    line shows it's complete.
    """

    def __init__(self, events: DownloadEvents):
        self.events = events
        self.warnings: list[str] = []
        self.errors: list[str] = []
        # The message being read: [level, [lines]].
        self._message: list | None = None
        # Whether the line before was (part of) that message.
        self._wrapping = False
        self._buffer = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def feed(self, data: bytes) -> None:
        self._buffer += self._decoder.decode(data)
        *lines, self._buffer = re.split(r"\r\n|\r|\n", self._buffer)
        for line in lines:
            self._line(line)

    def close(self) -> None:
        self._buffer += self._decoder.decode(b"", final=True)
        if self._buffer:
            self._line(self._buffer)
            self._buffer = ""
        self._flush()

    def reason(self, template: str = "{}") -> str:
        """Why the song failed (the last error, else the last warning) in *template*; '' if unknown."""
        last = (self.errors or self.warnings or [""])[-1]
        return template.format(last) if last else ""

    def _line(self, raw: str) -> None:
        text = _ANSI.sub("", raw).rstrip()
        line = text.strip()
        match = _MESSAGE.match(line)
        if match:
            level = "ERROR" if match.group(1) == "CRITICAL" else match.group(1)
            if self._message and self._message[0] == level == "ERROR":
                self._message[1].append(match.group(2))
            else:
                self._flush()
                self._message = [level, [match.group(2)]]
            self._wrapping = True
            return
        if self._message and self._wrapping and line and text == line and not _FINISHED.match(line):
            # The message went on in a wrapped line.
            self._message[1][-1] += " " + line
            return
        self._wrapping = False
        # SomeDL redraws its table after every message, so only a warning or
        # the song's last line ends a run of errors.
        if self._message and (self._message[0] == "WARNING" or _FINISHED.match(line)):
            self._flush()
        percent = _PERCENT.search(line)
        if percent:
            self.events.progress(min(float(percent.group(1)) / 100, 1.0))
        elif _POSTPROCESSING in line:
            self.events.progress(1.0)

    def _flush(self) -> None:
        if not self._message:
            return
        level, lines = self._message
        self._message = None
        message = " ".join(lines)
        if level == "WARNING":
            self.warnings.append(message)
            self.events.warning(message)
        else:
            self.errors.append(message)
            self.events.error(message)


def run_somedl(cmd: list[str], cwd: Path, events: DownloadEvents) -> tuple[int, OutputParser]:
    """Run SomeDL, parsing its output; returns its exit code and the parser.

    With ``events.show_output`` its output is passed on to the terminal as
    it arrives (SomeDL's own display, as if it ran there itself); otherwise
    it is only parsed. SomeDL is told it writes to a terminal when the
    terminal is real or the output is captured, since only then does it draw
    the live table the progress comes from.
    """
    parser = OutputParser(events)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    show = events.show_output
    if show and _is_terminal(sys.stdout):
        if not _enable_ansi():
            # A console that can't show escape sequences (old Windows): let
            # SomeDL draw on it directly; then only the result is known.
            return subprocess.run(cmd, cwd=cwd, check=False).returncode, parser
        size = shutil.get_terminal_size()
        env.update(_forced_terminal(size.columns, size.lines))
    elif not show:
        env.update(_forced_terminal(_CAPTURE_COLUMNS, 50))
    with subprocess.Popen(
        cmd, cwd=cwd, env=env,
        stdin=None if show else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    ) as proc:
        while True:
            chunk = proc.stdout.read1(65536)
            if not chunk:
                break
            if show:
                _forward(chunk)
            parser.feed(chunk)
        returncode = proc.wait()
    parser.close()
    return returncode, parser


def _forced_terminal(columns: int, lines: int) -> dict[str, str]:
    # Rich (SomeDL's output library) trusts these over its own terminal check.
    return {"FORCE_COLOR": "1", "TTY_COMPATIBLE": "1", "TTY_INTERACTIVE": "1",
            "COLUMNS": str(columns), "LINES": str(lines)}


def _is_terminal(stream) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def _forward(chunk: bytes) -> None:
    """Pass SomeDL's output (UTF-8) on to our stdout."""
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


def _enable_ansi() -> bool:
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
