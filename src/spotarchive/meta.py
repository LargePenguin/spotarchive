"""Metadata cleanup: give the downloaded songs proper tags.

The tags come from one of two sources (``metadata.source`` in the config):

- ``backend``: the download helper's own metadata. Files the helper
  downloaded itself are already tagged when it says so (``tags_downloads``);
  the rest (added by hand, or by another helper) are handed to its ``tag``.
  With a fallback backend too, a song goes to the backend that downloaded
  it first, then to the others until one tags it (yt-dlp, which finds no
  metadata, tags with the setlist's).
- ``setlist``: Spotify's metadata from ``setlist.csv`` (title, artists,
  album, release date), written with mutagen. Files are only saved when a
  tag actually differs.

Songs the user replaced with ``--overwrite`` already have the setlist's tags
and are never handed to a helper.

Which file belongs to which song comes from the download record, as for
downloading. The record also remembers who tagged a song (``tagged``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from spotarchive.backends import DownloadBackend, Song, TaggingFailed
from spotarchive.download import (
    Backends,
    as_chain,
    audio_extensions,
    find_present,
    load_record,
    record_present,
    save_record,
    setlist_songs,
)
from spotarchive.setlist import SETLIST_FILENAME
from spotarchive.tags import TAGGABLE_EXTENSIONS, setlist_tags, write_setlist_tags  # noqa: F401 (re-exported)

SETLIST_SOURCE = "setlist"

# (song, position, number of songs)
StartCallback = Callable[[Song, int, int], None]
# (song, error or None)
DoneCallback = Callable[[Song, "str | None"], None]


@dataclass
class PlaylistMeta:
    name: str
    folder: Path
    # Songs whose files were retagged / already fine / couldn't be tagged.
    updated: int = 0
    unchanged: int = 0
    failed: list[tuple[Song, str]] = field(default_factory=list)


def tagged_by(entry: dict, backends: Backends) -> str | None:
    """Who last tagged a recorded song's files, if known."""
    if entry.get("tagged"):
        return entry["tagged"]
    for backend in as_chain(backends):
        if backend.tags_downloads and entry.get("backend") == backend.name:
            return backend.name
    return None


def tagging_order(entry: dict, backends: Sequence[DownloadBackend]) -> list[DownloadBackend]:
    """*backends* to try for a song: the one that downloaded it first."""
    first = [b for b in backends if b.name == entry.get("backend")]
    return first + [b for b in backends if b not in first]


def fix_playlist_metadata(
    name: str,
    folder: Path,
    backend: Backends | None,
    on_start: StartCallback = lambda song, i, n: None,
    on_done: DoneCallback = lambda song, error: None,
) -> PlaylistMeta:
    """Fix the tags of the songs of *folder*'s setlist that are there.

    With *backend* (one, or several to try in turn) the helper tags the
    files, otherwise the setlist does. A song that fails doesn't stop the
    others; the record is saved after every song that changed.
    """
    if not (folder / SETLIST_FILENAME).is_file():
        raise FileNotFoundError(f"{folder / SETLIST_FILENAME} doesn't exist; fetch the playlist first.")
    songs = setlist_songs(folder)
    record = load_record(folder)
    chain = as_chain(backend) if backend else []
    extensions = audio_extensions(chain) if chain else TAGGABLE_EXTENSIONS
    present = find_present(songs, folder, record, extensions)
    record_present(folder, record, present)
    names = {b.name for b in chain}

    result = PlaylistMeta(name, folder)
    todo = [s for s in songs if s.key in present]
    for i, song in enumerate(todo, start=1):
        on_start(song, i, len(todo))
        entry = record[song.key]  # record_present added every present song
        try:
            if chain:
                # A helper looks songs up by name, so it could put back the
                # metadata of the match the user replaced (--overwrite).
                changed = not entry.get("override") and tagged_by(entry, chain) not in names
                if changed:
                    tagger = _tag(song, folder, present[song.key], tagging_order(entry, chain))
            else:
                tagger = SETLIST_SOURCE
                # Not short: every file must be checked.
                changed = any([write_setlist_tags(folder / f, song) for f in present[song.key]])
        except TaggingFailed as exc:
            result.failed.append((song, str(exc)))
            on_done(song, str(exc))
            continue
        if changed:
            entry["tagged"] = tagger
            save_record(folder, record)
            result.updated += 1
        else:
            result.unchanged += 1
        on_done(song, None)
    return result


def _tag(song: Song, folder: Path, files: list[str], backends: list[DownloadBackend]) -> str:
    """Tag *files* with the first of *backends* that manages; returns its name."""
    failures = []
    for backend in backends:
        try:
            for file in files:
                backend.tag(song, folder / file)
        except TaggingFailed as exc:
            failures.append((backend.name, str(exc)))
            continue
        return backend.name
    if len(failures) == 1:
        raise TaggingFailed(failures[0][1])
    raise TaggingFailed("; ".join(f"{name}: {reason}" for name, reason in failures))
