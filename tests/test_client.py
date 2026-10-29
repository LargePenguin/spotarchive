import pytest

from conftest import (
    FakeResponse,
    FakeTokens,
    library_page,
    library_playlist,
    ok,
    playlist_entry,
    playlist_page,
    track_json,
)
from spotarchive.spotify.client import (
    DEFAULT_QUERY_HASHES,
    MAX_RETRIES,
    QueryOutdated,
    RateLimited,
    SpotifyApiError,
    SpotifyClient,
)
from spotarchive.spotify.models import LIKED_SONGS_ID, Playlist, Track

FRESH_HASH = "f" * 64


@pytest.fixture
def client(http, tokens):
    return SpotifyClient(tokens, user_agent="dummy-agent", http=http, sleep=lambda s: None)


def profile(name="Fake User", username="fake-user"):
    return ok({"me": {"profile": {"name": name, "username": username, "uri": f"spotify:user:{username}"}}})


def test_sends_token_user_agent_and_query_hash(client, http):
    http.add("profileAttributes", profile())
    assert client.current_user() == {"id": "fake-user", "display_name": "Fake User"}
    operation, variables, query_hash, headers = http.calls[0]
    assert operation == "profileAttributes"
    assert query_hash == DEFAULT_QUERY_HASHES["profileAttributes"]
    assert headers["Authorization"] == "Bearer dummy-token-0"
    assert headers["User-Agent"] == "dummy-agent"


def test_prefers_hashes_read_from_the_web_player(http):
    client = SpotifyClient(FakeTokens({"profileAttributes": FRESH_HASH}), http=http)
    http.add("profileAttributes", profile())
    client.current_user()
    assert http.calls[0][2] == FRESH_HASH


def test_missing_profile_is_an_error(client, http):
    http.add("profileAttributes", ok({"me": None}))
    with pytest.raises(SpotifyApiError):
        client.current_user()


def test_refreshes_token_once_on_401(client, http, tokens):
    http.add("profileAttributes", FakeResponse(401, {"error": {"message": "expired"}}), profile())
    client.current_user()
    assert tokens.refreshes == 1
    assert http.calls[-1][3]["Authorization"] == "Bearer dummy-token-1"


def test_second_401_is_an_error(client, http):
    http.add("profileAttributes", FakeResponse(401, {"error": {"message": "nope"}}))
    with pytest.raises(SpotifyApiError) as exc:
        client.current_user()
    assert exc.value.status == 401


def test_unknown_query_rereads_hashes_and_retries(client, http, tokens):
    tokens.hashes_after_refresh = {"profileAttributes": FRESH_HASH}
    http.add("profileAttributes", FakeResponse(412), profile())
    assert client.current_user()["id"] == "fake-user"
    assert tokens.refreshes == 1
    assert http.calls[-1][2] == FRESH_HASH


def test_unknown_query_after_rereading_hashes_is_outdated(client, http, tokens):
    http.add("profileAttributes", FakeResponse(412))
    with pytest.raises(QueryOutdated) as exc:
        client.current_user()
    assert "update" in str(exc.value)
    assert tokens.refreshes == 1


def test_graphql_errors_are_reported(client, http):
    http.add("profileAttributes", FakeResponse(200, {"errors": [{"message": "Something broke"}]}))
    with pytest.raises(SpotifyApiError, match="Something broke"):
        client.current_user()


def test_retries_rate_limits_using_retry_after(http, tokens):
    waits = []
    client = SpotifyClient(tokens, http=http, sleep=waits.append)
    http.add("profileAttributes", FakeResponse(429, headers={"Retry-After": "3"}), profile())
    client.current_user()
    assert waits == [4]


def test_reports_each_wait(http, tokens):
    reported = []
    client = SpotifyClient(tokens, http=http, sleep=lambda s: None, on_wait=reported.append)
    http.add("profileAttributes", FakeResponse(429, headers={"Retry-After": "3"}), profile())
    client.current_user()
    assert reported == [4]


def test_long_retry_after_fails_without_waiting(http, tokens):
    waits = []
    client = SpotifyClient(tokens, http=http, sleep=waits.append)
    http.add("profileAttributes", FakeResponse(
        429, {"error": {"message": "API rate limit exceeded"}}, headers={"Retry-After": "86400"},
    ))
    with pytest.raises(RateLimited) as exc:
        client.current_user()
    assert waits == []
    assert exc.value.status == 429
    assert exc.value.retry_after_s == 86400


def test_rate_limit_without_retry_after_gives_up_after_max_retries(client, http):
    http.add("profileAttributes", FakeResponse(429))
    with pytest.raises(RateLimited) as exc:
        client.current_user()
    assert exc.value.retry_after_s is None
    assert len(http.calls) == MAX_RETRIES + 1


