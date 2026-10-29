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
    refresh_session,
)

GOOD_COOKIE = "dummy-cookie"

# A persistent cookie (max-age), like the real sp_dc, so it survives the
# browser being closed.
LOGIN_PAGE = f"""<html><body>Fake login<script>
document.cookie = "sp_dc={GOOD_COOKIE}; domain=.spotify.com; path=/; secure; max-age=3600";
</script></body></html>"""

WEB_PLAYER = """<html><body>Fake web player<script>
fetch("/api/token?reason=init&productType=web-player");
</script></body></html>"""


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


def fake_user(log_in):
    """Stands in for the login window: a user who logs in (or not) and closes it."""
    def open_browser(executable, profile_dir, url, timeout_s):
        def run():
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    str(profile_dir), executable_path=str(executable), headless=True,
                )
                fake_spotify(context)
                if log_in:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(url)
                    page.wait_for_function("document.cookie.includes('sp_dc=')")
                context.close()

        # The sync API can't nest in one thread, and interactive_login already
        # runs Playwright in this one.
        errors = []
        thread = threading.Thread(target=lambda: _record(run, errors))
        thread.start()
        thread.join(timeout_s)
        if errors:
            raise errors[0]

    return open_browser


def _record(fn, errors):
    try:
        fn()
    except Exception as exc:
        errors.append(exc)


def test_interactive_login_captures_session(tmp_path):
    session = interactive_login(
        tmp_path / "profile", timeout_s=30,
        open_browser=fake_user(log_in=True), configure_context=fake_spotify,
    )
    assert session.access_token == "dummy-token"
    assert session.expires_at_ms == 4_000_000_000_000
    assert any(c["name"] == "sp_dc" and c["value"] == GOOD_COOKIE for c in session.cookies)
    assert session.user_agent and "HeadlessChrome" not in session.user_agent


def test_interactive_login_closed_without_logging_in(tmp_path):
    with pytest.raises(LoginCancelled):
        interactive_login(
            tmp_path / "profile", timeout_s=30,
            open_browser=fake_user(log_in=False), configure_context=fake_spotify,
        )


def test_refresh_gets_new_token_from_cookies():
    old = SpotifySession(cookies=[cookie(GOOD_COOKIE)], user_agent="Mozilla/5.0 dummy", display_name="Fake")
    new = refresh_session(old, timeout_s=30, configure_context=fake_spotify)
    assert new.access_token == "dummy-token"
    assert new.display_name == "Fake"


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
