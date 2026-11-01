from urllib.parse import parse_qs, urlparse

from spotarchive.session import YouTubeSession
import pytest

from spotarchive.youtube.auth import (
    LOGIN_URL,
    CookieFileError,
    has_login_cookie,
    import_cookies,
    is_logged_in,
    is_logged_in_url,
    parse_netscape_cookies,
    youtube_cookies,
)

# Like what browser extensions and yt-dlp export (fake values).
COOKIES_TXT = (
    "# Netscape HTTP Cookie File\n"
    "# https://curl.haxx.se/rfc/cookie_spec.html\n"
    "\n"
    ".youtube.com\tTRUE\t/\tTRUE\t4102444800\tSAPISID\tdummy-sapisid\r\n"
    "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t4102444800\t__Secure-3PSID\tdummy-psid\n"
    "www.youtube.com\tFALSE\t/\tFALSE\t0\tPREF\tdummy-pref\n"
    "youtube.com\tTRUE\t/\tFALSE\t0\tVISITOR\tdummy-visitor\n"
    ".google.com\tTRUE\t/\tTRUE\t4102444800\tSID\tdummy-google\n"
    "not a cookie line\n"
)


def cookie(name="SAPISID", domain=".youtube.com", expires=-1):
    return {"name": name, "value": "dummy-cookie", "domain": domain, "path": "/", "expires": expires}


def test_login_url_continues_to_youtube():
    url = urlparse(LOGIN_URL)
    assert url.hostname == "accounts.google.com"
    continue_to = urlparse(parse_qs(url.query)["continue"][0])
    assert continue_to.hostname == "www.youtube.com"


def test_logged_in_urls():
    assert is_logged_in_url("https://www.youtube.com/")
    assert is_logged_in_url("https://music.youtube.com/")
    # Still setting cookies.
    assert not is_logged_in_url("https://www.youtube.com/signin?action_handle_signin=true")
    assert not is_logged_in_url("https://accounts.google.com/ServiceLogin")
    assert not is_logged_in_url("https://youtube.com.example.com/")


def test_only_youtube_cookies_are_kept():
    kept = youtube_cookies([cookie(), cookie(domain="www.youtube.com"), cookie(domain=".google.com"),
                            cookie(domain="example.com")])
    assert [c["domain"] for c in kept] == [".youtube.com", "www.youtube.com"]


def test_login_cookie_detection():
    assert has_login_cookie([cookie("__Secure-3PAPISID")])
    assert not has_login_cookie([cookie("PREF")])
    assert not has_login_cookie([])


def test_expired_login_cookie_doesnt_count():
    assert has_login_cookie([cookie(expires=2000)], at_s=1000)
    assert not has_login_cookie([cookie(expires=1000)], at_s=2000)
    # Session cookies don't expire on their own.
    assert has_login_cookie([cookie(expires=-1)], at_s=2000)


def test_is_logged_in():
    assert not is_logged_in(None)
    assert is_logged_in(YouTubeSession(cookies=[cookie()], user_agent="dummy-agent"))
    assert not is_logged_in(YouTubeSession(cookies=[cookie(expires=1)], user_agent="dummy-agent"))


def test_parse_netscape_cookies():
    cookies = {c["name"]: c for c in parse_netscape_cookies(COOKIES_TXT)}
    assert set(cookies) == {"SAPISID", "__Secure-3PSID", "PREF", "VISITOR", "SID"}
    assert cookies["SAPISID"] == {
        "name": "SAPISID", "value": "dummy-sapisid", "domain": ".youtube.com", "path": "/",
        "expires": 4102444800, "httpOnly": False, "secure": True,
    }
    assert cookies["__Secure-3PSID"]["httpOnly"] is True
    # 0 is a session cookie.
    assert cookies["PREF"]["expires"] == -1
    assert cookies["PREF"]["domain"] == "www.youtube.com"
    # Including subdomains means a leading dot.
    assert cookies["VISITOR"]["domain"] == ".youtube.com"


def test_import_cookies_keeps_youtube_cookies(tmp_path):
    path = tmp_path / "cookies.txt"
    # A BOM, as some Windows editors write.
    path.write_text("﻿" + COOKIES_TXT, encoding="utf-8")
    session = import_cookies(path)
    assert is_logged_in(session)
    assert {c["name"] for c in session.cookies} == {"SAPISID", "__Secure-3PSID", "PREF", "VISITOR"}
    assert session.logged_in_at_ms > 0
    # Round trip through what the backend hands to yt-dlp.
    assert ".youtube.com\tTRUE\t/\tTRUE\t4102444800\tSAPISID\tdummy-sapisid" in session.netscape_cookies()


def test_import_cookies_without_youtube_login(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(".google.com\tTRUE\t/\tTRUE\t4102444800\tSAPISID\tdummy\n")
    with pytest.raises(CookieFileError, match="no YouTube login"):
        import_cookies(path)


def test_import_cookies_with_expired_login(tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text(".youtube.com\tTRUE\t/\tTRUE\t1000\tSAPISID\tdummy\n")
    with pytest.raises(CookieFileError):
        import_cookies(path)


def test_import_missing_cookie_file(tmp_path):
    with pytest.raises(CookieFileError, match="Couldn't read"):
        import_cookies(tmp_path / "missing.txt")
