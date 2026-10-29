"""Fakes for the Spotify layer. Tests never talk to Spotify."""

from __future__ import annotations

import pytest

from spotarchive import config
from spotarchive.backends import DownloadBackend, DownloadFailed, TaggingFailed
from spotarchive.spotify.client import PATHFINDER_URL


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.reason = "fake reason"

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeHttp:
    """Serves queued responses per pathfinder operation name."""

    def __init__(self):
        self.routes: dict[str, list[FakeResponse]] = {}
        # (operationName, variables, sha256Hash, headers) per request
        self.calls: list[tuple[str, dict, str, dict]] = []

    def add(self, operation, *responses):
        self.routes.setdefault(operation, []).extend(responses)

    def post(self, url, json=None, headers=None, timeout=None):
        assert url == PATHFINDER_URL
        operation = json["operationName"]
        query_hash = json["extensions"]["persistedQuery"]["sha256Hash"]
        self.calls.append((operation, json["variables"], query_hash, headers or {}))
        queue = self.routes.get(operation)
        if not queue:
            return FakeResponse(412, None)
        # The last response repeats so routes can be hit several times.
        return queue.pop(0) if len(queue) > 1 else queue[0]


class FakeTokens:
    def __init__(self, hashes=None):
        self.refreshes = 0
        self.hashes = dict(hashes or {})
        # Hashes "read from the web player" by the next refresh, if any.
        self.hashes_after_refresh = None

    def get_token(self, force_refresh=False):
        if force_refresh:
            self.refreshes += 1
            if self.hashes_after_refresh is not None:
                self.hashes = self.hashes_after_refresh
        return f"dummy-token-{self.refreshes}"

    def query_hashes(self):
        return self.hashes


def track_json(name, artists=("Fake Artist",), album="Fake Album", release="2001-02-03T00:00:00Z",
               precision="DAY", track_id=None):
    track_id = track_id or name.replace(" ", "")
    return {
        "__typename": "Track",
        "name": name,
        "uri": f"spotify:track:{track_id}",
        "artists": {"items": [{"profile": {"name": a}, "uri": "spotify:artist:fake"} for a in artists]},
        "albumOfTrack": {"name": album, "uri": "spotify:album:fake",
                         "date": {"isoString": release, "precision": precision}},
        "trackDuration": {"totalMilliseconds": 180000},
    }


def playlist_entry(data, added="2024-01-01T00:00:00Z"):
    return {"addedAt": {"isoString": added}, "itemV2": {"__typename": "TrackResponseWrapper", "data": data}}


def ok(data):
    return FakeResponse(200, {"data": data})


def playlist_page(entries, total=None):
    total = len(entries) if total is None else total
    return ok({"playlistV2": {"__typename": "Playlist",
                              "content": {"__typename": "PlaylistItemsPage", "items": entries, "totalCount": total}}})


def library_page(items, total=None):
    total = len(items) if total is None else total
    return ok({"me": {"libraryV3": {"__typename": "LibraryPage", "items": [{"item": i} for i in items],
                                    "totalCount": total}}})


def library_playlist(playlist_id, name, owner="Fake Owner"):
    return {"__typename": "PlaylistResponseWrapper", "_uri": f"spotify:playlist:{playlist_id}",
            "data": {"__typename": "Playlist", "uri": f"spotify:playlist:{playlist_id}", "name": name,
                     "ownerV2": {"data": {"__typename": "User", "name": owner}}}}


@pytest.fixture
def http():
    return FakeHttp()


@pytest.fixture
def tokens():
    return FakeTokens()


# A few silent MPEG frames (128 kbps, 44.1 kHz): enough for mutagen to tag.
SILENT_MP3 = (b"\xff\xfb\x90\x64" + b"\x00" * 413) * 4


class FakeBackend(DownloadBackend):
    """Writes a silent MP3 per song instead of downloading anything."""

    name = "fake"
    audio_extensions = frozenset({".mp3"})

    def __init__(self, failing=(), unavailable=None, can_tag=False, tags_downloads=False):
        self.failing = set(failing)
        self.unavailable = unavailable
        self.can_tag = can_tag
        self.tags_downloads = tags_downloads
        self.requested = []
        self.tagged = []

    def check(self):
        if self.unavailable:
            raise self.unavailable

    def download(self, song, folder):
        self.requested.append(song.name)
        if song.name in self.failing:
            raise DownloadFailed("not found")
        path = folder / f"YT {song.label}.mp3"
        path.write_bytes(SILENT_MP3)
        return [path]

    def tag(self, song, file):
        self.tagged.append(file.name)
        if song.name in self.failing:
            raise TaggingFailed("not found")


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture(autouse=True)
def no_user_config(tmp_path_factory, monkeypatch):
    # Never read the developer's own spotarchive.toml.
    missing = tmp_path_factory.mktemp("config") / config.CONFIG_FILENAME
    monkeypatch.setattr(config, "default_path", lambda: missing)
