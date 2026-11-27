"""``--overwrite "Artist - Song" URL``: replace a wrong match with a link of the user's.

A helper sometimes downloads the wrong track for a song. :func:`find_copies`
looks for that song in every folder of the data folder (selected or not),
and :func:`replace_copy` puts the audio downloaded from the user's link in
its place.

The song is looked up in the setlists first, so a copy is found even when the
helper named it after the wrong track it found (the download record knows
which file is which). A file *named* like the query counts too, so the user
may also type the name of the wrong file. But when a setlist has the song,
a file that the record gives to a *different* song is left alone.

The new file is named after the setlist, gets the setlist's tags, and its
record entry notes the link (``override``), which keeps the metadata cleanup
from looking the song up by name again.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from spotarchive.backends import Song, TaggingFailed
from spotarchive.backends.util import safe_name
from spotarchive.download import (
    expected_stems,
    find_present,
    load_record,
    normalize,
    save_record,
    setlist_songs,
)
from spotarchive.setlist import SETLIST_FILENAME
from spotarchive.tags import write_setlist_tags

SEPARATOR = " - "
# Room for the extension and a " (2)" within the usual 255 file name limit.
_MAX_STEM_BYTES = 200


@dataclass
class Copy:
    """One song's file(s) in one folder, to be replaced by one new file."""

    folder: Path
    # What the new file is tagged with.
    song: Song
    # True when *song* is a setlist row, which then also names the new file.
    from_setlist: bool
    # Relative to *folder*, with "/".
    files: list[str]
    # The download record entries that stand for these files.
    keys: list[str] = field(default_factory=list)


@dataclass
class Search:
    copies: list[Copy] = field(default_factory=list)
    # Playlists whose setlist has the song without a file for it.
    missing: list[str] = field(default_factory=list)
    # Files that were deliberately left alone.
    notes: list[str] = field(default_factory=list)


@dataclass
class Replaced:
    # The new file, relative to the copy's folder.
    file: str
    warnings: list[str] = field(default_factory=list)


def check_arguments(query: str, url: str) -> str | None:
    """What's wrong with ``--overwrite``'s two values, if anything."""
    artist, _, title = query.strip().partition(SEPARATOR)
    if not artist.strip() or not title.strip():
        return f'--overwrite takes the song as "Artist - Song", not {query!r}'
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        host, parts = "", None
    if parts is None or parts.scheme not in ("http", "https") or not host:
        return f'--overwrite takes the song, then an http(s) link, not {url!r}'
    if host == "spotify.link" or host == "spotify.com" or host.endswith(".spotify.com"):
        return "Spotarchive never takes audio from Spotify; give a link to another site"
    return None


def find_copies(data_dir: Path, query: str, extensions: frozenset[str]) -> Search:
    """Every copy of the song *query* ("Artist - Song") in *data_dir* and its subfolders."""
    wanted = normalize(query)
    folders = []
    if data_dir.is_dir():
        # data_dir itself: only the files lying directly in it.
        folders = [data_dir] + sorted(
            p for p in data_dir.iterdir()
            if p.is_dir() and not p.name.startswith((".", "browser-profile"))
        )

    libraries = []
    for folder in folders:
        songs = setlist_songs(folder) if (folder / SETLIST_FILENAME).is_file() else []
        libraries.append((folder, songs, [s for s in songs if wanted in expected_stems(s)]))
    # Whether the query names a song of some setlist (rather than just a file).
    known = any(matched for _, _, matched in libraries)

    search = Search()
    for folder, songs, matched in libraries:
        _search_folder(search, folder, folder == data_dir, songs, matched, known, query, wanted, extensions)
    return search


