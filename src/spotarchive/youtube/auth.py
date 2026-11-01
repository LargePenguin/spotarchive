"""Browser-based YouTube (Google) login.

Like the Spotify login, it happens in a normal browser window with a profile
of its own (see :mod:`spotarchive.browser`); spotarchive never sees the
password. Unlike it, the window has no DevTools port: Google's sign-in
refuses a browser with one ("This browser or app may not be secure"). The
user closes the window once YouTube shows them logged in, and the
youtube.com cookies and the user agent are then read from its profile.

:func:`import_cookies` is the fallback: a ``cookies.txt`` exported from the
user's own browser.

Nothing here talks to YouTube afterwards: the login is handed to the
download backend (see :meth:`spotarchive.backends.DownloadBackend.use_youtube_login`),
which passes it to its helper in whatever form that helper takes (e.g.
yt-dlp's ``cookies.txt``). The profile is only read headless, without
loading a page, and isn't opened on YouTube again, so YouTube doesn't rotate
(invalidate) the saved cookies the way it does for open browser tabs.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlparse

from spotarchive.browser import (
    LOGIN_TIMEOUT_S,
    AuthError,
    LoginSite,
    LoginWindow,
    capture_login,
    normalize_user_agent,
    open_headless_browser,
    open_login_browser,
)
from spotarchive.session import YouTubeSession, now_ms

# Google's sign-in, continuing to YouTube's sign-in handler, which sets the
# youtube.com login cookies and then opens the home page.
_CONTINUE = "https://www.youtube.com/signin?action_handle_signin=true&app=desktop&next=%2F"
LOGIN_URL = (
    "https://accounts.google.com/ServiceLogin?service=youtube&passive=true"
    f"&continue={quote(_CONTINUE, safe='')}"
)
# Cookies that prove a login; yt-dlp authenticates its requests with these.
LOGIN_COOKIES = ("SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID")


def is_youtube_host(host: str | None) -> bool:
    return bool(host) and (host == "youtube.com" or host.endswith(".youtube.com"))


def is_logged_in_url(url: str) -> bool:
    """A YouTube page other than the sign-in handler, which is still setting cookies."""
    parsed = urlparse(url)
    return is_youtube_host(parsed.hostname) and not parsed.path.startswith("/signin")


def youtube_cookies(cookies: list[dict]) -> list[dict]:
    """Keep only youtube.com cookies; Google's and other sites' are never stored."""
    return [c for c in cookies if is_youtube_host(c.get("domain", "").lstrip("."))]


def has_login_cookie(cookies: list[dict], at_s: float | None = None) -> bool:
    """Whether *cookies* hold a YouTube login that hasn't expired at *at_s*."""
    at_s = time.time() if at_s is None else at_s
    for c in cookies:
        if c.get("name") in LOGIN_COOKIES and c.get("value"):
            expires = c.get("expires", -1)
            # -1: a session cookie, kept until the login is replaced.
            if expires is None or expires <= 0 or expires > at_s:
                return True
    return False


def is_logged_in(session: YouTubeSession | None) -> bool:
    return session is not None and has_login_cookie(session.cookies)


YOUTUBE = LoginSite(
    name="YouTube",
    login_url=LOGIN_URL,
    is_logged_in_url=is_logged_in_url,
    keep_cookies=youtube_cookies,
    has_login=has_login_cookie,
    # Google's sign-in refuses a browser with a DevTools port.
    watch_window=False,
)


def interactive_login(
    profile_dir: Path,
    timeout_s: int = LOGIN_TIMEOUT_S,
    on_status: Callable[[str], None] = lambda msg: None,
    open_browser: Callable[..., LoginWindow] = open_login_browser,
    browser: str | None = None,
    open_headless: Callable[[Path | str, Path], LoginWindow] = open_headless_browser,
) -> YouTubeSession:
    """Let the user log in to YouTube in a normal browser window, then capture the session.

    The user closes the window once logged in; the login counts if the
    profile then holds YouTube's login cookies. See
    :func:`spotarchive.browser.capture_login`, which also explains
    *browser*. *open_browser* and *open_headless* exist for tests.
    """
    cookies, user_agent = capture_login(
        YOUTUBE, profile_dir, timeout_s, on_status, open_browser, browser, open_headless
    )
    return YouTubeSession(
        cookies=cookies,
        user_agent=normalize_user_agent(user_agent),
        logged_in_at_ms=now_ms(),
    )


class CookieFileError(AuthError):
    """A cookies.txt that can't be read or holds no YouTube login."""


# Netscape cookies.txt marks HttpOnly cookies by prefixing the domain.
HTTP_ONLY_PREFIX = "#HttpOnly_"


def parse_netscape_cookies(text: str) -> list[dict]:
    """Cookies (in Playwright's format) from a Netscape ``cookies.txt``.

    Comment, blank and malformed lines are skipped.
    """
    cookies = []
    for line in text.splitlines():
        http_only = line.startswith(HTTP_ONLY_PREFIX)
        if http_only:
            line = line[len(HTTP_ONLY_PREFIX):]
        elif not line.strip() or line.startswith("#"):
            continue
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) != 7:
            continue
        domain, subdomains, path, secure, expires, name, value = fields
        if subdomains.upper() == "TRUE" and not domain.startswith("."):
            domain = "." + domain
        try:
            expires_s = int(float(expires))
        except ValueError:
            continue
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
            # 0: a session cookie.
            "expires": expires_s if expires_s > 0 else -1,
            "httpOnly": http_only,
            "secure": secure.upper() == "TRUE",
        })
    return cookies


def import_cookies(path: Path) -> YouTubeSession:
    """A YouTube login from a ``cookies.txt`` exported from the user's own browser.

    Only the youtube.com cookies are kept. There's no user agent to go with
    them, so it's left empty.
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise CookieFileError(f"Couldn't read {path}: {exc}") from exc
    cookies = youtube_cookies(parse_netscape_cookies(text))
    if not has_login_cookie(cookies):
        raise CookieFileError(
            f"{path} holds no YouTube login (no unexpired SAPISID cookie for youtube.com). "
            "Export the cookies (Netscape cookies.txt format) while logged in to youtube.com."
        )
    return YouTubeSession(cookies=cookies, user_agent="", logged_in_at_ms=now_ms())
