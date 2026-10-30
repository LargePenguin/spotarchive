"""Browser-based Spotify login.

Spotarchive never asks for your password. It opens a normal browser window
at Spotify's login page, with a profile of its own. Once Spotify redirects the
window to the web player, it reads from the running window:

* the spotify.com cookies (``sp_dc`` is the long-lived login cookie) and
* the browser's user agent,

then closes the window and has a headless browser load the web player with
those cookies to get the short-lived access token it fetches for itself.

The login window is started as a plain process, not driven by Playwright: an
automated browser (``navigator.webdriver``, a throwaway profile, a debugger
attached to the page) gets stuck in endless captchas on Spotify's login page.
While the user logs in, spotarchive only polls the window's DevTools HTTP
endpoint for the URLs of its tabs; it connects over DevTools once the window
has reached the web player.

The same headless step runs again whenever the token expires, so the web
player hands out a fresh one. Letting the real web player mint the
token avoids re-implementing Spotify's token endpoint, which changes often.
The same page load also yields the hashes of the web player's API queries
(read from its main script), which :mod:`spotarchive.spotify.client` needs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlparse

from spotarchive.session import SessionStore, SpotifySession

LOGIN_URL = "https://accounts.spotify.com/login?continue=https%3A%2F%2Fopen.spotify.com%2F"
WEB_PLAYER_URL = "https://open.spotify.com/"
LOGIN_COOKIE = "sp_dc"
TOKEN_PATHS = ("/api/token", "/get_access_token")
# The web player's main script, which defines the queries spotarchive uses, as
# e.g. ("fetchPlaylist","query","<sha256>",...).
WEB_PLAYER_SCRIPT_RE = re.compile(r"/web-player\.[0-9a-f]+\.js$")
QUERY_HASH_RE = re.compile(r'"(\w+)","query","([0-9a-f]{64})"')

LOGIN_TIMEOUT_S = 600
# How long a window on the web player may lack the login cookie before it
# counts as not logged in (yet).
LOGIN_COOKIE_WAIT_S = 3
CLOSE_TIMEOUT_S = 10
REFRESH_TIMEOUT_S = 60
# How long an anonymous token may stand alone before the cookies are
# considered logged out.
ANONYMOUS_GRACE_S = 5
POLL_MS = 500
# Where Chromium writes the DevTools port it picked for --remote-debugging-port=0.
DEBUG_PORT_FILE = "DevToolsActivePort"

# Browsers to try for headless token refreshes, in order: Playwright's
# bundled Chromium, then installed Chrome and Edge (always present on Windows).
BROWSER_CHANNELS = (None, "chrome", "msedge")
# Keep navigator.webdriver false in the headless browsers (not used for the
# login window: Chrome shows a "no longer supported" banner for the flag).
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
    def query_hashes(self) -> dict[str, str]: ...


@dataclass
class TokenInfo:
    access_token: str
    expires_at_ms: int
    anonymous: bool


def is_token_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname == "open.spotify.com" and parsed.path in TOKEN_PATHS


def is_web_player_script(url: str) -> bool:
    return bool(WEB_PLAYER_SCRIPT_RE.search(urlparse(url).path))


def extract_query_hashes(script: str) -> dict[str, str]:
    """Persisted-query hashes (operation -> hash) defined in the web player's JS."""
    return {m.group(1): m.group(2) for m in QUERY_HASH_RE.finditer(script)}


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
    """Collects the web player's token responses, and the query hashes from
    its script, from a browser context."""

    def __init__(self, context):
        self._pending = []
        self.query_hashes: dict[str, str] = {}
        context.on("response", self._on_response)

    def _on_response(self, response) -> None:
        # Only queue here: reading the body inside an event handler can
        # deadlock the sync API, so it is read from the main loop instead.
        if is_token_url(response.url) or is_web_player_script(response.url):
            self._pending.append(response)

    def poll(self) -> TokenInfo | None:
        """Return the newest token seen since the last poll, if any."""
        found = None
        while self._pending:
            response = self._pending.pop(0)
            try:
                if is_web_player_script(response.url):
                    self.query_hashes.update(extract_query_hashes(response.text()))
                    continue
                info = parse_token_response(response.json())
            except Exception:
                continue
            if info:
                found = info
        return found


def _web_player_token(context, timeout_s: int) -> tuple[TokenInfo | None, dict[str, str]]:
    """Load the web player; return the logged-in token it fetches and the
    query hashes its script defines.

    The token is None when only a logged-out (anonymous) token shows up, i.e.
    the context's cookies hold no valid login. The web player's script loads
    before it asks for a token, so its hashes are in by then.
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
            return info, watcher.query_hashes
        if anonymous_since and time.monotonic() - anonymous_since > ANONYMOUS_GRACE_S:
            return None, watcher.query_hashes
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
    """A plain (not automated) browser window on its own profile.

    The DevTools port (picked by the browser, see :func:`read_debug_port`)
    only lets spotarchive see which page the window is on; nothing attaches
    to the page while the user logs in.
    """
    return [
        str(executable),
        f"--user-data-dir={profile_dir}",
        "--remote-debugging-port=0",
        # No --disable-blink-features=AutomationControlled here: current Chrome
        # no longer supports it and shows a warning banner in the window.
        "--no-first-run",
        "--no-default-browser-check",
        # Keep the profile's cookies out of the system keyring (no prompt).
        "--password-store=basic",
        "--use-mock-keychain",
        "--new-window",
        url,
    ]


def read_debug_port(profile_dir: Path) -> int | None:
    """The DevTools port the browser on *profile_dir* listens on, once it's up."""
    try:
        first_line = (Path(profile_dir) / DEBUG_PORT_FILE).read_text().splitlines()[0]
        return int(first_line)
    except (OSError, IndexError, ValueError):
        return None