def _search_folder(search: Search, folder: Path, top_level: bool, songs: list[Song], matched: list[Song],
                   known: bool, query: str, wanted: str, extensions: frozenset[str]) -> None:
    record = load_record(folder)
    by_key = {s.key: s for s in songs}
    paths = folder.glob("*") if top_level else folder.rglob("*")
    named = []
    for p in sorted(paths):
        relative = p.relative_to(folder)
        hidden = any(part.startswith(".") for part in relative.parts)
        if not hidden and p.is_file() and p.suffix.lower() in extensions and normalize(p.stem) == wanted:
            named.append(relative.as_posix())

    copies: list[Copy] = []

    def add(song: Song, from_setlist: bool, files: list[str], keys: list[str]) -> None:
        # Copies sharing a file are one: it can only be replaced once.
        for copy in copies:
            if set(copy.files) & set(files):
                copy.files = sorted({*copy.files, *files})
                copy.keys += [k for k in keys if k not in copy.keys]
                return
        copies.append(Copy(folder, song, from_setlist, sorted(files), list(keys)))

    matched_keys = {s.key for s in matched}
    present = find_present(matched, folder, record, extensions) if matched else {}
    for song in matched:
        files = present.get(song.key, [])
        recorded = record.get(song.key, {}).get("files", [])
        # A match by name only must not take a file recorded as another song.
        files = [
            f for f in files
            if f in recorded or all(k in matched_keys for k, e in record.items() if f in e.get("files", []))
        ]
        if files:
            add(song, True, files, [song.key])

    for file in named:
        if any(file in copy.files for copy in copies):
            continue
        owners = [key for key, entry in record.items() if file in entry.get("files", [])]
        if matched:
            if not owners:
                # A second file of the song (or its only, unrecorded one).
                song = matched[0]
                own = next((c for c in copies if song.key in c.keys), None)
                if own:
                    own.files = sorted([*own.files, file])
                else:
                    add(song, True, [file], [song.key])
                continue
        if owners and known:
            label = record[owners[0]].get("label") or "another song"
            search.notes.append(
                f"Left {folder / file} alone: it was downloaded as {label!r}. "
                f"If it's wrong too, run --overwrite with that name."
            )
            continue
        # The user named the file itself: it may be the wrong match for
        # another song, which then provides the name and tags.
        owner = next((by_key[key] for key in owners if key in by_key), None)
        if owner:
            add(owner, True, record[owner.key]["files"], owners)
        else:
            artist, _, title = query.partition(SEPARATOR)
            add(Song(name=title.strip(), artists=(artist.strip(),)), False, [file], owners)

    for copy in copies:
        copy.files = [f for f in copy.files if (folder / f).is_file()]
    found = {key for copy in copies for key in copy.keys}
    if matched_keys - found:
        search.missing.append(folder.name)
    search.copies += copies


def replace_copy(copy: Copy, source: Path, url: str) -> Replaced:
    """Put a copy of *source* (downloaded from *url*) in place of *copy*'s files.

    The new file is complete before anything is removed. Raises
    :class:`OSError` when it can't be put there.
    """
    first = Path(copy.files[0])
    directory = copy.folder / first.parent
    stem = _shorten(safe_name(copy.song.label) if copy.from_setlist else first.stem)
    suffix = source.suffix.lower()
    old = {copy.folder / f for f in copy.files}
    target = directory / f"{stem}{suffix}"
    number = 1
    # Never write over a file that isn't being replaced.
    while target.exists() and target not in old:
        number += 1
        target = directory / f"{stem} ({number}){suffix}"

    warnings = []
    tagged = False
    fd, name = tempfile.mkstemp(prefix=".override-", suffix=suffix, dir=directory)
    os.close(fd)
    staged = Path(name)
    try:
        # copyfile keeps mkstemp's owner-only permissions off the song.
        shutil.copyfile(source, staged)
        shutil.copymode(source, staged)
        try:
            # The partial setlist info of a file without a setlist row still
            # beats whatever the link's file carries.
            write_setlist_tags(staged, copy.song)
            tagged = True
        except TaggingFailed as exc:
            warnings.append(str(exc).replace(staged.name, target.name))
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)

    for path in old - {target}:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            warnings.append(f"Couldn't delete the old file {path}: {exc}")

    new = target.relative_to(copy.folder).as_posix()
    warnings += _update_record(copy, new, url, tagged)
    return Replaced(new, warnings)


def _update_record(copy: Copy, new: str, url: str, tagged: bool) -> list[str]:
    record = load_record(copy.folder)
    gone = {*copy.files, new}
    warnings = []
    changed = False
    # Other songs that were recorded with a file that's now something else.
    for key, entry in list(record.items()):
        files = entry.get("files", [])
        if key in copy.keys or not gone.intersection(files):
            continue
        left = [f for f in files if f not in gone]
        if left:
            record[key] = {**entry, "files": left}
        else:
            del record[key]
            warnings.append(
                f"{entry.get('label') or key} shared the replaced file; "
                "`spotarchive --download` will download it again."
            )
        changed = True
    for key in copy.keys:
        label = record.get(key, {}).get("label") or copy.song.label
        record[key] = {"label": label, "files": [new], "override": url}
        if tagged:
            record[key]["tagged"] = "setlist"
        changed = True
    if changed:
        save_record(copy.folder, record)
    return warnings


def _shorten(stem: str) -> str:
    cut = stem.encode("utf-8")[:_MAX_STEM_BYTES].decode("utf-8", errors="ignore")
    return cut.rstrip(" .") or "song"
