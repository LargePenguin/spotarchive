"""Per-playlist folders and their ``setlist.csv`` metadata files."""

from __future__ import annotations

import csv
import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path

from spotarchive.spotify.models import Track

SETLIST_FILENAME = "setlist.csv"
COLUMNS = ["name", "artist", "album", "release_date", "date_added", "duration_ms", "isrc", "spotify_url"]
# Artist names can contain commas ("Tyler, The Creator"), so join with ";".
ARTIST_SEPARATOR = "; "
MAX_FOLDER_NAME = 100

# Characters Windows forbids in file names, plus control characters.
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)}


def safe_folder_name(name: str) -> str:
    """Turn a playlist name into a folder name that is valid on Linux and Windows."""
    cleaned = " ".join(_INVALID_CHARS.sub("_", name).split())
    # Windows drops trailing dots and spaces, so never end with them.
    cleaned = cleaned[:MAX_FOLDER_NAME].strip(" .")
    if not cleaned:
        return "playlist"
    if cleaned.split(".")[0].upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def assign_folders(
    names: dict[str, str],
    previous: dict[str, str | None],
    reserved: set[str] = frozenset(),
) -> dict[str, str]:
    """Pick a unique folder for each playlist ID.

    Playlists keep the folder they had before when it still matches their
    name. Clashes with each other or with *reserved* folders (compared
    case-insensitively, as Windows does) get the playlist ID appended so the
    result doesn't depend on ordering.
    """
    result: dict[str, str] = {}
    taken = {r.casefold() for r in reserved}
    for pid, name in names.items():
        folder = safe_folder_name(name)
        if previous.get(pid) == folder and folder.casefold() not in taken:
            result[pid] = folder
            taken.add(folder.casefold())
    for pid, name in names.items():
        if pid in result:
            continue
        folder = safe_folder_name(name)
        if folder.casefold() in taken:
            folder = f"{folder} ({safe_folder_name(pid)})"
        result[pid] = folder
        taken.add(folder.casefold())
    return result


def prepare_folder(data_dir: Path, folder: str, previous: str | None) -> Path:
    """Create the playlist folder, moving the old one if the playlist was renamed."""
    target = data_dir / folder
    if previous and previous != folder:
        old = data_dir / previous
        if old.is_dir() and not target.exists():
            old.rename(target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def track_row(track: Track) -> dict[str, str]:
    row = asdict(track)
    row["artist"] = ARTIST_SEPARATOR.join(row.pop("artists"))
    row["duration_ms"] = "" if track.duration_ms is None else str(track.duration_ms)
    return row


def write_setlist(folder: Path, tracks: list[Track]) -> Path:
    """Atomically (re)write ``setlist.csv`` in *folder*."""
    path = folder / SETLIST_FILENAME
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{SETLIST_FILENAME}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(track_row(t) for t in tracks)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def read_setlist(folder: Path) -> list[dict[str, str]]:
    with open(folder / SETLIST_FILENAME, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))
