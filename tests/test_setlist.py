import pytest

from spotarchive.setlist import (
    COLUMNS,
    SETLIST_FILENAME,
    assign_folders,
    prepare_folder,
    read_setlist,
    safe_folder_name,
    write_setlist,
)
from spotarchive.spotify.models import Track


@pytest.mark.parametrize("name, expected", [
    ("Road Trip", "Road Trip"),
    ('a<b>c:d"e/f\\g|h?i*j', "a_b_c_d_e_f_g_h_i_j"),
    ("  spaced   out  ", "spaced out"),
    ("ends with dots...", "ends with dots"),
    ("CON", "_CON"),
    ("com1.mix", "_com1.mix"),
    ("Console", "Console"),
    ("...", "playlist"),
    ("", "playlist"),
    ("Ünïcödé 🎵", "Ünïcödé 🎵"),
])
def test_safe_folder_name(name, expected):
    assert safe_folder_name(name) == expected


def test_safe_folder_name_is_length_limited():
    assert len(safe_folder_name("x" * 500)) == 100


def test_assign_folders_disambiguates_case_insensitively():
    folders = assign_folders({"p1": "Chill", "p2": "chill"}, {})
    assert folders == {"p1": "Chill", "p2": "chill (p2)"}


def test_assign_folders_lets_previous_owner_keep_its_folder():
    folders = assign_folders({"p1": "Chill", "p2": "Chill"}, {"p2": "Chill"})
    assert folders == {"p1": "Chill (p1)", "p2": "Chill"}


def test_assign_folders_avoids_reserved_folders():
    assert assign_folders({"p1": "Chill"}, {}, reserved={"CHILL"}) == {"p1": "Chill (p1)"}


def test_prepare_folder_moves_renamed_playlist(tmp_path):
    (tmp_path / "Old Name").mkdir()
    (tmp_path / "Old Name" / "song.mp3").write_bytes(b"fake")
    folder = prepare_folder(tmp_path, "New Name", "Old Name")
    assert folder == tmp_path / "New Name"
    assert (folder / "song.mp3").exists()
    assert not (tmp_path / "Old Name").exists()


def test_prepare_folder_doesnt_overwrite_existing_target(tmp_path):
    (tmp_path / "Old").mkdir()
    (tmp_path / "New").mkdir()
    prepare_folder(tmp_path, "New", "Old")
    assert (tmp_path / "Old").exists()


def test_write_and_read_setlist(tmp_path):
    tracks = [
        Track("Song, with comma", ["Tyler, The Fake", "Other"], "Album \"Quoted\"", "2001", "2024-01-01T00:00:00Z",
              123, "XX0000000001", "https://open.spotify.com/track/fake"),
        Track("Ünïcödé", ["Fake"]),
    ]
    write_setlist(tmp_path, tracks)
    rows = read_setlist(tmp_path)
    assert list(rows[0]) == COLUMNS
    assert rows[0]["name"] == "Song, with comma"
    assert rows[0]["artist"] == "Tyler, The Fake; Other"
    assert rows[0]["album"] == 'Album "Quoted"'
    assert rows[0]["duration_ms"] == "123"
    assert rows[1]["name"] == "Ünïcödé"
    assert rows[1]["duration_ms"] == ""
    assert [p.name for p in tmp_path.iterdir()] == [SETLIST_FILENAME]


def test_write_setlist_replaces_previous(tmp_path):
    write_setlist(tmp_path, [Track("A"), Track("B")])
    write_setlist(tmp_path, [Track("C")])
    assert [r["name"] for r in read_setlist(tmp_path)] == ["C"]
