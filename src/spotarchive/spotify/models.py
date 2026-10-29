"""Plain data objects parsed from Spotify Web API responses.

Spotify renamed playlist fields in February 2026 (``tracks`` -> ``items``,
``track`` -> ``item``), so the parsers accept both spellings.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Pseudo playlist ID for the user's Liked Songs, which the API serves from a
# separate endpoint.
LIKED_SONGS_ID = "liked-songs"


@dataclass
class Playlist:
    id: str
    name: str
    owner: str = ""
    total: int | None = None

    @property
    def is_liked_songs(self) -> bool:
        return self.id == LIKED_SONGS_ID

    @classmethod
    def from_api(cls, data: dict) -> Playlist:
        listing = data.get("items") or data.get("tracks") or {}
        owner = data.get("owner") or {}
        return cls(
            id=data["id"],
            name=data.get("name") or data["id"],
            owner=owner.get("display_name") or owner.get("id") or "",
            total=listing.get("total") if isinstance(listing, dict) else None,
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
    def from_playlist_entry(cls, entry: dict) -> Track | None:
        """Parse one playlist/saved-tracks entry; ``None`` for non-songs.

        Entries can be ``null`` (removed from Spotify) or podcast episodes,
        which aren't songs and are skipped.
        """
        track = entry.get("item") or entry.get("track")
        if not track or track.get("type", "track") != "track":
            return None
        album = track.get("album") or {}
        return cls(
            name=track.get("name") or "",
            artists=[a["name"] for a in track.get("artists") or [] if a.get("name")],
            album=album.get("name") or "",
            release_date=album.get("release_date") or "",
            date_added=entry.get("added_at") or "",
            duration_ms=track.get("duration_ms"),
            isrc=(track.get("external_ids") or {}).get("isrc") or "",
            spotify_url=(track.get("external_urls") or {}).get("spotify") or "",
        )
