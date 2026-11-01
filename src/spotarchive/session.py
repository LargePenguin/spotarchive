"""The session file at the root of the data folder.

It holds the captured Spotify login (cookies, user agent, cached access
token), the optional YouTube login (cookies, user agent) and the user's
playlist selections. It contains credentials, so it is
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
class YouTubeSession:
    """A YouTube login, handed to download backends that can use it."""

    # youtube.com cookies in Playwright's format (name, value, domain, path,
    # expires in seconds since the epoch or -1, httpOnly, secure, ...).
    cookies: list[dict]
    user_agent: str
    logged_in_at_ms: int = 0

    def netscape_cookies(self) -> str:
        """The cookies as a Netscape ``cookies.txt`` file, as yt-dlp's ``--cookies`` reads it."""
        lines = ["# Netscape HTTP Cookie File", "# Written by spotarchive; do not share.", ""]
        for c in self.cookies:
            domain = c.get("domain", "")
            expires = c.get("expires", -1)
            lines.append("\t".join([
                domain,
                "TRUE" if domain.startswith(".") else "FALSE",
                c.get("path") or "/",
                "TRUE" if c.get("secure") else "FALSE",
                str(int(expires)) if expires and expires > 0 else "0",
                c.get("name", ""),
                c.get("value", ""),
            ]))
        return "\n".join(lines) + "\n"


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
    youtube: YouTubeSession | None = None
    # False once the user said "don't ask again" to the YouTube login offer.
    offer_youtube_login: bool = True
    playlists: dict[str, PlaylistState] = field(default_factory=dict)

    @classmethod
    def load(cls, data_dir: Path) -> SessionStore:
        path = Path(data_dir) / SESSION_FILENAME
        if not path.exists():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            spotify = raw.get("spotify")
            youtube = raw.get("youtube")
            return cls(
                path=path,
                spotify=SpotifySession(**spotify) if spotify else None,
                youtube=YouTubeSession(**youtube) if youtube else None,
                offer_youtube_login=bool(raw.get("offer_youtube_login", True)),
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
            "youtube": asdict(self.youtube) if self.youtube else None,
            "offer_youtube_login": self.offer_youtube_login,
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
