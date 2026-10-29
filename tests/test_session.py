import os
import stat

import pytest

from spotarchive.session import (
    SESSION_FILENAME,
    PlaylistState,
    SessionError,
    SessionStore,
    SpotifySession,
)


def fake_session(**kwargs):
    return SpotifySession(
        cookies=[{"name": "sp_dc", "value": "dummy-cookie", "domain": ".spotify.com", "path": "/"}],
        user_agent="dummy-agent",
        **kwargs,
    )


def test_missing_file_gives_empty_store(tmp_path):
    store = SessionStore.load(tmp_path)
    assert store.spotify is None
    assert store.playlists == {}


def test_roundtrip(tmp_path):
    store = SessionStore.load(tmp_path / "data")
    store.spotify = fake_session(access_token="dummy-token", expires_at_ms=123, display_name="Fake User")
    store.playlists = {"p1": PlaylistState("Road Trip", True, "Road Trip"), "p2": PlaylistState("Other")}
    store.save()

    loaded = SessionStore.load(tmp_path / "data")
    assert loaded.spotify == store.spotify
    assert loaded.playlists == store.playlists
    assert loaded.selected_ids() == ["p1"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_file_is_private(tmp_path):
    store = SessionStore.load(tmp_path)
    store.spotify = fake_session()
    store.save()
    mode = stat.S_IMODE((tmp_path / SESSION_FILENAME).stat().st_mode)
    assert mode == 0o600


def test_corrupt_file_raises_session_error(tmp_path):
    (tmp_path / SESSION_FILENAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(SessionError):
        SessionStore.load(tmp_path)


def test_token_freshness():
    session = fake_session(access_token="dummy-token", expires_at_ms=1_000_000)
    assert session.token_is_fresh(at_ms=0)
    assert not session.token_is_fresh(at_ms=1_000_000 - 30_000)
    assert not fake_session(expires_at_ms=1_000_000).token_is_fresh(at_ms=0)
