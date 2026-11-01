"""The browser window the logins happen in, shared by every site.

The login window is started as a plain process, not driven by Playwright: an
automated browser (``navigator.webdriver``, a throwaway profile, a debugger
attached to the page) gets stuck in endless captchas on login pages.

There are two ways to find out when the login is done:

- *Watched* sites (Spotify): the window gets a DevTools port. While the user
  logs in, spotarchive only polls its HTTP endpoint for the URLs of the tabs;
  it connects over DevTools once a tab has reached the site's logged-in page,
  reads the cookies and user agent, and asks the window to quit.
- Unwatched sites (Google/YouTube): any DevTools port turns
  ``navigator.webdriver`` on, and Google's sign-in refuses such a browser as
  "not secure". So the window has no port at all, and the user closes it once
  logged in. Then the same browser opens the profile headless (with a port,
  but loading no page) and the cookies are read from it. The browser
  decrypts its own cookie store, which nothing else can do reliably (e.g.
  Chrome's app-bound cookie encryption on Windows).

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
# How long the headless browser that reads a closed window's profile may take to start.
HEADLESS_START_TIMEOUT_S = 30
POLL_MS = 500
# Where Chromium writes the DevTools port it picked for --remote-debugging-port=0.
DEBUG_PORT_FILE = "DevToolsActivePort"

# Installed browsers preferred for the login window, before Playwright's
# bundled Chromium.
LINUX_BROWSERS = ("google-chrome", "google-chrome-stable", "microsoft-edge", "microsoft-edge-stable")
WINDOWS_BROWSERS = ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe")
WINDOWS_ROOTS = ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")

# Browsers the user can pick for the login window (config: [login] browser):
# name -> (executable names on PATH on Linux, paths below WINDOWS_ROOTS).
# "chromium" also accepts Playwright's bundled Chromium.
NAMED_BROWSERS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "chrome": (("google-chrome", "google-chrome-stable", "chrome"), ("Google/Chrome/Application/chrome.exe",)),
    "edge": (("microsoft-edge", "microsoft-edge-stable"), ("Microsoft/Edge/Application/msedge.exe",)),
    "brave": (
        ("brave-browser", "brave-browser-stable", "brave"),
        ("BraveSoftware/Brave-Browser/Application/brave.exe",),
    ),
    "vivaldi": (("vivaldi", "vivaldi-stable"), ("Vivaldi/Application/vivaldi.exe",)),
    "opera": (("opera",), ("Programs/Opera/opera.exe", "Opera/opera.exe")),
    "chromium": (("chromium", "chromium-browser"), ("Chromium/Application/chrome.exe",)),
}
# Flatpak app IDs of the same browsers (Linux), used when no executable is on PATH.
FLATPAK_BROWSERS = {
    "chrome": "com.google.Chrome",
    "edge": "com.microsoft.Edge",
    "brave": "com.brave.Browser",
    "vivaldi": "com.vivaldi.Vivaldi",
    "opera": "com.opera.Opera",
    "chromium": "org.chromium.Chromium",
}
FLATPAK_PREFIX = "flatpak:"
BROWSER_ALIASES = {"google-chrome": "chrome", "msedge": "edge", "microsoft-edge": "edge", "brave-browser": "brave"}


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
    # False: the window gets no DevTools port, and the user closes it once
    # logged in (see the module docstring).
    watch_window: bool = True


def installed_browsers() -> list[Path]:
    """Chrome and Edge executables installed on this computer."""
    if os.name == "nt":
        roots = [os.environ.get(var) for var in WINDOWS_ROOTS]
        found = [Path(root) / rel for rel in WINDOWS_BROWSERS for root in roots if root]
    else:
        found = [Path(path) for name in LINUX_BROWSERS if (path := shutil.which(name))]
    return [path for path in found if path.is_file()]


def canonical_browser(name: str) -> str | None:
    """The key in NAMED_BROWSERS for a user-given name (case-insensitive), or None if unknown."""
    key = name.strip().lower()
    key = BROWSER_ALIASES.get(key, key)
    return key if key in NAMED_BROWSERS else None


def named_browser(name: str) -> list[Path]:
    """Executables of the browser called *name* (a NAMED_BROWSERS key) installed on this computer."""
    linux_names, windows_paths = NAMED_BROWSERS[name]
    if os.name == "nt":
        roots = [os.environ.get(var) for var in WINDOWS_ROOTS]
        found = [Path(root) / rel for rel in windows_paths for root in roots if root]
    else:
        found = [Path(path) for exe in linux_names if (path := shutil.which(exe))]
    return [path for path in found if path.is_file()]


def flatpak_installed(app_id: str) -> bool:
    """Whether the Flatpak app *app_id* is installed (Linux only)."""
    if os.name == "nt" or not shutil.which("flatpak"):
        return False
    try:
        return subprocess.run(
            ["flatpak", "info", app_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def login_browser(playwright, browser: str | None = None) -> Path | str:
    """The browser for the login window: *browser* (a NAMED_BROWSERS name),
    or by default the first installed Chrome, Edge or Playwright's Chromium.

    A Flatpak install is returned as ``"flatpak:<app id>"``.
    """
    bundled = Path(playwright.chromium.executable_path)
    if browser:
        key = canonical_browser(browser)
        if key is None:
            raise BrowserUnavailable(f"Unknown browser {browser!r}; choose one of: {', '.join(NAMED_BROWSERS)}.")
        candidates: list[Path | str] = list(named_browser(key))
        if not candidates and flatpak_installed(FLATPAK_BROWSERS[key]):
            candidates.append(FLATPAK_PREFIX + FLATPAK_BROWSERS[key])
        if key == "chromium" and bundled.is_file():
            candidates.append(bundled)
        if not candidates:
            raise BrowserUnavailable(
                f"Couldn't find {key} on this computer (login.browser in the configuration file). "
                "Install it, or leave the setting empty to use whichever supported browser is found."
            )
        return candidates[0]
    candidates = installed_browsers()
    if bundled.is_file():
        candidates.append(bundled)
    if not candidates:
        raise BrowserUnavailable(
            "Couldn't find a browser. Install Google Chrome or Microsoft Edge, "
            "or Playwright's Chromium with `python -m playwright install chromium`."
        )
    return candidates[0]


def login_browser_command(
    executable: Path | str, profile_dir: Path, url: str, debug_port: bool = True, headless: bool = False
) -> list[str]:
    """A plain (not automated) browser window on its own profile.

    The DevTools port (picked by the browser, see :func:`read_debug_port`)
    only lets spotarchive see which page the window is on; nothing attaches
    to the page while the user logs in. Without *debug_port* there is none,
    so ``navigator.webdriver`` stays false. *headless* opens no window; it's
    for reading a profile's cookies.
    """
    if str(executable).startswith(FLATPAK_PREFIX):
        # The sandbox needs access to the profile folder (it holds the port file).
        launcher = ["flatpak", "run", f"--filesystem={profile_dir}", str(executable)[len(FLATPAK_PREFIX):]]
    else:
        launcher = [str(executable)]
    return [
        *launcher,
        f"--user-data-dir={profile_dir}",
        *(["--remote-debugging-port=0"] if debug_port else []),
        *(["--headless=new"] if headless else []),
        # No --disable-blink-features=AutomationControlled here: current Chrome
        # no longer supports it and shows a warning banner in the window.
        "--no-first-run",
        "--no-default-browser-check",
        # Keep the profile's cookies out of the system keyring (no prompt).
        "--password-store=basic",
        "--use-mock-keychain",
        *([] if headless else ["--new-window"]),
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

    def __init__(
        self, executable: Path | str, profile_dir: Path, url: str, debug_port: bool = True, headless: bool = False
    ):
        self.profile_dir = profile_dir
        try:
            self._process = subprocess.Popen(
                login_browser_command(executable, profile_dir, url, debug_port, headless),
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


def open_login_browser(
    executable: Path | str, profile_dir: Path, url: str, debug_port: bool = True
) -> LoginWindow:
    return BrowserProcess(executable, profile_dir, url, debug_port)


def open_headless_browser(executable: Path | str, profile_dir: Path) -> LoginWindow:
    """The same browser on *profile_dir*, headless, with a DevTools port and no page loaded."""
    return BrowserProcess(executable, profile_dir, "about:blank", debug_port=True, headless=True)


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


def _wait_for_close(window: LoginWindow, timeout_s: int) -> None:
    """Wait until the user has closed the (unwatched) login window."""
    deadline = time.monotonic() + timeout_s
    while window.is_open():
        if time.monotonic() >= deadline:
            raise LoginCancelled(
                f"The browser window was still open after {timeout_s // 60} minutes; "
                "close it once you're logged in."
            )
        time.sleep(POLL_MS / 1000)


def _read_profile_login(
    playwright,
    executable: Path | str,
    profile_dir: Path,
    site: LoginSite,
    open_headless: Callable[[Path | str, Path], LoginWindow],
) -> tuple[list[dict], str]:
    """Read the login from the closed window's profile, with the same browser started headless."""
    (profile_dir / DEBUG_PORT_FILE).unlink(missing_ok=True)
    reader = open_headless(executable, profile_dir)
    try:
        deadline = time.monotonic() + HEADLESS_START_TIMEOUT_S
        while (port := reader.debug_port()) is None:
            if not reader.is_open() or time.monotonic() >= deadline:
                raise AuthError("The browser didn't start in the background to read the login.")
            time.sleep(POLL_MS / 1000)
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        try:
            cdp = browser.new_browser_cdp_session()
            cookies = site.keep_cookies(browser.contexts[0].cookies())
            user_agent = cdp.send("Browser.getVersion")["userAgent"]
            cdp.send("Browser.close")
        finally:
            try:
                browser.close()  # only disconnects from a browser it didn't launch
            except Exception:
                pass
    finally:
        reader.close()
    if not site.has_login(cookies):
        raise LoginCancelled(f"The browser was closed before the {site.name} login finished.")
    return cookies, user_agent


