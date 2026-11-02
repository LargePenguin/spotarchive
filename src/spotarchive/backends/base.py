"""The interface every download backend implements.

The rest of Spotarchive only talks to :class:`DownloadBackend`, so a
contributor can add another download helper without touching the download
loop, the setlists or the CLI. See ``docs/backends.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from spotarchive.session import YouTubeSession
from spotarchive.setlist import ARTIST_SEPARATOR


class BackendUnavailable(Exception):
    """The backend can't run at all (e.g. its program isn't installed)."""


class DownloadFailed(Exception):
    """One song couldn't be downloaded; the others can still be tried."""


class TaggingFailed(Exception):
    """One file's metadata couldn't be fixed; the others can still be tried."""


class DownloadEvents:
    """What a backend can report while it downloads one song.

    Spotarchive hands one to every :meth:`DownloadBackend.download` call.
    Several songs may download at once, each in its own thread with its own
    events object; the methods may be called from that thread at any time.
    The defaults do nothing, so a backend may ignore all of it.
    """

    # False when several songs download at once: the backend must then not
    # write to the terminal (capture its helper's output instead), since
    # Spotarchive draws its own display.
    show_output: bool = True

    def __init__(self, show_output: bool = True):
        self.show_output = show_output

    def progress(self, fraction: float) -> None:
        """The download is *fraction* (0.0 to 1.0) done, when the helper tells."""

    def warning(self, message: str) -> None:
        """Something went wrong but the download carries on."""

    def error(self, message: str) -> None:
        """An error the helper reported. Still raise :class:`DownloadFailed` if the song failed."""


@dataclass(frozen=True)
class Song:
    """One ``setlist.csv`` row, as handed to a backend."""

    name: str
    artists: tuple[str, ...] = ()
    album: str = ""
    release_date: str = ""
    duration_ms: int | None = None
    isrc: str = ""
    spotify_url: str = ""

    @classmethod
    def from_row(cls, row: dict[str, str]) -> Song:
        artists = tuple(a.strip() for a in (row.get("artist") or "").split(ARTIST_SEPARATOR.strip()) if a.strip())
        duration = (row.get("duration_ms") or "").strip()
        return cls(
            name=(row.get("name") or "").strip(),
            artists=artists,
            album=(row.get("album") or "").strip(),
            release_date=(row.get("release_date") or "").strip(),
            duration_ms=int(duration) if duration.isdigit() else None,
            isrc=(row.get("isrc") or "").strip(),
            spotify_url=(row.get("spotify_url") or "").strip(),
        )

    @property
    def key(self) -> str:
        """Identifies the song across fetches (the Spotify link when there is one)."""
        if self.spotify_url:
            return self.spotify_url
        return " | ".join([self.name, ARTIST_SEPARATOR.join(self.artists), self.album])

    @property
    def label(self) -> str:
        return f"{self.artists[0]} - {self.name}" if self.artists else self.name


class DownloadBackend(ABC):
    """A download helper that turns a :class:`Song` into audio files in a folder."""

    # Name used in messages and in the download record.
    name: str = ""
    # True when the helper prints its own progress to the terminal; the CLI
    # then shows a line per song instead of a progress bar when downloading
    # one song at a time. With several at once (``events.show_output`` is
    # false) the backend must keep quiet.
    shows_own_output: bool = False
    # File types the helper produces (lowercase, with the dot); only these
    # count as downloaded songs.
    audio_extensions: frozenset[str] = frozenset()
    # True when the helper can (re)write a file's metadata itself, see :meth:`tag`.
    can_tag: bool = False
    # True when files from :meth:`download` already carry the helper's own
    # metadata, so they don't need :meth:`tag` afterwards.
    tags_downloads: bool = False
    # The YouTube login handed over by :meth:`use_youtube_login`, if any.
    youtube_login: YouTubeSession | None = None

    def use_youtube_login(self, login: YouTubeSession) -> None:
        """Called before downloading when the user is logged in to YouTube.

        *login* holds the youtube.com cookies (Playwright's format) and the
        user agent of the browser they came from. A backend passes them on
        in whatever form its helper takes (yt-dlp: a ``cookies.txt`` file,
        see :meth:`YouTubeSession.netscape_cookies`); keep them private and
        out of logs. The default just keeps them in :attr:`youtube_login`.
        """
        self.youtube_login = login

    def check(self) -> None:
        """Raise :class:`BackendUnavailable` if the backend can't run."""

    def warnings(self) -> list[str]:
        """Problems worth mentioning that don't stop downloads (called after :meth:`check`)."""
        return []

    @abstractmethod
    def download(self, song: Song, folder: Path, events: DownloadEvents | None = None) -> list[Path]:
        """Download *song* into *folder* and return the new audio files.

        Raise :class:`DownloadFailed` when nothing could be downloaded. Other
        songs may be downloading into the same folder at the same time, so
        tell *this* song's files apart without looking at what else changed
        in the folder (e.g. download into a private temporary folder first).
        *events* (``None``: report nothing, show output) is where progress,
        warnings and errors go.
        """

    def tag(self, song: Song, file: Path) -> None:
        """Rewrite *file*'s metadata with what the helper finds for *song*.

        Only called when :attr:`can_tag` is true. Raise :class:`TaggingFailed`
        when the file couldn't be tagged.
        """
        raise NotImplementedError
