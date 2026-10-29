"""Browser-based Spotify login.

Spotarchive never asks for your password. It opens a normal browser window
at Spotify's login page, with a profile of its own, and once you've signed in
and closed the window it reads from that profile:

* the spotify.com cookies (``sp_dc`` is the long-lived login cookie),
* the browser's user agent, and
* the short-lived access token the Spotify web player fetches for itself.

The login window is started as a plain process, not driven by Playwright: an
automated browser (``navigator.webdriver``, a throwaway profile, a DevTools
connection) gets stuck in endless captchas on Spotify's login page.
Playwright only opens the profile afterwards, headlessly, to read the result.

When the token expires, the cookies are replayed in a headless browser so
the web player hands out a fresh one. Letting the real web player mint the
token avoids re-implementing Spotify's token endpoint, which changes often.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlparse

from spotarchive.session import SessionStore, SpotifySession

LOGIN_URL = "https://accounts.spotify.com/login?continue=https%3A%2F%2Fopen.spotify.com%2F"
WEB_PLAYER_URL = "https://open.spotify.com/"
LOGIN_COOKIE = "sp_dc"
TOKEN_PATHS = ("/api/token", "/get_access_token")

LOGIN_TIMEOUT_S = 600
REFRESH_TIMEOUT_S = 60
# How long an anonymous token may stand alone before the cookies are
# considered logged out.
ANONYMOUS_GRACE_S = 5
POLL_MS = 500

# Browsers to try for headless token refreshes, in order: Playwright's
# bundled Chromium, then installed Chrome and Edge (always present on Windows).
BROWSER_CHANNELS = (None, "chrome", "msedge")
# Keep navigator.webdriver false in the headless browsers too.
STEALTH_ARGS = ["--disable-blink-features=AutomationControlled"]
STEALTH_IGNORED_DEFAULTS = ["--enable-automation"]

# Installed browsers preferred for the login window, before Playwright's
# bundled Chromium.
LINUX_BROWSERS = ("google-chrome", "google-chrome-stable", "microsoft-edge", "microsoft-edge-stable")
WINDOWS_BROWSERS = ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe")
WINDOWS_ROOTS = ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")


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
            return playwright.chromium.launch(
                headless=headless,
                channel=channel,
                args=STEALTH_ARGS,
                ignore_default_args=STEALTH_IGNORED_DEFAULTS,
            )
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


def _web_player_token(context, timeout_s: int) -> TokenInfo | None:
    """Load the web player and return the logged-in token it fetches.

    Returns None when only a logged-out (anonymous) token shows up, i.e. the
    context's cookies hold no valid login.
    """
    watcher = _TokenWatcher(context)
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(WEB_PLAYER_URL, wait_until="domcontentloaded")

    deadline = time.monotonic() + timeout_s
    anonymous_since = None
    while time.monotonic() < deadline:
        info = watcher.poll()
        if info and info.anonymous:
            anonymous_since = anonymous_since or time.monotonic()
        elif info:
            return info
        if anonymous_since and time.monotonic() - anonymous_since > ANONYMOUS_GRACE_S:
            return None
        page.wait_for_timeout(POLL_MS)
    raise AuthError("Timed out waiting for Spotify to issue a token.")


def installed_browsers() -> list[Path]:
    """Chrome and Edge executables installed on this computer."""
    if os.name == "nt":
        roots = [os.environ.get(var) for var in WINDOWS_ROOTS]
        found = [Path(root) / rel for rel in WINDOWS_BROWSERS for root in roots if root]
    else:
        found = [Path(path) for name in LINUX_BROWSERS if (path := shutil.which(name))]
    return [path for path in found if path.is_file()]


def _login_browser(playwright) -> Path:
    candidates = installed_browsers()
    bundled = Path(playwright.chromium.executable_path)
    if bundled.is_file():
        candidates.append(bundled)
    if not candidates:
        raise BrowserUnavailable(
            "Couldn't find a browser. Install Google Chrome or Microsoft Edge, "
            "or Playwright's Chromium with `python -m playwright install chromium`."
        )
    return candidates[0]


def login_browser_command(executable: Path, profile_dir: Path, url: str) -> list[str]:
    """A plain (not automated) browser window on its own profile."""
    return [
        str(executable),
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        # Encrypt cookies the way Playwright reads them back (Linux/macOS).
        "--password-store=basic",
        "--use-mock-keychain",
        "--new-window",
        url,
    ]


def open_login_browser(executable: Path, profile_dir: Path, url: str, timeout_s: int) -> None:
    """Open the login window and wait until the user closes it."""
    try:
        process = subprocess.Popen(
            login_browser_command(executable, profile_dir, url),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise BrowserUnavailable(f"Couldn't start {executable}: {exc}") from exc
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise LoginCancelled(f"The browser was still open after {timeout_s // 60} minutes.") from None
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def _prepare_profile(profile_dir: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        # The profile holds the login cookies.
        os.chmod(profile_dir, 0o700)


def interactive_login(
    profile_dir: Path,
    timeout_s: int = LOGIN_TIMEOUT_S,
    on_status: Callable[[str], None] = lambda msg: None,
    open_browser: Callable[[Path, Path, str, int], None] = open_login_browser,
    configure_context: Callable[[object], None] | None = None,
) -> SpotifySession:
    """Let the user log in in a normal browser window, then capture the session.

    The window uses *profile_dir* as its profile, which is kept so Spotify
    recognises the device on later logins. *open_browser* (which returns once
    the window is closed) and *configure_context* (called with the headless
    context that reads the profile, e.g. to route requests to fakes) exist
    for tests.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    profile_dir = Path(profile_dir).resolve()
    _prepare_profile(profile_dir)
    with sync_playwright() as p:
        executable = _login_browser(p)
        on_status(
            "Log in to Spotify in the browser window that just opened, "
            "then close the window to continue."
        )
        open_browser(executable, profile_dir, LOGIN_URL, timeout_s)

        on_status("Reading the login from the browser profile...")
        try:
            context = p.chromium.launch_persistent_context(
                str(profile_dir),
                executable_path=str(executable),
                headless=True,
                args=STEALTH_ARGS,
                ignore_default_args=STEALTH_IGNORED_DEFAULTS,
            )
        except PlaywrightError as exc:
            raise AuthError(
                "Couldn't open the login browser profile; make sure the login "
                f"window is closed: {str(exc).splitlines()[0]}"
            ) from exc
        try:
            if configure_context:
                configure_context(context)
            if not has_login_cookie(spotify_cookies(context.cookies())):
                raise LoginCancelled("The browser was closed before the login finished.")
            info = _web_player_token(context, REFRESH_TIMEOUT_S)
            if info is None:
                raise LoginCancelled("The browser was closed before the login finished.")
            user_agent = context.pages[0].evaluate("navigator.userAgent")
            return SpotifySession(
                cookies=spotify_cookies(context.cookies()),
                user_agent=normalize_user_agent(user_agent),
                access_token=info.access_token,
                expires_at_ms=info.expires_at_ms,
            )
        except PlaywrightError as exc:
            raise AuthError(f"Reading the login failed: {str(exc).splitlines()[0]}") from exc
        finally:
            try:
                context.close()
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
            info = _web_player_token(context, timeout_s)
            if info is None:
                # Only a logged-out token showed up: the cookies are dead.
                raise LoginRequired("The saved Spotify login has expired.")
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
