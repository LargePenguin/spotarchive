"""Download backends. Add a new one by subclassing ``DownloadBackend`` and listing it here."""

from __future__ import annotations

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
from spotarchive.backends.somedl import SomeDLBackend
from spotarchive.backends.ytdlp import YtDlpBackend

DEFAULT_BACKEND = "somedl"
# The backend that imports links (``--download LINK``), whatever is configured.
IMPORT_BACKEND = "ytdlp"
BACKENDS: dict[str, Callable[..., DownloadBackend]] = {
    "somedl": SomeDLBackend,
    "ytdlp": YtDlpBackend,
}

__all__ = [
    "BACKENDS",
    "DEFAULT_BACKEND",
    "BackendUnavailable",
    "DownloadBackend",
    "DownloadEvents",
    "DownloadFailed",
    "IMPORT_BACKEND",
    "ImportItem",
    "ImportListing",
    "Song",
    "TaggingFailed",
    "get_backend",
]


def get_backend(name: str = DEFAULT_BACKEND, **options) -> DownloadBackend:
    try:
        factory = BACKENDS[name]
    except KeyError:
        known = ", ".join(sorted(BACKENDS))
        raise BackendUnavailable(f"Unknown download backend {name!r} (known: {known}).") from None
    return factory(**options)
