"""yt-dlp (https://github.com/yt-dlp/yt-dlp) on its own: plain YouTube search.

Meant as the fallback for SomeDL, which only searches YouTube Music and gives
up on songs it finds no metadata for. yt-dlp is run once per song with a
``ytsearchN:Artist - Song`` query, from a temporary folder inside the
playlist folder (like SomeDL, so songs downloading at once don't mix up their
files). Of the first *search_results* videos, the first one whose length is
close to the song's length on Spotify is downloaded and converted to audio.

yt-dlp finds no music metadata, so the file is tagged with the setlist's
(title, artists, album, release date) right after downloading, and
:meth:`YtDlpBackend.tag` does the same for the metadata cleanup.

With a YouTube login, its cookies are written to a private temporary
``cookies.txt`` for each download and passed with ``--cookies``.
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
from spotarchive.backends.util import (
    cookie_file,
    enable_ansi,
    find_executable,
    forward,
    has_deno,
    is_terminal,
    move_into,
    safe_name,
)
from spotarchive.tags import write_setlist_tags

INSTALL_HINT = 'Install it with `pip install "yt-dlp[default,deno]"`. See docs/backends.md#yt-dlp.'
# What yt-dlp's --audio-format takes.
AUDIO_FORMATS = ("best", "aac", "alac", "flac", "m4a", "mp3", "opus", "vorbis", "wav")
# Per-song download folder inside the playlist folder (hidden: the dot keeps
# it out of the "already there" check).
STAGING_PREFIX = ".ytdlp-download-"
# yt-dlp's exit code when it stopped because --max-downloads was reached.
_MAX_DOWNLOADS_REACHED = 101
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
_MESSAGE = re.compile(r"^(WARNING|ERROR): (.+?)\s*$")
_PERCENT = re.compile(r"^\[download\]\s+(\d+(?:\.\d+)?)%")
_POSTPROCESSING = re.compile(r"^\[(?:ExtractAudio|Metadata|FixupM\w*)\]")


class YtDlpBackend(DownloadBackend):
    name = "ytdlp"
    shows_own_output = True
    audio_extensions = frozenset({".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav", ".aac", ".webm"})
    # Its "own" metadata is the setlist's: always available, never wrong
    # about which song it is.
    can_tag = True
    # Downloads are tagged right away, but only best effort (a warning when
    # it fails); the metadata cleanup checks them again (cheap: a file whose
    # tags are right isn't rewritten).
    tags_downloads = False

    def __init__(self, executable: str | None = None, audio_format: str = "mp3",
                 search_results: int = 5, length_tolerance: int = 30,
                 extra_args: tuple[str, ...] = ()):
        if audio_format not in AUDIO_FORMATS:
            raise ValueError(f"audio_format must be one of {', '.join(AUDIO_FORMATS)}, not {audio_format!r}")
        if isinstance(search_results, bool) or not isinstance(search_results, int) or search_results < 1:
            raise ValueError("search_results must be a whole number, 1 or more")
        if isinstance(length_tolerance, bool) or not isinstance(length_tolerance, int) or length_tolerance < 0:
            raise ValueError("length_tolerance must be a whole number of seconds, 0 (any length) or more")
        # None means: find `yt-dlp`.
        self.executable = executable
        self.audio_format = audio_format
        self.search_results = search_results
        # Seconds a video may be shorter or longer than the song on Spotify.
        self.length_tolerance = length_tolerance
        self.extra_args = tuple(extra_args)

    def check(self) -> None:
        if self.executable is None:
            self.executable = find_executable("yt-dlp")
        if self.executable is None:
            raise BackendUnavailable(f"yt-dlp isn't installed. {INSTALL_HINT}")
        if shutil.which("ffmpeg") is None:
            raise BackendUnavailable(
                "yt-dlp needs FFmpeg to extract the audio, which isn't installed or not on PATH. "
                "See docs/backends.md#yt-dlp."
            )

    def warnings(self) -> list[str]:
        if has_deno(self.executable):
            return []
        return [
            "Deno isn't installed. yt-dlp needs it for YouTube; without it downloads may fail with HTTP 403. "
            'Install it with `pip install "yt-dlp[default,deno]"`. See docs/backends.md#troubleshooting.'
        ]

    def match_filter(self, song: Song) -> str:
        """yt-dlp's --match-filters: no live streams, and a length close to the song's."""
        rules = ["!is_live"]
        if self.length_tolerance and song.duration_ms:
            seconds = song.duration_ms / 1000
            low = max(0, int(seconds - self.length_tolerance))
            high = int(seconds + self.length_tolerance + 1)
            # "?": a video whose length isn't known passes.
            rules += [f"duration>=?{low}", f"duration<=?{high}"]
        return " & ".join(rules)

    def command(self, song: Song, staging: Path, cookies: Path | None = None, color: bool = False,
                newline: bool = True) -> list[str]:
        query = f"{song.artists[0]} - {song.name}" if song.artists else song.name
        # The file is named after the setlist, like SomeDL's flat names
        # ("%" would start a template field).
        template = safe_name(query).replace("%", "%%") + ".%(ext)s"
        cmd = [
            self.executable or "yt-dlp",
            "--ignore-config",
            "--extract-audio", "--audio-format", self.audio_format, "--audio-quality", "0",
            "--match-filters", self.match_filter(song),
            "--max-downloads", "1",
            "--paths", str(staging), "--output", template,
            "--no-mtime",
            "--color", "always" if color else "no_color",
        ]
        if newline:
            cmd.append("--newline")
        if cookies is not None:
            cmd += ["--cookies", str(cookies)]
        # The search query is a single argument; "--" so it isn't read as an option.
        return [*cmd, *self.extra_args, "--", f"ytsearch{self.search_results}:{query}"]

    def download(self, song: Song, folder: Path, events: DownloadEvents | None = None) -> list[Path]:
        events = events or DownloadEvents()
        staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=folder))
        try:
            with cookie_file(self.youtube_login) as cookies:
                try:
                    returncode, output = self._run(song, staging, cookies, events)
                except OSError as exc:
                    raise DownloadFailed(f"couldn't run yt-dlp: {exc}") from exc
            new = [p for p in staging.rglob("*") if p.is_file() and p.suffix.lower() in self.audio_extensions]
            # Even with a file: it may be what was left when converting failed.
            if returncode not in (0, _MAX_DOWNLOADS_REACHED):
                raise DownloadFailed(f"yt-dlp exited with code {returncode}{output.reason(' ({})')}")
            if not new:
                raise DownloadFailed(output.reason() or self._not_found(song))
            # Only the audio: the rest (thumbnails, leftovers) goes with the staging folder.
            files = move_into(staging, folder, new, everything=False)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        for file in files:
            try:
                write_setlist_tags(file, song)
            except TaggingFailed as exc:
                events.warning(str(exc))
        return files

    def tag(self, song: Song, file: Path) -> None:
        write_setlist_tags(file, song)

    def _not_found(self, song: Song) -> str:
        if self.length_tolerance and song.duration_ms:
            return (f"no YouTube video within {self.length_tolerance} s of the song's length "
                    f"among the first {self.search_results} results")
        return "no YouTube video found"

    def _run(self, song: Song, staging: Path, cookies: Path | None, events: DownloadEvents):
        parser = OutputParser(events)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        show = events.show_output
        terminal = show and is_terminal(sys.stdout)
        if terminal and not enable_ansi():
            # A console that can't show escape sequences (old Windows): let
            # yt-dlp draw on it directly; then only the result is known.
            cmd = self.command(song, staging, cookies, newline=False)
            return subprocess.run(cmd, check=False).returncode, parser
        # On a terminal yt-dlp redraws its progress line in place; captured,
        # every update comes as a line of its own.
        cmd = self.command(song, staging, cookies, color=terminal, newline=not terminal)
        with subprocess.Popen(
            cmd, env=env,
            stdin=None if show else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        ) as proc:
            while True:
                chunk = proc.stdout.read1(65536)
                if not chunk:
                    break
                if show:
                    forward(chunk)
                parser.feed(chunk)
            returncode = proc.wait()
        parser.close()
        return returncode, parser


class OutputParser:
    """Reads yt-dlp's output: ``WARNING:``/``ERROR:`` lines and ``[download]  42.0%`` progress."""

    def __init__(self, events: DownloadEvents):
        self.events = events
        self.warnings: list[str] = []
        self.errors: list[str] = []
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

    def reason(self, template: str = "{}") -> str:
        """Why the song failed (the last error) in *template*; '' if unknown."""
        return template.format(self.errors[-1]) if self.errors else ""

    def _line(self, raw: str) -> None:
        line = _ANSI.sub("", raw).strip()
        match = _MESSAGE.match(line)
        if match:
            level, message = match.groups()
            if level == "WARNING":
                self.warnings.append(message)
                self.events.warning(message)
            else:
                self.errors.append(message)
                self.events.error(message)
            return
        percent = _PERCENT.match(line)
        if percent:
            self.events.progress(min(float(percent.group(1)) / 100, 1.0))
        elif _POSTPROCESSING.match(line):
            self.events.progress(1.0)
