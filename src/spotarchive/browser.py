"""The browser window the logins happen in, shared by every site.

The login window is started as a plain process, not driven by Playwright: an
automated browser (``navigator.webdriver``, a throwaway profile, a debugger
attached to the page) gets stuck in endless captchas on login pages. While
the user logs in, spotarchive only polls the window's DevTools HTTP endpoint
for the URLs of its tabs; it connects over DevTools once a tab has reached
the site's logged-in page, reads the cookies and user agent, and asks the
window to quit.

Each site (see :mod:`spotarchive.spotify.auth` and
:mod:`spotarchive.youtube.auth`) supplies its login URL, which pages count
as "logged in", which cookies to keep and which of them prove a login.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

LOGIN_TIMEOUT_S = 600
# How long a window on the logged-in page may lack the login cookie before it
# counts as not logged in (yet).
LOGIN_COOKIE_WAIT_S = 3
CLOSE_TIMEOUT_S = 10
POLL_MS = 500
# Where Chromium writes the DevTools port it picked for --remote-debugging-port=0.
DEBUG_PORT_FILE = "DevToolsActivePort"

# Installed browsers preferred for the login window, before Playwright's
# bundled Chromium.
LINUX_BROWSERS = ("google-chrome", "google-chrome-stable", "microsoft-edge", "microsoft-edge-stable")
WINDOWS_BROWSERS = ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe")
WINDOWS_ROOTS = ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")


class AuthError(Exception):
    """Base class for login problems."""


class LoginCancelled(AuthError):
    """The user closed the browser before finishing the login."""


class BrowserUnavailable(AuthError):
    """No browser Playwright can drive is installed."""


@dataclass(frozen=True)
class LoginSite:
    """What the login window needs to know about a site."""

    # Shown in messages, e.g. "Spotify".
    name: str
    login_url: str
    # True for a tab URL where a finished login lands.
    is_logged_in_url: Callable[[str], bool]
    # The cookies worth keeping; nothing else from the browser is stored.
    keep_cookies: Callable[[list[dict]], list[dict]]
    # True when the kept cookies hold a login.
    has_login: Callable[[list[dict]], bool]


def installed_browsers() -> list[Path]:
    """Chrome and Edge executables installed on this computer."""
    if os.name == "nt":
        roots = [os.environ.get(var) for var in WINDOWS_ROOTS]
        found = [Path(root) / rel for rel in WINDOWS_BROWSERS for root in roots if root]
    else:
        found = [Path(path) for name in LINUX_BROWSERS if (path := shutil.which(name))]
    return [path for path in found if path.is_file()]


def login_browser(playwright) -> Path:
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


def page_urls(port: int) -> list[str]:
    """URLs of the window's tabs, from the DevTools HTTP endpoint.

    This plain HTTP request doesn't attach a debugger to any page.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
            targets = json.load(response)
    except (OSError, ValueError):
        return []
    return [t.get("url", "") for t in targets if isinstance(t, dict) and t.get("type") == "page"]


def _read_live_login(playwright, port: int, site: LoginSite) -> tuple[list[dict], str] | None:
    """Read the login cookies and user agent from the running window.

    Returns None (and leaves the window alone) when it holds no login yet.
    On success the window is asked to quit.
    """
    browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    try:
        cdp = browser.new_browser_cdp_session()
        context = browser.contexts[0]
        cookies: list[dict] = []
        # The site sets its login cookie while logging in, but the redirect
        # may win the race.
        deadline = time.monotonic() + LOGIN_COOKIE_WAIT_S
        while time.monotonic() < deadline:
            cookies = site.keep_cookies(context.cookies())
            if site.has_login(cookies):
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


def _wait_for_login(playwright, window: LoginWindow, site: LoginSite, timeout_s: int) -> tuple[list[dict], str]:
    """Wait until a tab of the window reaches a logged-in page with a login."""
    deadline = time.monotonic() + timeout_s
    checked: list[str] = []
    while time.monotonic() < deadline:
        if not window.is_open():
            raise LoginCancelled("The browser was closed before the login finished.")
        port = window.debug_port()
        arrived = [u for u in page_urls(port) if site.is_logged_in_url(u)] if port else []
        # Check once per arrival on a logged-in page (or move between them),
        # so nothing attaches to the browser while the user is still on the
        # login page.
        if arrived and arrived != checked:
            found = _read_live_login(playwright, port, site)
            if found:
                return found
        checked = arrived
        time.sleep(POLL_MS / 1000)
    raise LoginCancelled(f"The login didn't finish within {timeout_s // 60} minutes.")


def prepare_profile(profile_dir: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        # The profile holds the login cookies.
        os.chmod(profile_dir, 0o700)
    # A port file left over from an earlier run would point at a dead browser.
    (profile_dir / DEBUG_PORT_FILE).unlink(missing_ok=True)


def capture_login(
    site: LoginSite,
    profile_dir: Path,
    timeout_s: int = LOGIN_TIMEOUT_S,
    on_status: Callable[[str], None] = lambda msg: None,
    open_browser: Callable[[Path, Path, str], LoginWindow] = open_login_browser,
) -> tuple[list[dict], str]:
    """Let the user log in to *site* in a normal browser window.

    Returns the kept cookies and the browser's user agent, read from the
    running window, which is then closed. The window uses *profile_dir* as
    its profile, which is kept so the site recognises the device on later
    logins. *open_browser* exists for tests.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    profile_dir = Path(profile_dir).resolve()
    prepare_profile(profile_dir)
    with sync_playwright() as p:
        executable = login_browser(p)
        window = open_browser(executable, profile_dir, site.login_url)
        on_status(
            f"Log in to {site.name} in the browser window that just opened; "
            "it closes by itself once you're logged in."
        )
        try:
            return _wait_for_login(p, window, site, timeout_s)
        except PlaywrightError as exc:
            raise AuthError(f"Reading the login failed: {str(exc).splitlines()[0]}") from exc
        finally:
            window.close()


def normalize_user_agent(user_agent: str) -> str:
    # Headless Chromium advertises itself; look like the browser the user
    # logged in with instead.
    return user_agent.replace("HeadlessChrome", "Chrome")
