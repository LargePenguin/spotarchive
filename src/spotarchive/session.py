"""The session file at the root of the data folder.

It holds the captured Spotify login (cookies, user agent, cached access
token) and the user's playlist selections. It contains credentials, so it is
written with owner-only permissions where the platform supports it.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SESSION_FILENAME = "session.json"
SESSION_VERSION = 1
DEFAULT_DATA_DIR = Path("data")

# Refresh the access token this long before Spotify says it expires.
TOKEN_EXPIRY_MARGIN_MS = 60_000


class SessionError(Exception):
    """The session file exists but can't be used."""


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class SpotifySession:
    cookies: list[dict]
    user_agent: str
    access_token: str | None = None
    expires_at_ms: int = 0
    user_id: str | None = None
    display_name: str | None = None
    # Persisted-query hashes read from the web player (operation -> hash).
    query_hashes: dict[str, str] = field(default_factory=dict)

    def token_is_fresh(self, at_ms: int | None = None) -> bool:
        if not self.access_token:
            return False
        at_ms = now_ms() if at_ms is None else at_ms
        return at_ms < self.expires_at_ms - TOKEN_EXPIRY_MARGIN_MS


@dataclass
class PlaylistState:
    name: str
    selected: bool = False
    # Folder name under the data folder, kept so a renamed playlist can be
    # moved instead of losing its downloads.
    folder: str | None = None


@dataclass
class SessionStore:
    path: Path
    spotify: SpotifySession | None = None
    playlists: dict[str, PlaylistState] = field(default_factory=dict)

    @classmethod
    def load(cls, data_dir: Path) -> SessionStore:
        path = Path(data_dir) / SESSION_FILENAME
        if not path.exists():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            spotify = raw.get("spotify")
            return cls(
                path=path,
                spotify=SpotifySession(**spotify) if spotify else None,
                playlists={
                    pid: PlaylistState(**state)
                    for pid, state in raw.get("playlists", {}).items()
                },
            )
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise SessionError(
                f"Can't read the session file {path}: {exc}. "
                "Delete it and log in again."
            ) from exc

    def selected_ids(self) -> list[str]:
        return [pid for pid, state in self.playlists.items() if state.selected]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": SESSION_VERSION,
            "spotify": asdict(self.spotify) if self.spotify else None,
            "playlists": {pid: asdict(s) for pid, s in self.playlists.items()},
        }
        _write_private(self.path, json.dumps(data, indent=2, ensure_ascii=False))


def _write_private(path: Path, text: str) -> None:
    """Atomically write *text* to *path*, readable only by the owner on POSIX."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
