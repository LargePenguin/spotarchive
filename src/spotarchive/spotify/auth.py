"""Browser-based Spotify login.

Spotarchive never asks for your password. It opens a real browser at
Spotify's login page and, once you've signed in, keeps:

* the spotify.com cookies (``sp_dc`` is the long-lived login cookie),
* the browser's user agent, and
* the short-lived access token the Spotify web player fetches for itself.

When the token expires, the cookies are replayed in a headless browser so
the web player hands out a fresh one. Letting the real web player mint the
token avoids re-implementing Spotify's token endpoint, which changes often.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.parse import urlparse

from spotarchive.session import SessionStore, SpotifySession

LOGIN_URL = "https://accounts.spotify.com/login?continue=https%3A%2F%2Fopen.spotify.com%2F"
WEB_PLAYER_URL = "https://open.spotify.com/"
LOGIN_COOKIE = "sp_dc"
TOKEN_PATHS = ("/api/token", "/get_access_token")

LOGIN_TIMEOUT_S = 600
REFRESH_TIMEOUT_S = 60
# After the login cookie appears, how long to wait for the web player to
# fetch a token on its own before navigating to it ourselves.
REDIRECT_GRACE_S = 10
# How long an anonymous token may stand alone during a refresh before the
# saved login is considered expired.
ANONYMOUS_GRACE_S = 5
POLL_MS = 500

# Browsers to try, in order: Playwright's bundled Chromium, then installed
# Chrome and Edge (always present on Windows).
BROWSER_CHANNELS = (None, "chrome", "msedge")


class AuthError(Exception):
    """Base class for login problems."""


class LoginRequired(AuthError):
    """There is no usable Spotify login; run the login helper."""


class LoginCancelled(AuthError):
    """The user closed the browser before finishing the login."""


class BrowserUnavailable(AuthError):
    """No browser Playwright can drive is installed."""


class TokenProvider(Protocol):
    def get_token(self, force_refresh: bool = False) -> str: ...


@dataclass
class TokenInfo:
    access_token: str
    expires_at_ms: int
    anonymous: bool


def is_token_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname == "open.spotify.com" and parsed.path in TOKEN_PATHS


def parse_token_response(data: object) -> TokenInfo | None:
    if not isinstance(data, dict) or not data.get("accessToken"):
        return None
    return TokenInfo(
        access_token=data["accessToken"],
        expires_at_ms=int(data.get("accessTokenExpirationTimestampMs") or 0),
        anonymous=bool(data.get("isAnonymous")),
    )


def spotify_cookies(cookies: list[dict]) -> list[dict]:
    """Keep only spotify.com cookies; nothing else from the browser is stored."""
    return [
        c for c in cookies
        if c.get("domain", "").lstrip(".") == "spotify.com"
        or c.get("domain", "").endswith(".spotify.com")
    ]


def has_login_cookie(cookies: list[dict]) -> bool:
    return any(c.get("name") == LOGIN_COOKIE and c.get("value") for c in cookies)


def normalize_user_agent(user_agent: str) -> str:
    # Headless Chromium advertises itself; look like the browser the user
    # logged in with instead.
    return user_agent.replace("HeadlessChrome", "Chrome")


def _launch(playwright, headless: bool):
    from playwright.sync_api import Error as PlaywrightError

    errors = []
    for channel in BROWSER_CHANNELS:
        try:
            return playwright.chromium.launch(headless=headless, channel=channel)
        except PlaywrightError as exc:
            errors.append(f"{channel or 'bundled chromium'}: {str(exc).splitlines()[0]}")
    raise BrowserUnavailable(
        "Couldn't start a browser. Install Playwright's Chromium with "
        "`python -m playwright install chromium`, or install Google Chrome "
        "or Microsoft Edge.\n  " + "\n  ".join(errors)
    )


class _TokenWatcher:
    """Collects the web player's token responses from a browser context."""

    def __init__(self, context):
        self._pending = []
        context.on("response", self._on_response)

    def _on_response(self, response) -> None:
        # Only queue here: reading the body inside an event handler can
        # deadlock the sync API, so it is read from the main loop instead.
        if is_token_url(response.url):
            self._pending.append(response)

    def poll(self) -> TokenInfo | None:
        """Return the newest token seen since the last poll, if any."""
        found = None
        while self._pending:
            response = self._pending.pop(0)
            try:
                info = parse_token_response(response.json())
            except Exception:
                continue
            if info:
                found = info
        return found


