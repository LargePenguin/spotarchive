"""The interface every download backend implements.

The rest of Spotarchive only talks to :class:`DownloadBackend`, so a
contributor can add another download helper without touching the download
loop, the setlists or the CLI. See ``docs/backends.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from spotarchive.setlist import ARTIST_SEPARATOR


class BackendUnavailable(Exception):
    """The backend can't run at all (e.g. its program isn't installed)."""


class DownloadFailed(Exception):
    """One song couldn't be downloaded; the others can still be tried."""


@dataclass(frozen=True)
class Song:
    """One ``setlist.csv`` row, as handed to a backend."""

    name: str
    artists: tuple[str, ...] = ()
    album: str = ""
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
    # then shows a line per song instead of a progress bar.
    shows_own_output: bool = False
    # File types the helper produces (lowercase, with the dot); only these
    # count as downloaded songs.
    audio_extensions: frozenset[str] = frozenset()

    def check(self) -> None:
        """Raise :class:`BackendUnavailable` if the backend can't run."""

    @abstractmethod
    def download(self, song: Song, folder: Path) -> list[Path]:
        """Download *song* into *folder* and return the new audio files.

        Raise :class:`DownloadFailed` when nothing could be downloaded.
        """
