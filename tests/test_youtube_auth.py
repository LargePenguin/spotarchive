from urllib.parse import parse_qs, urlparse

from spotarchive.session import YouTubeSession
from spotarchive.youtube.auth import (
    LOGIN_URL,
    has_login_cookie,
    is_logged_in,
    is_logged_in_url,
    youtube_cookies,
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