def _capture_unwatched(
    playwright,
    executable: Path | str,
    profile_dir: Path,
    site: LoginSite,
    timeout_s: int,
    on_status: Callable[[str], None],
    open_browser: Callable[..., LoginWindow],
    open_headless: Callable[[Path | str, Path], LoginWindow],
) -> tuple[list[dict], str]:
    from playwright.sync_api import Error as PlaywrightError

    window = open_browser(executable, profile_dir, site.login_url, debug_port=False)
    on_status(
        f"Log in to {site.name} in the browser window that just opened, "
        f"then close the window once {site.name} shows you're logged in."
    )
    try:
        _wait_for_close(window, timeout_s)
    finally:
        window.close()
    on_status("Reading the login from the browser profile...")
    try:
        return _read_profile_login(playwright, executable, profile_dir, site, open_headless)
    except PlaywrightError as exc:
        raise AuthError(f"Reading the login failed: {str(exc).splitlines()[0]}") from exc


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
    open_browser: Callable[..., LoginWindow] = open_login_browser,
    browser: str | None = None,
    open_headless: Callable[[Path | str, Path], LoginWindow] = open_headless_browser,
) -> tuple[list[dict], str]:
    """Let the user log in to *site* in a normal browser window.

    Returns the kept cookies and the browser's user agent. For a watched
    site they're read from the running window, which is then closed; for an
    unwatched one the user closes the window and they're read from its
    profile (see the module docstring). The window uses *profile_dir* as its
    profile, which is kept so the site recognises the device on later
    logins. *browser* names the browser to use (see :data:`NAMED_BROWSERS`);
    by default the first installed Chrome, Edge or Playwright's Chromium.
    *open_browser* and *open_headless* exist for tests.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    profile_dir = Path(profile_dir).resolve()
    prepare_profile(profile_dir)
    with sync_playwright() as p:
        executable = login_browser(p, browser)
        on_status(describe_browser(executable))
        if not site.watch_window:
            return _capture_unwatched(
                p, executable, profile_dir, site, timeout_s, on_status, open_browser, open_headless
            )
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


def describe_browser(executable: Path | str) -> str:
    """The "Using ..." line shown before the login window opens."""
    name = str(executable)
    if name.startswith(FLATPAK_PREFIX):
        return f"Using {name[len(FLATPAK_PREFIX):]} (Flatpak)"
    return f"Using {name}"


def normalize_user_agent(user_agent: str) -> str:
    # Headless Chromium advertises itself; look like the browser the user
    # logged in with instead.
    return user_agent.replace("HeadlessChrome", "Chrome")
