"""Metadata cleanup: give the downloaded songs proper tags.

The tags come from one of two sources (``metadata.source`` in the config):

- ``backend``: the download helper's own metadata. Files the helper
  downloaded itself are already tagged when it says so (``tags_downloads``);
  the rest (added by hand, or by another helper) are handed to its ``tag``.
- ``setlist``: Spotify's metadata from ``setlist.csv`` (title, artists,
  album, release date), written with mutagen. Files are only saved when a
  tag actually differs.

Which file belongs to which song comes from the download record, as for
downloading. The record also remembers who tagged a song (``tagged``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from spotarchive.backends import DownloadBackend, Song, TaggingFailed
from spotarchive.download import find_present, load_record, record_present, save_record, setlist_songs
from spotarchive.setlist import SETLIST_FILENAME

# Formats the setlist source can tag.
TAGGABLE_EXTENSIONS = frozenset({".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".opus"})
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


def setlist_tags(song: Song) -> dict[str, list[str]]:
    tags = {"title": [song.name], "artist": list(song.artists), "album": [song.album], "date": [song.release_date]}
    return {key: values for key, values in tags.items() if any(values)}


def write_setlist_tags(path: Path, song: Song) -> bool:
    """Tag *path* with *song*'s setlist metadata; False when it already had it."""
    import mutagen

    try:
        audio = mutagen.File(path, easy=True)
        if audio is None:
            raise TaggingFailed(f"{path.suffix or 'this file type'} files can't be tagged")
        if audio.tags is None:
            audio.add_tags()
        changes = {k: v for k, v in setlist_tags(song).items() if list(audio.tags.get(k, [])) != v}
        if not changes:
            return False
        for key, values in changes.items():
            audio[key] = values
        audio.save()
    except (mutagen.MutagenError, OSError) as exc:
        raise TaggingFailed(f"couldn't tag {path.name}: {exc}") from exc
    return True


def tagged_by(entry: dict, backend: DownloadBackend) -> str | None:
    """Who last tagged a recorded song's files, if known."""
    if entry.get("tagged"):
        return entry["tagged"]
    if backend.tags_downloads and entry.get("backend") == backend.name:
        return backend.name
    return None


def fix_playlist_metadata(
    name: str,
    folder: Path,
    backend: DownloadBackend | None,
    on_start: StartCallback = lambda song, i, n: None,
    on_done: DoneCallback = lambda song, error: None,
) -> PlaylistMeta:
    """Fix the tags of the songs of *folder*'s setlist that are there.

    With *backend* the helper tags the files, otherwise the setlist does. A
    song that fails doesn't stop the others; the record is saved after every
    song that changed.
    """
    if not (folder / SETLIST_FILENAME).is_file():
        raise FileNotFoundError(f"{folder / SETLIST_FILENAME} doesn't exist; fetch the playlist first.")
    songs = setlist_songs(folder)
    record = load_record(folder)
    extensions = backend.audio_extensions if backend else TAGGABLE_EXTENSIONS
    present = find_present(songs, folder, record, extensions)
    record_present(folder, record, present)
    tagger = backend.name if backend else SETLIST_SOURCE

    result = PlaylistMeta(name, folder)
    todo = [s for s in songs if s.key in present]
    for i, song in enumerate(todo, start=1):
        on_start(song, i, len(todo))
        entry = record[song.key]  # record_present added every present song
        try:
            if backend:
                changed = tagged_by(entry, backend) != backend.name
                if changed:
                    for file in present[song.key]:
                        backend.tag(song, folder / file)
            else:
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
