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

Imported links (:meth:`YtDlpBackend.list_url` and
:meth:`~YtDlpBackend.import_item`) involve no search and no setlist: exactly
what the link points to is downloaded, and named and tagged after what yt-dlp
reports about it (see :mod:`spotarchive.backends.ytinfo`). When it names the
track and artists, the thumbnail, cut to a square, becomes the cover art.

With a YouTube login, its cookies are written to a private temporary
``cookies.txt`` for each download and passed with ``--cookies``.
"""

from __future__ import annotations

import codecs
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable

from spotarchive.backends.base import (
    BackendUnavailable,
    DownloadBackend,
    DownloadEvents,
    DownloadFailed,
    ImportItem,
    ImportListing,
    Song,
    TaggingFailed,
)
from spotarchive.backends.util import (
    SPOTIFY_REFUSAL,
    cookie_file,
    enable_ansi,
    find_executable,
    fit_name,
    forward,
    has_deno,
    is_spotify_url,
    is_terminal,
    move_into,
    safe_name,
)
from spotarchive.backends.ytinfo import TrackInfo, track_info
from spotarchive.tags import embed_cover, write_setlist_tags, write_tags

INSTALL_HINT = 'Install it with `pip install "yt-dlp[default,deno]"`. See docs/backends.md#yt-dlp.'
# What yt-dlp's --audio-format takes.
AUDIO_FORMATS = ("best", "aac", "alac", "flac", "m4a", "mp3", "opus", "vorbis", "wav")
# Per-song download folder inside the playlist folder (hidden: the dot keeps
# it out of the "already there" check).
STAGING_PREFIX = ".ytdlp-download-"
# An import downloads under this name, then is renamed after its metadata.
_IMPORT_STEM = "download"
_THUMBNAIL_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
# Imports running at once must not pick the same file name.
_NAMING = threading.Lock()
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
    can_import = True

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
        options = [
            "--match-filters", self.match_filter(song),
            "--max-downloads", "1",
            "--paths", str(staging), "--output", template,
        ]
        # The search query is a single argument.
        return self._command(options, f"ytsearch{self.search_results}:{query}", cookies, color, newline)

    def _command(self, options: list[str], target: str, cookies: Path | None, color: bool,
                 newline: bool) -> list[str]:
        """A yt-dlp command downloading the audio of *target*."""
        cmd = [
            self.executable or "yt-dlp",
            "--ignore-config",
            "--extract-audio", "--audio-format", self.audio_format, "--audio-quality", "0",
            *options,
            "--no-mtime",
            "--color", "always" if color else "no_color",
        ]
        if newline:
            cmd.append("--newline")
        if cookies is not None:
            cmd += ["--cookies", str(cookies)]
        # "--" so the target isn't read as an option.
        return [*cmd, *self.extra_args, "--", target]

    def download(self, song: Song, folder: Path, events: DownloadEvents | None = None) -> list[Path]:
        events = events or DownloadEvents()
        staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=folder))
        try:
            def command(cookies, color, newline):
                return self.command(song, staging, cookies, color, newline)

            new = self._fetch(command, staging, events, lambda: self._not_found(song))
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

    def _fetch(self, command: Callable, staging: Path, events: DownloadEvents,
               not_found: Callable[[], str]) -> list[Path]:
        """Run the download *command* builds; return the audio files it left in *staging*."""
        with cookie_file(self.youtube_login) as cookies:
            try:
                returncode, output = self._run(command, cookies, events)
            except OSError as exc:
                raise DownloadFailed(f"couldn't run yt-dlp: {exc}") from exc
        new = [p for p in staging.rglob("*") if p.is_file() and p.suffix.lower() in self.audio_extensions]
        # Even with a file: it may be what was left when converting failed.
        if returncode not in (0, _MAX_DOWNLOADS_REACHED):
            raise DownloadFailed(f"yt-dlp exited with code {returncode}{output.reason(' ({})')}")
        if not new:
            raise DownloadFailed(output.reason() or not_found())
        return new

    def list_command(self, url: str, cookies: Path | None = None) -> list[str]:
        cmd = [
            self.executable or "yt-dlp", "--ignore-config",
            # One JSON document; a playlist's items are only listed, not looked up.
            "--dump-single-json", "--flat-playlist",
            # A video link that also names a playlist means the video.
            "--no-playlist",
            "--color", "no_color",
        ]
        if cookies is not None:
            cmd += ["--cookies", str(cookies)]
        return [*cmd, *self.extra_args, "--", url]

    def list_url(self, url: str) -> ImportListing:
        with cookie_file(self.youtube_login) as cookies:
            try:
                result = subprocess.run(
                    self.list_command(url, cookies),
                    stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"}, check=False,
                )
            except OSError as exc:
                raise DownloadFailed(f"couldn't run yt-dlp: {exc}") from exc
        parser = OutputParser(DownloadEvents())
        parser.feed((result.stderr or "").encode("utf-8"))
        parser.close()
        try:
            info = json.loads(result.stdout)
        except ValueError:
            info = None
        if result.returncode != 0 or not isinstance(info, dict):
            raise DownloadFailed(parser.reason() or f"yt-dlp couldn't read the link (code {result.returncode})")
        return parse_listing(info, url)

    def import_command(self, item: ImportItem, staging: Path, cookies: Path | None = None, color: bool = False,
                       newline: bool = True) -> list[str]:
        options = ["--no-playlist"]
        if item.index is not None:
            options += ["--playlist-items", str(item.index)]
        options += [
            "--match-filters", "!is_live & extractor!*=?spotify",
            "--max-downloads", "1",
            "--paths", str(staging), "--output", f"{_IMPORT_STEM}.%(ext)s",
            # What the file is named and tagged after, and its cover art.
            "--write-info-json", "--no-write-playlist-metafiles", "--write-thumbnail",
        ]
        return self._command(options, item.url, cookies, color, newline)

    def import_item(self, item: ImportItem, folder: Path, events: DownloadEvents | None = None) -> list[Path]:
        events = events or DownloadEvents()
        staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=folder))
        try:
            def command(cookies, color, newline):
                return self.import_command(item, staging, cookies, color, newline)

            new = self._fetch(command, staging, events,
                              lambda: "nothing was downloaded (live streams are skipped)")
            audio = sorted(new)[0]
            info = _read_info(staging)
            if info is None:
                events.warning("yt-dlp reported nothing about the file, so only the link's title is known.")
            track = track_info(info if info is not None else {"title": item.title})
            # Tagged before the move, so the folder never holds a half-written file.
            try:
                write_tags(audio, track.tags())
                if track.official:
                    embed_cover(audio, self._cover(staging))
            except TaggingFailed as exc:
                events.warning(str(exc))
            return [self._move_import(audio, folder, track, str((info or {}).get("id") or ""))]
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _cover(self, staging: Path) -> bytes:
        """The downloaded thumbnail, cut to a centered square, as JPEG."""
        thumbnails = [staging / f"{_IMPORT_STEM}{ext}" for ext in _THUMBNAIL_EXTENSIONS]
        thumbnail = next((p for p in thumbnails if p.is_file()), None)
        if thumbnail is None:
            raise TaggingFailed("no cover art: there is no thumbnail")
        cover = staging / "cover.jpg"
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(thumbnail),
               "-vf", "crop='min(iw,ih)':'min(iw,ih)'", "-frames:v", "1", "-q:v", "2", str(cover)]
        try:
            if subprocess.run(cmd, capture_output=True, check=False).returncode == 0:
                return cover.read_bytes()
        except OSError as exc:
            raise TaggingFailed(f"no cover art: {exc}") from exc
        raise TaggingFailed("no cover art: FFmpeg couldn't convert the thumbnail")

    def _move_import(self, audio: Path, folder: Path, track: TrackInfo, item_id: str) -> Path:
        """Move *audio* into *folder* as "Artist - Title", without replacing another file."""
        # No leading dot: hidden files and folders are Spotarchive's own.
        stem = safe_name(track.label).lstrip(". ") or "song"
        # The extension is appended so that a dot in the title isn't read as one.
        name = stem + audio.suffix.lower()
        marker = f" [{safe_name(item_id)}]" if item_id else ""
        with _NAMING:
            target = folder / fit_name(folder, name)
            number = 1
            while target.exists():
                # First the item's ID, then numbers too.
                keep = marker if marker and number == 1 else f"{marker} ({number})"
                target = folder / fit_name(folder, name, keep=keep)
                number += 1
            try:
                os.replace(audio, target)
            except OSError as exc:
                raise DownloadFailed(f"couldn't move {target.name} into the folder: {exc}") from exc
        return target

    def tag(self, song: Song, file: Path) -> None:
        write_setlist_tags(file, song)

    def _not_found(self, song: Song) -> str:
        if self.length_tolerance and song.duration_ms:
            return (f"no YouTube video within {self.length_tolerance} s of the song's length "
                    f"among the first {self.search_results} results")
        return "no YouTube video found"

    def _run(self, command: Callable, cookies: Path | None, events: DownloadEvents):
        parser = OutputParser(events)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        show = events.show_output
        terminal = show and is_terminal(sys.stdout)
        if terminal and not enable_ansi():
            # A console that can't show escape sequences (old Windows): let
            # yt-dlp draw on it directly; then only the result is known.
            cmd = command(cookies, False, False)
            return subprocess.run(cmd, check=False).returncode, parser
        # On a terminal yt-dlp redraws its progress line in place; captured,
        # every update comes as a line of its own.
        cmd = command(cookies, terminal, not terminal)
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


def _read_info(staging: Path) -> dict | None:
    try:
        info = json.loads((staging / f"{_IMPORT_STEM}.info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def _refuse_spotify(extractor: object, *urls: object) -> None:
    if str(extractor or "").casefold().startswith("spotify") or any(
        isinstance(url, str) and is_spotify_url(url) for url in urls
    ):
        raise DownloadFailed(SPOTIFY_REFUSAL)


def parse_listing(info: dict, url: str) -> ImportListing:
    """The items in yt-dlp's flat JSON about *url*; refuses anything from Spotify."""
    _refuse_spotify(info.get("extractor_key"), url, info.get("webpage_url"))
    page = info.get("webpage_url") or url
    if info.get("_type") not in ("playlist", "multi_video"):
        return ImportListing((ImportItem(page, info.get("title") or ""),))
    items: dict[str, ImportItem] = {}
    for index, entry in enumerate(info.get("entries") or [], start=1):
        if not isinstance(entry, dict):
            continue
        if entry.get("_type") in ("playlist", "multi_video") or entry.get("ie_key") == "YoutubeTab":
            raise DownloadFailed("this page lists other playlists; use the link of one of them")
        # The link of the item's page. In a fully described entry, "url" is the
        # media stream itself, which expires.
        link = entry.get("url") if entry.get("_type") in ("url", "url_transparent") else entry.get("webpage_url")
        _refuse_spotify(entry.get("ie_key") or entry.get("extractor_key"), link, entry.get("url"))
        title = entry.get("title") or entry.get("id") or f"Item {index}"
        if isinstance(link, str) and link.startswith(("http://", "https://")) and link != page:
            item = ImportItem(link, title)
        else:
            item = ImportItem(page, title, index)
        items.setdefault(item.key, item)
    if not items:
        raise DownloadFailed("there is nothing to download at this link")
    return ImportListing(tuple(items.values()), info.get("title") or info.get("id") or "Playlist")


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
