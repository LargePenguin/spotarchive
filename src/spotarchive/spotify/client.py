"""A small read-only client for the Spotify web player's API.

Spotify's public Web API (``api.spotify.com``) rate-limits the web player's
tokens, so this talks to the GraphQL endpoint the web player itself uses
("pathfinder"). Each query is identified by a hash that the web player's
JavaScript defines; they're read from it on every token refresh (see
:mod:`spotarchive.spotify.auth`), with the values below as a fallback.

Only playlist metadata is ever requested; no audio is fetched from Spotify.
"""

from __future__ import annotations

import time
from typing import Callable, Iterator

import requests

from spotarchive.spotify.auth import TokenProvider
from spotarchive.spotify.models import LIKED_SONGS_ID, Playlist, Track

PATHFINDER_URL = "https://api-partner.spotify.com/pathfinder/v2/query"
# Hashes of the persisted queries used here, as of October 2026.
DEFAULT_QUERY_HASHES = {
    "fetchLibraryTracks": "087278b20b743578a6262c2b0b4bcd20d879c503cc359a2285baf083ef944240",
    "fetchPlaylist": "21ea11a6ac75a5f3e01fd365d29c18e0a4a8b7dc09f89bbb5d73910190c6acaf",
    "libraryV3": "390c78e5b951029bad359785e69b07b536a509c581cbcd0aded5e5067f187455",
    "profileAttributes": "08ffb4730af3746e04a8301396f20875dbbce10c75243803091a9274eacc8ac0",
}
LIBRARY_PAGE_SIZE = 50
TRACKS_PAGE_SIZE = 50
PLAYLIST_PAGE_SIZE = 100
MAX_RETRIES = 5
MAX_BACKOFF_S = 60
REQUEST_TIMEOUT_S = 30
# Pathfinder's answer to a query hash it doesn't know (any more).
UNKNOWN_QUERY_STATUS = 412

ProgressCallback = Callable[[int, "int | None"], None]
WaitCallback = Callable[[float], None]


class SpotifyApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Spotify API error {status}: {message}")
        self.status = status


class RateLimited(SpotifyApiError):
    """Spotify asked to wait longer than is worth waiting for."""

    def __init__(self, message: str, retry_after_s: float | None):
        super().__init__(429, message)
        self.retry_after_s = retry_after_s


class QueryOutdated(SpotifyApiError):
    """Spotify no longer knows a query, even after re-reading the web player."""

    def __init__(self, operation: str):
        super().__init__(
            UNKNOWN_QUERY_STATUS,
            f"Spotify's web player no longer accepts the {operation!r} query; "
            "spotarchive needs an update to keep up with it.",
        )


