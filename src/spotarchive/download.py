"""Download the songs of the selected playlists' setlists through a backend.

Each playlist folder gets a ``.downloads.json`` record mapping every song
(by its Spotify link) to the files the backend produced for it. Helpers name
files after what they found, which doesn't always match Spotify's names, so
this record is what lets later runs skip songs that are already there.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from spotarchive.backends import DownloadBackend, DownloadFailed, Song
from spotarchive.session import SessionStore
from spotarchive.setlist import SETLIST_FILENAME, read_setlist

RECORD_FILENAME = ".downloads.json"
RECORD_VERSION = 1

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# (song, position, number of songs to download)
StartCallback = Callable[[Song, int, int], None]
# (song, new files or None, error or None)
DoneCallback = Callable[[Song, "list[Path] | None", "str | None"], None]


@dataclass
class PlaylistDownload:
    name: str
    folder: Path
    total: int = 0
    already_present: int = 0
    downloaded: int = 0
    failed: list[tuple[Song, str]] = field(default_factory=list)


def selected_folders(store: SessionStore, data_dir: Path) -> list[tuple[str, Path]]:
    """The selected playlists that have been fetched, as (name, folder)."""
    return [
        (state.name, data_dir / state.folder)
        for state in store.playlists.values()
        if state.selected and state.folder
    ]


def load_record(folder: Path) -> dict[str, dict]:
    path = folder / RECORD_FILENAME
    if not path.exists():
        return {}
    try:
        songs = json.loads(path.read_text(encoding="utf-8")).get("songs", {})
    except (OSError, ValueError, AttributeError):
        # Only a cache: losing it means falling back to the file name check.
        return {}
    return songs if isinstance(songs, dict) else {}


def save_record(folder: Path, songs: dict[str, dict]) -> None:
    text = json.dumps({"version": RECORD_VERSION, "songs": songs}, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f"{RECORD_FILENAME}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, folder / RECORD_FILENAME)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def setlist_songs(folder: Path) -> list[Song]:
    """The songs in the folder's setlist, in order, without duplicates."""
    songs: dict[str, Song] = {}
    for row in read_setlist(folder):
        song = Song.from_row(row)
        if song.name:
            songs.setdefault(song.key, song)
    return list(songs.values())


def _normalize(stem: str) -> str:
    return " ".join(_INVALID_CHARS.sub("_", stem).split()).casefold()


def expected_stems(song: Song) -> set[str]:
    """File names (without extension) a helper would likely give *song*."""
    if not song.artists:
        return {_normalize(song.name)}
    artist_forms = {song.artists[0], ", ".join(song.artists), " & ".join(song.artists), "; ".join(song.artists)}
    return {_normalize(f"{artist} - {song.name}") for artist in artist_forms}


def find_present(songs: list[Song], folder: Path, record: dict[str, dict],
                 extensions: frozenset[str]) -> dict[str, list[str]]:
    """Map the key of every song already in *folder* to its files.

    A song counts as present when a file recorded for it still exists, or
    when an audio file is named like ``Artist - Song`` (files from before
    the record existed, or added by hand).
    """
    by_stem: dict[str, str] = {}
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in extensions:
            by_stem.setdefault(_normalize(p.stem), p.name)

    present: dict[str, list[str]] = {}
    for song in songs:
        recorded = [f for f in record.get(song.key, {}).get("files", []) if (folder / f).is_file()]
        if recorded:
            present[song.key] = recorded
            continue
        matches = sorted({by_stem[s] for s in expected_stems(song) if s in by_stem})
        if matches:
            present[song.key] = matches
    return present


def download_playlist(
    backend: DownloadBackend,
    name: str,
    folder: Path,
    on_start: StartCallback = lambda song, i, n: None,
    on_done: DoneCallback = lambda song, files, error: None,
) -> PlaylistDownload:
    """Download the songs of *folder*'s setlist that aren't there yet.

    A song that fails is reported and doesn't stop the others. The record is
    saved after every song, so an interrupted run loses nothing.
    """
    if not (folder / SETLIST_FILENAME).is_file():
        raise FileNotFoundError(f"{folder / SETLIST_FILENAME} doesn't exist; fetch the playlist first.")
    songs = setlist_songs(folder)
    record = load_record(folder)
    present = find_present(songs, folder, record, backend.audio_extensions)
    result = PlaylistDownload(name, folder, total=len(songs), already_present=len(present))

    changed = False
    for key, files in present.items():
        if record.get(key, {}).get("files") != files:
            record[key] = {**record.get(key, {}), "files": files}
            changed = True
    if changed:
        save_record(folder, record)

    pending = [s for s in songs if s.key not in present]
    for i, song in enumerate(pending, start=1):
        on_start(song, i, len(pending))
        try:
            files = backend.download(song, folder)
        except DownloadFailed as exc:
            result.failed.append((song, str(exc)))
            on_done(song, None, str(exc))
            continue
        record[song.key] = {
            "label": song.label,
            "backend": backend.name,
            "files": [p.relative_to(folder).as_posix() for p in files],
        }
        save_record(folder, record)
        result.downloaded += 1
        on_done(song, files, None)
    return result
