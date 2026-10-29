"""A small read-only client for the Spotify Web API.

Only playlist metadata is ever requested; no audio is fetched from Spotify.
"""

from __future__ import annotations

import time
from typing import Callable, Iterator

import requests

from spotarchive.spotify.auth import TokenProvider
from spotarchive.spotify.models import LIKED_SONGS_ID, Playlist, Track

API_URL = "https://api.spotify.com/v1"
PAGE_SIZE = 50
MAX_RETRIES = 5
MAX_BACKOFF_S = 60
REQUEST_TIMEOUT_S = 30

ProgressCallback = Callable[[int, "int | None"], None]


class SpotifyApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Spotify API error {status}: {message}")
        self.status = status


class SpotifyClient:
    def __init__(
        self,
        tokens: TokenProvider,
        user_agent: str = "",
        http: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.tokens = tokens
        self.user_agent = user_agent
        self.http = http or requests.Session()
        self._sleep = sleep

    def _get(self, url: str, params: dict | None = None) -> dict:
        if not url.startswith("http"):
            url = API_URL + url
        refreshed = False
        retries = 0
        while True:
            headers = {
                "Authorization": f"Bearer {self.tokens.get_token(force_refresh=False)}",
                "Accept": "application/json",
            }
            if self.user_agent:
                headers["User-Agent"] = self.user_agent
            resp = self.http.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_S)

            if resp.status_code == 401 and not refreshed:
                self.tokens.get_token(force_refresh=True)
                refreshed = True
                continue
            if (resp.status_code == 429 or resp.status_code >= 500) and retries < MAX_RETRIES:
                self._sleep(_backoff(resp, retries))
                retries += 1
                continue
            if not resp.ok:
                raise SpotifyApiError(resp.status_code, _error_message(resp))
            return resp.json()

    def _paginate(self, url: str, params: dict | None = None) -> Iterator[tuple[dict, int | None]]:
        """Yield ``(item, total)`` for every item across all pages."""
        page = self._get(url, params)
        while True:
            total = page.get("total")
            for item in page.get("items") or []:
                yield item, total
            if not page.get("next"):
                return
            page = self._get(page["next"])

    def current_user(self) -> dict:
        return self._get("/me")

    def playlists(self, include_liked_songs: bool = True) -> list[Playlist]:
        """The user's own and followed playlists, plus Liked Songs first."""
        result = []
        if include_liked_songs:
            liked = self._get("/me/tracks", {"limit": 1})
            result.append(Playlist(id=LIKED_SONGS_ID, name="Liked Songs", total=liked.get("total")))
        result.extend(
            Playlist.from_api(item)
            for item, _ in self._paginate("/me/playlists", {"limit": PAGE_SIZE})
            if item and item.get("id")
        )
        return result

    def playlist_tracks(self, playlist: Playlist, on_progress: ProgressCallback | None = None) -> list[Track]:
        if playlist.is_liked_songs:
            return self._collect_tracks("/me/tracks", on_progress)
        # `/items` replaced `/tracks` in February 2026; fall back for clients
        # still on the old API.
        try:
            return self._collect_tracks(f"/playlists/{playlist.id}/items", on_progress)
        except SpotifyApiError as exc:
            if exc.status not in (403, 404):
                raise
        return self._collect_tracks(f"/playlists/{playlist.id}/tracks", on_progress)

    def _collect_tracks(self, url: str, on_progress: ProgressCallback | None) -> list[Track]:
        tracks = []
        seen = 0
        for entry, total in self._paginate(url, {"limit": PAGE_SIZE}):
            seen += 1
            track = Track.from_playlist_entry(entry or {})
            if track:
                tracks.append(track)
            if on_progress:
                on_progress(seen, total)
        return tracks


def _backoff(resp: requests.Response, retries: int) -> float:
    try:
        return min(float(resp.headers["Retry-After"]) + 1, MAX_BACKOFF_S)
    except (KeyError, ValueError):
        return min(2 ** retries, MAX_BACKOFF_S)


def _error_message(resp: requests.Response) -> str:
    try:
        error = resp.json().get("error")
        if isinstance(error, dict):
            return error.get("message") or resp.reason
        return str(error or resp.reason)
    except ValueError:
        return resp.reason or "unknown error"