class SpotifyClient:
    def __init__(
        self,
        tokens: TokenProvider,
        user_agent: str = "",
        http: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_wait: WaitCallback = lambda seconds: None,
    ):
        self.tokens = tokens
        self.user_agent = user_agent
        self.http = http or requests.Session()
        self._sleep = sleep
        # Called with the delay before each retry, so a slow run says why.
        self._on_wait = on_wait

    def _query_hash(self, operation: str) -> str:
        return {**DEFAULT_QUERY_HASHES, **self.tokens.query_hashes()}[operation]

    def _query(self, operation: str, variables: dict) -> dict:
        refreshed = False
        rehashed = False
        retries = 0
        while True:
            headers = {
                "Authorization": f"Bearer {self.tokens.get_token(force_refresh=False)}",
                "App-Platform": "WebPlayer",
                "Accept": "application/json",
            }
            if self.user_agent:
                headers["User-Agent"] = self.user_agent
            body = {
                "variables": variables,
                "operationName": operation,
                "extensions": {"persistedQuery": {"version": 1, "sha256Hash": self._query_hash(operation)}},
            }
            resp = self.http.post(PATHFINDER_URL, json=body, headers=headers, timeout=REQUEST_TIMEOUT_S)

            if resp.status_code == 401 and not refreshed:
                self.tokens.get_token(force_refresh=True)
                refreshed = True
                continue
            if resp.status_code == UNKNOWN_QUERY_STATUS:
                if rehashed:
                    raise QueryOutdated(operation)
                # The web player was updated; a refresh re-reads its hashes.
                self.tokens.get_token(force_refresh=True)
                rehashed = True
                continue
            if resp.status_code == 429:
                retry_after = _retry_after(resp)
                # Spotify can ask for hours; fail now instead of looking hung.
                if retries >= MAX_RETRIES or (retry_after or 0) > MAX_BACKOFF_S:
                    raise RateLimited(_error_message(resp), retry_after)
            if (resp.status_code == 429 or resp.status_code >= 500) and retries < MAX_RETRIES:
                delay = _backoff(resp, retries)
                self._on_wait(delay)
                self._sleep(delay)
                retries += 1
                continue
            if not resp.ok:
                raise SpotifyApiError(resp.status_code, _error_message(resp))
            try:
                payload = resp.json()
            except ValueError:
                raise SpotifyApiError(resp.status_code, "response isn't JSON") from None
            if not payload.get("data"):
                raise SpotifyApiError(resp.status_code, _error_message(resp))
            return payload["data"]

    def _paginate(
        self, operation: str, variables: dict, page_size: int, page_of: Callable[[dict], dict],
    ) -> Iterator[tuple[dict, int | None]]:
        """Yield ``(item, total)`` for every item across all pages.

        *page_of* picks the page (``items`` + ``totalCount``) out of a response.
        """
        offset = 0
        while True:
            page = page_of(self._query(operation, {**variables, "offset": offset, "limit": page_size}))
            items = page.get("items") or []
            total = page.get("totalCount")
            for item in items:
                yield item, total
            offset += len(items)
            if not items or total is None or offset >= total:
                return

    def current_user(self) -> dict:
        """``{"id": ..., "display_name": ...}`` of the logged-in user."""
        profile = ((self._query("profileAttributes", {}).get("me") or {}).get("profile")) or {}
        if not profile:
            raise SpotifyApiError(401, "Spotify didn't return a profile; the login isn't valid.")
        return {"id": profile.get("username"), "display_name": profile.get("name")}

    def playlists(self, include_liked_songs: bool = True) -> list[Playlist]:
        """The user's own and followed playlists, plus Liked Songs first."""
        variables = {
            "filters": ["Playlists"],
            "order": None,
            "textFilter": "",
            "features": ["LIKED_SONGS"],
            "flatten": True,  # list playlists inside folders too
            "expandedFolders": [],
            "folderUri": None,
            "includeFoldersWhenFlattening": False,
        }
        found = [
            playlist
            for entry, _ in self._paginate("libraryV3", variables, LIBRARY_PAGE_SIZE, _library_page)
            if (playlist := Playlist.from_library_item((entry or {}).get("item") or {}))
        ]
        liked = next((p for p in found if p.is_liked_songs), None)
        others = [p for p in found if not p.is_liked_songs]
        if not include_liked_songs:
            return others
        return [liked or Playlist(id=LIKED_SONGS_ID, name="Liked Songs")] + others

    def playlist_tracks(self, playlist: Playlist, on_progress: ProgressCallback | None = None) -> list[Track]:
        if playlist.is_liked_songs:
            entries = self._paginate("fetchLibraryTracks", {}, TRACKS_PAGE_SIZE, _liked_songs_page)
            parse = _liked_song
        else:
            entries = self._paginate(
                "fetchPlaylist", {"uri": playlist.uri, "enableWatchFeedEntrypoint": False}, PLAYLIST_PAGE_SIZE,
                _playlist_page,
            )
            parse = _playlist_song

        tracks = []
        seen = 0
        for entry, total in entries:
            seen += 1
            track = parse(entry or {})
            if track:
                tracks.append(track)
            if on_progress:
                on_progress(seen, total)
        return tracks


def _library_page(data: dict) -> dict:
    return (data.get("me") or {}).get("libraryV3") or {}


def _liked_songs_page(data: dict) -> dict:
    return ((data.get("me") or {}).get("library") or {}).get("tracks") or {}


def _liked_song(entry: dict) -> Track | None:
    track = entry.get("track") or {}
    return Track.from_api(track.get("data"), entry.get("addedAt"), track.get("_uri") or "")


def _playlist_song(entry: dict) -> Track | None:
    return Track.from_api((entry.get("itemV2") or {}).get("data"), entry.get("addedAt"))


def _playlist_page(data: dict) -> dict:
    playlist = data.get("playlistV2") or {}
    if playlist.get("__typename") != "Playlist":
        # e.g. NotFound for a deleted playlist, with a message saying so.
        raise SpotifyApiError(404, playlist.get("message") or "playlist not available")
    return playlist.get("content") or {}


def _retry_after(resp: requests.Response) -> float | None:
    try:
        return float(resp.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


def _backoff(resp: requests.Response, retries: int) -> float:
    retry_after = _retry_after(resp)
    if retry_after is not None:
        return min(retry_after + 1, MAX_BACKOFF_S)
    return min(2 ** retries, MAX_BACKOFF_S)


def _error_message(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return resp.reason or "unknown error"
    if not isinstance(payload, dict):
        return str(payload) or resp.reason
    errors = payload.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return errors[0].get("message") or resp.reason
    error = payload.get("error")
    if isinstance(error, dict):
        return error.get("message") or resp.reason
    return str(error or resp.reason)
