"""Drive the real browser code against a fake Spotify.

Every request the browser makes is answered by the fakes below (anything
else is aborted), so nothing reaches Spotify. Skipped when no browser
Playwright can drive is installed.
"""

import json
import threading
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright")

from spotarchive.session import SpotifySession  # noqa: E402
from spotarchive.spotify.auth import (  # noqa: E402
    BrowserUnavailable,
    LoginCancelled,
    LoginRequired,
    _launch,
    interactive_login,
    read_debug_port,
    refresh_session,
)

GOOD_COOKIE = "dummy-cookie"

# A persistent cookie (max-age), like the real sp_dc, so it survives the
# browser being closed.
LOGIN_PAGE = f"""<html><body>Fake login<script>
document.cookie = "sp_dc={GOOD_COOKIE}; domain=.spotify.com; path=/; secure; max-age=3600";
</script></body></html>"""

FAKE_HASH = "a" * 64
# Like the real web player, the main script defines the queries and then asks
# for a token.
WEB_PLAYER_SCRIPT = f"""
function Query() {{}}
var fetchPlaylistQuery = new Query("fetchPlaylist","query","{FAKE_HASH}",null);
fetch("/api/token?reason=init&productType=web-player");
"""
WEB_PLAYER = """<html><body>Fake web player
<script src="https://open.spotifycdn.com/cdn/build/web-player/web-player.0123abcd.js"></script>
</body></html>"""


def fake_spotify(context):
    def handle(route):
        request = route.request
        url = urlparse(request.url)
        if url.hostname == "accounts.spotify.com":
            route.fulfill(status=200, content_type="text/html", body=LOGIN_PAGE)
        elif url.hostname == "open.spotify.com" and url.path == "/api/token":
            logged_in = f"sp_dc={GOOD_COOKIE}" in request.all_headers().get("cookie", "")
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "accessToken": "dummy-token" if logged_in else "anonymous-token",
                "accessTokenExpirationTimestampMs": 4_000_000_000_000,
                "isAnonymous": not logged_in,
            }))
        elif url.hostname == "open.spotifycdn.com" and url.path.endswith("/web-player.0123abcd.js"):
            route.fulfill(status=200, content_type="application/javascript", body=WEB_PLAYER_SCRIPT)
        elif url.hostname == "open.spotify.com":
            route.fulfill(status=200, content_type="text/html", body=WEB_PLAYER)
        else:
            route.abort()

    context.route("**/*", handle)


@pytest.fixture(scope="module", autouse=True)
def require_browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            _launch(p, headless=True).close()
        except BrowserUnavailable:
            pytest.skip("no browser installed for Playwright")


def cookie(value):
    return {"name": "sp_dc", "value": value, "domain": ".spotify.com", "path": "/", "secure": True}


class FakeWindow:
    """Stands in for the login window: a headless browser with a DevTools
    port, where a fake user logs in or not (*reach_player* without *log_in*
    opens the web player logged out), then closes the window unless
    spotarchive closes it first."""

    def __init__(self, executable, profile_dir, url, log_in, reach_player):
        self.profile_dir = profile_dir
        self.errors = []
        # The sync API can't nest in one thread, and interactive_login already
        # runs Playwright in this one.
        self.thread = threading.Thread(
            target=_record, args=(lambda: self._run(executable, url, log_in, reach_player), self.errors),
        )
        self.thread.start()

    def _run(self, executable, url, log_in, reach_player):
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                str(self.profile_dir), executable_path=str(executable), headless=True,
                args=["--remote-debugging-port=0"],
            )
            closed = threading.Event()
            context.on("close", lambda _: closed.set())
            fake_spotify(context)
            page = context.pages[0] if context.pages else context.new_page()
            if log_in:
                # The fake login page sets the login cookie as it loads.
                page.goto(url)
                page.wait_for_function("document.cookie.includes('sp_dc=')")
            if log_in or reach_player:
                page.goto("https://open.spotify.com/")
            # Linger like a user would (longer than spotarchive waits for the
            # login cookie); spotarchive closes the window on success.
            for _ in range(30 if log_in else 10):
                if closed.is_set():
                    return
                try:
                    page.wait_for_timeout(500)
                except PlaywrightError:
                    return
            context.close()

    def is_open(self):
        return self.thread.is_alive()

    def debug_port(self):
        return read_debug_port(self.profile_dir)

    def close(self):
        self.thread.join(30)
        if self.errors:
            raise self.errors[0]


def fake_user(log_in, reach_player=False):
    def open_browser(executable, profile_dir, url):
        window = FakeWindow(executable, profile_dir, url, log_in, reach_player)
        windows.append(window)
        return window

    windows = []
    open_browser.windows = windows
    return open_browser


def _record(fn, errors):
    try:
        fn()
    except Exception as exc:
        errors.append(exc)


def test_interactive_login_captures_session_and_closes_window(tmp_path):
    open_browser = fake_user(log_in=True)
    session = interactive_login(
        tmp_path / "profile", timeout_s=30,
        open_browser=open_browser, configure_context=fake_spotify,
    )
    assert session.access_token == "dummy-token"
    assert session.expires_at_ms == 4_000_000_000_000
    assert any(c["name"] == "sp_dc" and c["value"] == GOOD_COOKIE for c in session.cookies)
    assert session.user_agent and "HeadlessChrome" not in session.user_agent
    # Closed by spotarchive right after the redirect, not by the user's timeout.
    assert not open_browser.windows[0].is_open()


def test_interactive_login_closed_without_logging_in(tmp_path):
    with pytest.raises(LoginCancelled):
        interactive_login(
            tmp_path / "profile", timeout_s=30,
            open_browser=fake_user(log_in=False), configure_context=fake_spotify,
        )


def test_interactive_login_ignores_logged_out_web_player(tmp_path):
    with pytest.raises(LoginCancelled):
        interactive_login(
            tmp_path / "profile", timeout_s=30,
            open_browser=fake_user(log_in=False, reach_player=True), configure_context=fake_spotify,
        )


def test_refresh_gets_new_token_from_cookies():
    old = SpotifySession(cookies=[cookie(GOOD_COOKIE)], user_agent="Mozilla/5.0 dummy", display_name="Fake")
    new = refresh_session(old, timeout_s=30, configure_context=fake_spotify)
    assert new.access_token == "dummy-token"
    assert new.display_name == "Fake"
    assert new.query_hashes == {"fetchPlaylist": FAKE_HASH}


def test_refresh_with_dead_cookie_requires_login():
    old = SpotifySession(cookies=[cookie("expired-cookie")], user_agent="Mozilla/5.0 dummy")
    with pytest.raises(LoginRequired):
        refresh_session(old, timeout_s=30, configure_context=fake_spotify)


def test_headless_browser_hides_automation():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p, headless=True)
        try:
            page = browser.new_page()
            assert page.evaluate("navigator.webdriver") is False
        finally:
            browser.close()
