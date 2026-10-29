"""Download backends. Add a new one by subclassing ``DownloadBackend`` and listing it here."""

from __future__ import annotations

from typing import Callable

from spotarchive.backends.base import BackendUnavailable, DownloadBackend, DownloadFailed, Song, TaggingFailed
from spotarchive.backends.somedl import SomeDLBackend

DEFAULT_BACKEND = "somedl"
BACKENDS: dict[str, Callable[..., DownloadBackend]] = {
    "somedl": SomeDLBackend,
}

__all__ = [
    "BACKENDS",
    "DEFAULT_BACKEND",
    "BackendUnavailable",
    "DownloadBackend",
    "DownloadFailed",
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
