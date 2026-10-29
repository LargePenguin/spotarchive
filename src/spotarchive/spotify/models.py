"""Plain data objects parsed from the web player's (pathfinder) responses.

The web player's API doesn't return ISRCs, so :attr:`Track.isrc` stays empty;
Liked Songs also come without the album's release date.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Pseudo playlist ID for the user's Liked Songs, which the API serves from a
# separate query.
LIKED_SONGS_ID = "liked-songs"
LIKED_SONGS_URI = "spotify:collection:tracks"

OPEN_URL = "https://open.spotify.com"
# How much of an ISO date to keep for each date precision.
DATE_LENGTHS = {"YEAR": 4, "MONTH": 7, "DAY": 10}
SONG_TYPES = ("Track", "LocalTrack")


def uri_id(uri: str) -> str:
    """``spotify:playlist:abc`` -> ``abc``."""
    return uri.rsplit(":", 1)[-1]


def web_url(uri: str) -> str:
    """``spotify:track:abc`` -> ``https://open.spotify.com/track/abc``."""
    parts = uri.split(":")
    if len(parts) != 3 or parts[0] != "spotify":
        return ""
    return f"{OPEN_URL}/{parts[1]}/{parts[2]}"


def _date(value: dict | None) -> str:
    value = value or {}
    iso = value.get("isoString") or ""
    return iso[:DATE_LENGTHS.get(value.get("precision"), 10)]


def _names(artists: dict | None) -> list[str]:
    return [
        name for a in (artists or {}).get("items") or []
        if (name := (a.get("profile") or {}).get("name"))
    ]


@dataclass
class Playlist:
    id: str
    name: str
    owner: str = ""
    total: int | None = None

    @property
    def is_liked_songs(self) -> bool:
        return self.id == LIKED_SONGS_ID

    @property
    def uri(self) -> str:
        return LIKED_SONGS_URI if self.is_liked_songs else f"spotify:playlist:{self.id}"

    @classmethod
    def from_library_item(cls, item: dict) -> Playlist | None:
        """Parse one ``libraryV3`` entry; ``None`` for anything but playlists."""
        data = item.get("data") or {}
        uri = data.get("uri") or item.get("_uri") or ""
        if data.get("__typename") == "PseudoPlaylist" and uri == LIKED_SONGS_URI:
            return cls(id=LIKED_SONGS_ID, name="Liked Songs", total=data.get("count"))
        if data.get("__typename") != "Playlist" or not uri.startswith("spotify:playlist:"):
            return None
        owner = (data.get("ownerV2") or {}).get("data") or {}
        return cls(
            id=uri_id(uri),
            name=data.get("name") or uri_id(uri),
            owner=owner.get("name") or owner.get("username") or "",
        )


@dataclass
class Track:
    name: str
    artists: list[str] = field(default_factory=list)
    album: str = ""
    release_date: str = ""
    date_added: str = ""
    duration_ms: int | None = None
    isrc: str = ""
    spotify_url: str = ""

    @classmethod
    def from_api(cls, data: dict | None, added_at: dict | None = None, uri: str = "") -> Track | None:
        """Parse a song from ``fetchPlaylist``/``fetchLibraryTracks``.

        Returns ``None`` for anything that isn't a song: podcast episodes and
        items no longer available on Spotify.
        """
        if not data or data.get("__typename", "Track") not in SONG_TYPES:
            return None
        album = data.get("albumOfTrack") or {}
        duration = data.get("trackDuration") or data.get("duration") or {}
        return cls(
            name=data.get("name") or "",
            artists=_names(data.get("artists")),
            album=album.get("name") or "",
            release_date=_date(album.get("date")),
            date_added=(added_at or {}).get("isoString") or "",
            duration_ms=duration.get("totalMilliseconds"),
            spotify_url=web_url(data.get("uri") or uri),
        )
