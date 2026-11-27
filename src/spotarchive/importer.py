"""Importing a link (``--download LINK``): download what it points to, without a setlist.

Imports go to the ``Imports`` folder in the data folder, a playlist's into a
subfolder named after it. Each folder has the same ``.downloads.json`` record
as a playlist folder, keyed by the items' links, so importing a link again
only downloads what is missing. The downloading itself is the setlists'
(:func:`spotarchive.download.download_all`): an item stands in for a song.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from spotarchive.backends import DownloadBackend, DownloadEvents, ImportItem, ImportListing, Song
from spotarchive.backends.util import SPOTIFY_REFUSAL, is_spotify_url
from spotarchive.download import PlaylistDownload, load_record
from spotarchive.session import SessionStore
from spotarchive.setlist import IMPORTS_FOLDER, SETLIST_FILENAME, safe_folder_name


class ImportRefused(Exception):
    """The link can't be imported."""


def check_link(link: str) -> str:
    """*link* if it may be imported: a web link that isn't Spotify's."""
    link = link.strip()
    if is_spotify_url(link) or link.casefold().startswith("spotify:"):
        raise ImportRefused(
            f"{SPOTIFY_REFUSAL} To get the songs of your Spotify playlists, use "
            "`spotarchive --fetch` and `spotarchive --download`."
        )
    try:
        parts = urlsplit(link)
        valid = parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:
        valid = False
    if not valid:
        raise ImportRefused(f"Not a web link: {link!r} (it has to start with http:// or https://).")
    return link


def imports_root(store: SessionStore, data_dir: Path) -> Path:
    """The folder imports go to; refused while a Spotify playlist has it."""
    root = data_dir / IMPORTS_FOLDER
    owned = any((s.folder or "").casefold() == IMPORTS_FOLDER.casefold() for s in store.playlists.values())
    if owned or (root / SETLIST_FILENAME).is_file():
        raise ImportRefused(
            f"{root} holds a Spotify playlist, but imported links go there. Run `spotarchive --fetch`, "
            "which moves a playlist of yours out of it; if the folder stays, rename or move it yourself."
        )
    return root


def import_folder(root: Path, listing: ImportListing) -> Path:
    return root if listing.playlist is None else root / safe_folder_name(listing.playlist)


@dataclass(frozen=True)
class ImportSong(Song):
    """An item of a link, in the place of a setlist's song."""

    item: ImportItem = ImportItem("")

    @property
    def key(self) -> str:
        return self.item.key


class LinkImporter(DownloadBackend):
    """Makes a backend's :meth:`~DownloadBackend.import_item` look like downloading a song."""

    def __init__(self, backend: DownloadBackend):
        self.backend = backend
        self.name = backend.name
        self.shows_own_output = backend.shows_own_output
        self.audio_extensions = backend.audio_extensions

    def download(self, song: Song, folder: Path, events: DownloadEvents | None = None) -> list[Path]:
        assert isinstance(song, ImportSong)
        return self.backend.import_item(song.item, folder, events)


def prepare_import(listing: ImportListing, folder: Path) -> PlaylistDownload:
    """Create *folder* and work out which of the items still have to be downloaded.

    Only the record says what is there: an item's file is named after what
    the download reports, which the listing doesn't know yet.
    """
    folder.mkdir(parents=True, exist_ok=True)
    record = load_record(folder)
    pending = [
        ImportSong(item.title or item.url, item=item)
        for item in listing.items
        if not any((folder / f).is_file() for f in record.get(item.key, {}).get("files", []))
    ]
    total = len(listing.items)
    return PlaylistDownload(
        listing.playlist or IMPORTS_FOLDER, folder, total=total, already_present=total - len(pending),
        pending=pending, record=record,
    )
