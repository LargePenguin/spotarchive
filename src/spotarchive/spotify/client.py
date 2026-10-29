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
        error = resp.json().get("error")
        if isinstance(error, dict):
            return error.get("message") or resp.reason
        return str(error or resp.reason)
    except ValueError:
        return resp.reason or "unknown error"
