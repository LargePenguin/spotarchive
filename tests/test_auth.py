from pathlib import Path

import pytest

from spotarchive.session import SessionStore, SpotifySession
from spotarchive.spotify import auth
from spotarchive.spotify.auth import (
    LOGIN_URL,
    LoginRequired,
    SessionTokenProvider,
    has_login_cookie,
    installed_browsers,
    is_token_url,
    login_browser_command,
    normalize_user_agent,
    parse_token_response,
    spotify_cookies,
)


@pytest.mark.parametrize("url, expected", [
    ("https://open.spotify.com/api/token?reason=init&productType=web-player", True),
    ("https://open.spotify.com/get_access_token?reason=transport", True),
    ("https://open.spotify.com/playlist/fake", False),
    ("https://evil.example.com/api/token", False),
    ("https://api.spotify.com/api/token", False),
])
def test_is_token_url(url, expected):
    assert is_token_url(url) is expected


def test_parse_token_response():
    info = parse_token_response({
        "accessToken": "dummy-token",
        "accessTokenExpirationTimestampMs": 1234,
        "isAnonymous": False,
    })
    assert (info.access_token, info.expires_at_ms, info.anonymous) == ("dummy-token", 1234, False)
    assert parse_token_response({"isAnonymous": True}) is None
    assert parse_token_response("not a dict") is None


def test_spotify_cookies_keeps_only_spotify_domains():
    cookies = [
        {"name": "sp_dc", "value": "dummy", "domain": ".spotify.com"},
        {"name": "x", "value": "dummy", "domain": "open.spotify.com"},
        {"name": "y", "value": "dummy", "domain": ".example.com"},
        {"name": "z", "value": "dummy", "domain": "notspotify.com"},
    ]
    assert [c["name"] for c in spotify_cookies(cookies)] == ["sp_dc", "x"]


def test_has_login_cookie():
    assert has_login_cookie([{"name": "sp_dc", "value": "dummy"}])
    assert not has_login_cookie([{"name": "sp_dc", "value": ""}])
    assert not has_login_cookie([{"name": "sp_t", "value": "dummy"}])


def test_normalize_user_agent():
    assert normalize_user_agent("Mozilla/5.0 HeadlessChrome/120.0") == "Mozilla/5.0 Chrome/120.0"


def _store(tmp_path, **kwargs):
    store = SessionStore.load(tmp_path)
    store.spotify = SpotifySession(cookies=[], user_agent="dummy-agent", **kwargs)
    return store


def test_provider_uses_fresh_cached_token(tmp_path):
    store = _store(tmp_path, access_token="cached-token", expires_at_ms=2**62)
    provider = SessionTokenProvider(store, refresh=lambda s: pytest.fail("should not refresh"))
    assert provider.get_token() == "cached-token"


def test_provider_refreshes_expired_token_and_saves(tmp_path):
    store = _store(tmp_path, access_token="old-token", expires_at_ms=0)

    def refresh(session):
        return SpotifySession(cookies=[], user_agent=session.user_agent, access_token="new-token", expires_at_ms=2**62)

    assert SessionTokenProvider(store, refresh=refresh).get_token() == "new-token"
    assert SessionStore.load(tmp_path).spotify.access_token == "new-token"


def test_provider_force_refresh(tmp_path):
    store = _store(tmp_path, access_token="cached-token", expires_at_ms=2**62)
    calls = []

    def refresh(session):
        calls.append(1)
        return SpotifySession(cookies=[], user_agent="dummy-agent", access_token="new-token", expires_at_ms=2**62)

    assert SessionTokenProvider(store, refresh=refresh).get_token(force_refresh=True) == "new-token"
    assert calls == [1]


def test_provider_without_login(tmp_path):
    with pytest.raises(LoginRequired):
        SessionTokenProvider(SessionStore.load(tmp_path)).get_token()


def test_login_browser_is_not_automated(tmp_path):
    cmd = login_browser_command(Path("/fake/chrome"), tmp_path / "profile", LOGIN_URL)
    assert cmd[0] == str(Path("/fake/chrome"))
    assert f"--user-data-dir={tmp_path / 'profile'}" in cmd
    assert cmd[-1] == LOGIN_URL
    # Flags that make Spotify's captcha treat the window as a bot.
    assert not any(arg.startswith(("--enable-automation", "--remote-debugging", "--headless")) for arg in cmd)


def test_installed_browsers_on_linux(tmp_path, monkeypatch):
    chrome = tmp_path / "google-chrome"
    chrome.write_text("")
    monkeypatch.setattr(auth.os, "name", "posix")
    monkeypatch.setattr(auth.shutil, "which", lambda name: str(chrome) if name == "google-chrome" else None)
    assert installed_browsers() == [chrome]
