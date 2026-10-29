"""Fakes for the Spotify layer. Tests never talk to Spotify."""

from __future__ import annotations

from urllib.parse import urlencode

import pytest

from spotarchive.spotify.client import API_URL


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
    """Serves queued responses per URL (path relative to the API, plus query)."""

    def __init__(self):
        self.routes: dict[str, list[FakeResponse]] = {}
        self.calls: list[tuple[str, dict]] = []

    def add(self, url, *responses):
        self.routes.setdefault(url, []).extend(responses)

    def get(self, url, params=None, headers=None, timeout=None):
        key = url.removeprefix(API_URL)
        if params:
            key += "?" + urlencode(params)
        self.calls.append((key, headers or {}))
        queue = self.routes.get(key)
        if not queue:
            return FakeResponse(404, {"error": {"status": 404, "message": "Not found"}})
        # The last response repeats so routes can be hit several times.
        return queue.pop(0) if len(queue) > 1 else queue[0]


class FakeTokens:
    def __init__(self):
        self.refreshes = 0

    def get_token(self, force_refresh=False):
        if force_refresh:
            self.refreshes += 1
        return f"dummy-token-{self.refreshes}"


def track_json(name, artists=("Fake Artist",), album="Fake Album", release="2001-02-03", isrc="XX0000000001"):
    return {
        "type": "track",
        "name": name,
        "artists": [{"name": a} for a in artists],
        "album": {"name": album, "release_date": release},
        "duration_ms": 180000,
        "external_ids": {"isrc": isrc},
        "external_urls": {"spotify": f"https://open.spotify.com/track/{name.replace(' ', '')}"},
    }


def page(items, total=None, next_url=None):
    return FakeResponse(200, {"items": items, "total": len(items) if total is None else total, "next": next_url})


@pytest.fixture
def http():
    return FakeHttp()


@pytest.fixture
def tokens():
    return FakeTokens()
