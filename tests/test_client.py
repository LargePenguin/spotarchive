import pytest

from conftest import FakeResponse, page, track_json
from spotarchive.spotify.client import API_URL, MAX_RETRIES, RateLimited, SpotifyApiError, SpotifyClient
from spotarchive.spotify.models import LIKED_SONGS_ID, Playlist, Track


@pytest.fixture
def client(http, tokens):
    return SpotifyClient(tokens, user_agent="dummy-agent", http=http, sleep=lambda s: None)


def test_sends_token_and_user_agent(client, http):
    http.add("/me", FakeResponse(200, {"id": "fake-user"}))
    assert client.current_user() == {"id": "fake-user"}
    _, headers = http.calls[0]
    assert headers["Authorization"] == "Bearer dummy-token-0"
    assert headers["User-Agent"] == "dummy-agent"


def test_refreshes_token_once_on_401(client, http, tokens):
    http.add("/me", FakeResponse(401, {"error": {"message": "expired"}}), FakeResponse(200, {"id": "fake-user"}))
    client.current_user()
    assert tokens.refreshes == 1
    assert http.calls[-1][1]["Authorization"] == "Bearer dummy-token-1"


def test_second_401_is_an_error(client, http):
    http.add("/me", FakeResponse(401, {"error": {"message": "nope"}}))
    with pytest.raises(SpotifyApiError) as exc:
        client.current_user()
    assert exc.value.status == 401


def test_retries_rate_limits_using_retry_after(http, tokens):
    waits = []
    client = SpotifyClient(tokens, http=http, sleep=waits.append)
    http.add("/me", FakeResponse(429, headers={"Retry-After": "3"}), FakeResponse(200, {"id": "fake-user"}))
    client.current_user()
    assert waits == [4]


def test_reports_each_wait(http, tokens):
    reported = []
    client = SpotifyClient(tokens, http=http, sleep=lambda s: None, on_wait=reported.append)
    http.add("/me", FakeResponse(429, headers={"Retry-After": "3"}), FakeResponse(200, {"id": "fake-user"}))
    client.current_user()
    assert reported == [4]


def test_long_retry_after_fails_without_waiting(http, tokens):
    waits = []
    client = SpotifyClient(tokens, http=http, sleep=waits.append)
    http.add("/me", FakeResponse(429, {"error": {"message": "API rate limit exceeded"}}, headers={"Retry-After": "86400"}))
    with pytest.raises(RateLimited) as exc:
        client.current_user()
    assert waits == []
    assert exc.value.status == 429
    assert exc.value.retry_after_s == 86400


def test_rate_limit_without_retry_after_gives_up_after_max_retries(client, http):
    http.add("/me", FakeResponse(429))
    with pytest.raises(RateLimited) as exc:
        client.current_user()
    assert exc.value.retry_after_s is None
    assert len(http.calls) == MAX_RETRIES + 1


def test_gives_up_after_max_retries(client, http):
    http.add("/me", FakeResponse(503))
    with pytest.raises(SpotifyApiError):
        client.current_user()


def test_playlists_lists_liked_songs_first_and_paginates(client, http):
    http.add("/me/tracks?limit=1", page([], total=7))
    next_url = f"{API_URL}/me/playlists?offset=50"
    http.add("/me/playlists?limit=50", page(
        [{"id": "p1", "name": "Road Trip", "owner": {"display_name": "Someone"}, "items": {"total": 2}}],
        total=2, next_url=next_url,
    ))
    http.add("/me/playlists?offset=50", page([{"id": "p2", "name": "Old Shape", "tracks": {"total": 5}}], total=2))
    playlists = client.playlists()
    assert [(p.id, p.name, p.total) for p in playlists] == [
        (LIKED_SONGS_ID, "Liked Songs", 7),
        ("p1", "Road Trip", 2),
        ("p2", "Old Shape", 5),
    ]
    assert playlists[1].owner == "Someone"


def test_playlist_tracks_uses_items_endpoint_and_reports_progress(client, http):
    http.add("/playlists/p1/items?limit=50", page([
        {"added_at": "2024-01-01T00:00:00Z", "item": track_json("Song One")},
        {"added_at": "2024-01-02T00:00:00Z", "item": None},
        {"added_at": "2024-01-03T00:00:00Z", "item": {"type": "episode", "name": "A Podcast"}},
    ]))
    progress = []
    tracks = client.playlist_tracks(Playlist("p1", "Road Trip"), lambda done, total: progress.append((done, total)))
    assert [t.name for t in tracks] == ["Song One"]
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_playlist_tracks_falls_back_to_old_tracks_endpoint(client, http):
    http.add("/playlists/p1/tracks?limit=50", page([{"added_at": "2024-01-01T00:00:00Z", "track": track_json("Old")}]))
    assert [t.name for t in client.playlist_tracks(Playlist("p1", "Road Trip"))] == ["Old"]


def test_playlist_tracks_doesnt_fall_back_on_other_errors(client, http):
    http.add("/playlists/p1/items?limit=50", FakeResponse(400, {"error": {"message": "bad"}}))
    with pytest.raises(SpotifyApiError):
        client.playlist_tracks(Playlist("p1", "Road Trip"))


def test_liked_songs_use_saved_tracks_endpoint(client, http):
    http.add("/me/tracks?limit=50", page([{"added_at": "2024-01-01T00:00:00Z", "track": track_json("Liked")}]))
    assert [t.name for t in client.playlist_tracks(Playlist(LIKED_SONGS_ID, "Liked Songs"))] == ["Liked"]


def test_track_parsing():
    track = Track.from_playlist_entry({
        "added_at": "2024-05-06T07:08:09Z",
        "item": track_json("Song", artists=("Tyler, The Fake", "Other Fake"), release="1999"),
    })
    assert track.artists == ["Tyler, The Fake", "Other Fake"]
    assert track.release_date == "1999"
    assert track.date_added == "2024-05-06T07:08:09Z"
    assert track.isrc == "XX0000000001"
    assert track.duration_ms == 180000


def test_track_parsing_tolerates_missing_fields():
    track = Track.from_playlist_entry({"track": {"name": "Local File", "artists": [{"name": "Fake"}], "album": None}})
    assert (track.name, track.album, track.isrc, track.spotify_url) == ("Local File", "", "", "")
