"""The download cache: the folder songs are downloaded into before they're moved.

Backends make one staging folder per song there and remove it afterwards. A
run that is killed (or crashes) leaves its folders behind, so every download
starts by clearing them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from spotarchive.backends import STAGING_PREFIX


def clear_stale(cache_dir: Path) -> tuple[int, list[tuple[Path, str]]]:
    """Create *cache_dir* if needed and delete the staging folders left in it.

    Only those: ``cache_dir`` is the user's choice and may hold other files,
    which are never touched. Returns how many were deleted, and the ones that
    couldn't be, with the reason. Raises :class:`OSError` when the cache
    folder can't be created or read.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    removed, failed = 0, []
    for entry in sorted(cache_dir.iterdir()):
        if not entry.name.startswith(STAGING_PREFIX) or entry.is_symlink() or not entry.is_dir():
            continue
        try:
            shutil.rmtree(entry)
        except OSError as exc:
            failed.append((entry, str(exc)))
        else:
            removed += 1
    return removed, failed