def interactive_login(
    timeout_s: int = LOGIN_TIMEOUT_S,
    on_status: Callable[[str], None] = lambda msg: None,
    headless: bool = False,
    configure_context: Callable[[object], None] | None = None,
) -> SpotifySession:
    """Open a visible browser at Spotify's login page and capture the session.

    *headless* and *configure_context* (called with the new browser context,
    e.g. to route requests to fakes) exist for tests.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p, headless=headless)
        try:
            context = browser.new_context()
            if configure_context:
                configure_context(context)
            watcher = _TokenWatcher(context)
            page = context.new_page()
            page.goto(LOGIN_URL)
            on_status("Log in to Spotify in the browser window that just opened.")

            deadline = time.monotonic() + timeout_s
            cookie_seen_at = None
            redirected = False
            while time.monotonic() < deadline:
                info = watcher.poll()
                cookies = spotify_cookies(context.cookies())
                if info and not info.anonymous and has_login_cookie(cookies):
                    user_agent = page.evaluate("navigator.userAgent")
                    return SpotifySession(
                        cookies=cookies,
                        user_agent=normalize_user_agent(user_agent),
                        access_token=info.access_token,
                        expires_at_ms=info.expires_at_ms,
                    )
                if has_login_cookie(cookies):
                    cookie_seen_at = cookie_seen_at or time.monotonic()
                    if not redirected and time.monotonic() - cookie_seen_at > REDIRECT_GRACE_S:
                        # Logged in, but the login flow didn't end in the web
                        # player; open it so it fetches a token.
                        page.goto(WEB_PLAYER_URL)
                        redirected = True
                if not context.pages:
                    raise LoginCancelled("The browser was closed before the login finished.")
                page = context.pages[-1]
                try:
                    page.wait_for_timeout(POLL_MS)
                except PlaywrightError:
                    if not context.pages:
                        raise LoginCancelled("The browser was closed before the login finished.")
            raise LoginCancelled(f"No login within {timeout_s // 60} minutes.")
        except PlaywrightError as exc:
            raise LoginCancelled(f"The browser stopped unexpectedly: {str(exc).splitlines()[0]}") from exc
        finally:
            try:
                browser.close()
            except PlaywrightError:
                pass


def refresh_session(
    session: SpotifySession,
    timeout_s: int = REFRESH_TIMEOUT_S,
    configure_context: Callable[[object], None] | None = None,
) -> SpotifySession:
    """Get a fresh token by loading the web player headlessly with the saved cookies.

    *configure_context* is called with the new browser context (for tests).
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    if not has_login_cookie(session.cookies):
        raise LoginRequired("No Spotify login saved.")
    with sync_playwright() as p:
        browser = _launch(p, headless=True)
        try:
            context = browser.new_context(user_agent=session.user_agent or None)
            context.add_cookies(session.cookies)
            if configure_context:
                configure_context(context)
            watcher = _TokenWatcher(context)
            page = context.new_page()
            page.goto(WEB_PLAYER_URL, wait_until="domcontentloaded")

            deadline = time.monotonic() + timeout_s
            anonymous_since = None
            while time.monotonic() < deadline:
                info = watcher.poll()
                if info and info.anonymous:
                    anonymous_since = anonymous_since or time.monotonic()
                elif info:
                    cookies = spotify_cookies(context.cookies())
                    return SpotifySession(
                        # Spotify may rotate cookies; keep the newest ones.
                        cookies=cookies if has_login_cookie(cookies) else session.cookies,
                        user_agent=session.user_agent,
                        access_token=info.access_token,
                        expires_at_ms=info.expires_at_ms,
                        user_id=session.user_id,
                        display_name=session.display_name,
                    )
                if anonymous_since and time.monotonic() - anonymous_since > ANONYMOUS_GRACE_S:
                    # Only a logged-out token showed up: the cookies are dead.
                    raise LoginRequired("The saved Spotify login has expired.")
                page.wait_for_timeout(POLL_MS)
            raise AuthError("Timed out waiting for Spotify to issue a token.")
        except PlaywrightError as exc:
            raise AuthError(f"Token refresh failed: {str(exc).splitlines()[0]}") from exc
        finally:
            try:
                browser.close()
            except PlaywrightError:
                pass


class SessionTokenProvider:
    """Hands out the cached token, refreshing (and saving) it when it expires."""

    def __init__(
        self,
        store: SessionStore,
        refresh: Callable[[SpotifySession], SpotifySession] = refresh_session,
    ):
        self.store = store
        self._refresh = refresh

    def get_token(self, force_refresh: bool = False) -> str:
        session = self.store.spotify
        if session is None:
            raise LoginRequired("Not logged in to Spotify.")
        if force_refresh or not session.token_is_fresh():
            self.store.spotify = session = self._refresh(session)
            self.store.save()
        return session.access_token