class LoginWindow(Protocol):
    def is_open(self) -> bool: ...
    def debug_port(self) -> int | None: ...
    def close(self) -> None: ...


class BrowserProcess:
    """The login window, started as a plain browser process."""

    def __init__(self, executable: Path, profile_dir: Path, url: str):
        self.profile_dir = profile_dir
        try:
            self._process = subprocess.Popen(
                login_browser_command(executable, profile_dir, url),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise BrowserUnavailable(f"Couldn't start {executable}: {exc}") from exc

    def is_open(self) -> bool:
        return self._process.poll() is None

    def debug_port(self) -> int | None:
        return read_debug_port(self.profile_dir)

    def close(self) -> None:
        """Wait briefly for the browser to exit (it's asked to quit over
        DevTools first, which saves its profile cleanly), then stop it."""
        try:
            self._process.wait(timeout=CLOSE_TIMEOUT_S)
            return
        except subprocess.TimeoutExpired:
            self._process.terminate()
        try:
            self._process.wait(timeout=CLOSE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self._process.kill()


def open_login_browser(executable: Path, profile_dir: Path, url: str) -> LoginWindow:
    return BrowserProcess(executable, profile_dir, url)


def is_web_player_url(url: str) -> bool:
    return urlparse(url).hostname == "open.spotify.com"


def _page_urls(port: int) -> list[str]:
    """URLs of the window's tabs, from the DevTools HTTP endpoint.

    This plain HTTP request doesn't attach a debugger to any page.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
            targets = json.load(response)
    except (OSError, ValueError):
        return []
    return [t.get("url", "") for t in targets if isinstance(t, dict) and t.get("type") == "page"]


def _read_live_login(playwright, port: int) -> tuple[list[dict], str] | None:
    """Read the login cookies and user agent from the running window.

    Returns None (and leaves the window alone) when it holds no login yet.
    On success the window is asked to quit.
    """
    browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    try:
        cdp = browser.new_browser_cdp_session()
        context = browser.contexts[0]
        cookies: list[dict] = []
        # Spotify sets sp_dc while logging in, but the redirect may win the race.
        deadline = time.monotonic() + LOGIN_COOKIE_WAIT_S
        while time.monotonic() < deadline:
            cookies = spotify_cookies(context.cookies())
            if has_login_cookie(cookies):
                break
            time.sleep(POLL_MS / 1000)
        else:
            return None
        user_agent = cdp.send("Browser.getVersion")["userAgent"]
        cdp.send("Browser.close")
        return cookies, user_agent
    finally:
        try:
            browser.close()  # only disconnects from a browser it didn't launch
        except Exception:
            pass


def _wait_for_login(playwright, window: LoginWindow, timeout_s: int) -> tuple[list[dict], str]:
    """Wait until the window lands on the web player with a login."""
    deadline = time.monotonic() + timeout_s
    was_on_player = False
    while time.monotonic() < deadline:
        if not window.is_open():
            raise LoginCancelled("The browser was closed before the login finished.")
        port = window.debug_port()
        on_player = bool(port) and any(is_web_player_url(u) for u in _page_urls(port))
        # Check once per arrival on the web player, so nothing attaches to the
        # browser while the user is still on the login page.
        if on_player and not was_on_player:
            found = _read_live_login(playwright, port)
            if found:
                return found
        was_on_player = on_player
        time.sleep(POLL_MS / 1000)
    raise LoginCancelled(f"The login didn't finish within {timeout_s // 60} minutes.")


def _prepare_profile(profile_dir: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        # The profile holds the login cookies.
        os.chmod(profile_dir, 0o700)
    # A port file left over from an earlier run would point at a dead browser.
    (profile_dir / DEBUG_PORT_FILE).unlink(missing_ok=True)


def interactive_login(
    profile_dir: Path,
    timeout_s: int = LOGIN_TIMEOUT_S,
    on_status: Callable[[str], None] = lambda msg: None,
    open_browser: Callable[[Path, Path, str], LoginWindow] = open_login_browser,
    configure_context: Callable[[object], None] | None = None,
) -> SpotifySession:
    """Let the user log in in a normal browser window, then capture the session.

    The login counts as done when the window is redirected to the web player
    with Spotify's login cookie set; the cookies and user agent are read from
    the running window, which is then closed. The window uses *profile_dir*
    as its profile, which is kept so Spotify recognises the device on later
    logins. *open_browser* and *configure_context* (passed on to
    :func:`refresh_session`, e.g. to route requests to fakes) exist for tests.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    profile_dir = Path(profile_dir).resolve()
    _prepare_profile(profile_dir)
    with sync_playwright() as p:
        executable = _login_browser(p)
        window = open_browser(executable, profile_dir, LOGIN_URL)
        on_status(
            "Log in to Spotify in the browser window that just opened; "
            "it closes by itself once you're logged in."
        )
        try:
            cookies, user_agent = _wait_for_login(p, window, timeout_s)
        except PlaywrightError as exc:
            raise AuthError(f"Reading the login failed: {str(exc).splitlines()[0]}") from exc
        finally:
            window.close()

    on_status("Logged in; getting an access token...")
    session = SpotifySession(cookies=cookies, user_agent=normalize_user_agent(user_agent))
    try:
        return refresh_session(session, configure_context=configure_context)
    except LoginRequired:
        raise AuthError("Spotify didn't accept the new login; try logging in again.") from None


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
            info, query_hashes = _web_player_token(context, timeout_s)
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
                # Keep the old hashes if the script wasn't seen this time.
                query_hashes=query_hashes or session.query_hashes,
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

    def query_hashes(self) -> dict[str, str]:
        session = self.store.spotify
        return dict(session.query_hashes) if session else {}