def test_gives_up_after_max_retries(client, http):
    http.add("profileAttributes", FakeResponse(503))
    with pytest.raises(SpotifyApiError):
        client.current_user()


def test_playlists_lists_liked_songs_first_and_paginates(client, http):
    liked = {"__typename": "LibraryPseudoPlaylistResponseWrapper", "_uri": "spotify:collection:tracks",
             "data": {"__typename": "PseudoPlaylist", "uri": "spotify:collection:tracks", "count": 7}}
    folder = {"__typename": "LibraryFolderResponseWrapper", "_uri": "spotify:user:fake:folder:f1",
              "data": {"__typename": "Folder", "name": "A Folder"}}
    http.add(
        "libraryV3",
        library_page([library_playlist("p1", "Road Trip", owner="Someone"), liked], total=4),
        library_page([folder, library_playlist("p2", "Old Shape")], total=4),
    )
    playlists = client.playlists()
    assert [(p.id, p.name, p.total) for p in playlists] == [
        (LIKED_SONGS_ID, "Liked Songs", 7),
        ("p1", "Road Trip", None),
        ("p2", "Old Shape", None),
    ]
    assert playlists[1].owner == "Someone"
    first, second = (call[1] for call in http.calls)
    assert (first["offset"], second["offset"]) == (0, 2)
    assert first["flatten"] is True and first["filters"] == ["Playlists"]


def test_playlists_always_include_liked_songs(client, http):
    http.add("libraryV3", library_page([library_playlist("p1", "Road Trip")]))
    assert [p.id for p in client.playlists()] == [LIKED_SONGS_ID, "p1"]
    assert [p.id for p in client.playlists(include_liked_songs=False)] == ["p1"]


def test_playlist_tracks_paginates_skips_non_songs_and_reports_progress(client, http):
    http.add(
        "fetchPlaylist",
        playlist_page([
            playlist_entry(track_json("Song One")),
            {"addedAt": None, "itemV2": {"__typename": "RestrictedContent", "data": None}},
        ], total=3),
        playlist_page([playlist_entry({"__typename": "Episode", "name": "A Podcast"})], total=3),
    )
    progress = []
    tracks = client.playlist_tracks(Playlist("p1", "Road Trip"), lambda done, total: progress.append((done, total)))
    assert [t.name for t in tracks] == ["Song One"]
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert [call[1]["uri"] for call in http.calls] == ["spotify:playlist:p1"] * 2
    assert [call[1]["offset"] for call in http.calls] == [0, 2]


def test_unavailable_playlist_is_an_error(client, http):
    http.add("fetchPlaylist", ok({"playlistV2": {"__typename": "NotFound", "message": "Object not found"}}))
    with pytest.raises(SpotifyApiError, match="Object not found") as exc:
        client.playlist_tracks(Playlist("p1", "Road Trip"))
    assert exc.value.status == 404


def test_liked_songs_use_library_tracks_query(client, http):
    entry = {"addedAt": {"isoString": "2024-01-01T00:00:00Z"},
             "track": {"_uri": "spotify:track:liked1", "data": {**track_json("Liked"), "uri": None}}}
    http.add("fetchLibraryTracks", ok({"me": {"library": {"tracks": {"items": [entry], "totalCount": 1}}}}))
    tracks = client.playlist_tracks(Playlist(LIKED_SONGS_ID, "Liked Songs"))
    assert [(t.name, t.spotify_url) for t in tracks] == [("Liked", "https://open.spotify.com/track/liked1")]
    assert http.calls[0][0] == "fetchLibraryTracks"


def test_track_parsing():
    data = track_json("Song", artists=("Tyler, The Fake", "Other Fake"), release="1999-01-01T00:00:00Z",
                      precision="YEAR", track_id="abc")
    track = Track.from_api(data, {"isoString": "2024-05-06T07:08:09Z"})
    assert track.artists == ["Tyler, The Fake", "Other Fake"]
    assert track.album == "Fake Album"
    assert track.release_date == "1999"
    assert track.date_added == "2024-05-06T07:08:09Z"
    assert track.duration_ms == 180000
    assert track.spotify_url == "https://open.spotify.com/track/abc"
    assert track.isrc == ""


def test_track_parsing_tolerates_missing_fields():
    track = Track.from_api({"__typename": "LocalTrack", "name": "Local File"})
    assert (track.name, track.artists, track.album, track.release_date, track.spotify_url) == (
        "Local File", [], "", "", "",
    )
