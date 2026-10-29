"""Rebuild ``setlist.csv`` files for the selected playlists."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from spotarchive.session import PlaylistState, SessionStore
from spotarchive.setlist import assign_folders, prepare_folder, write_setlist
from spotarchive.spotify.client import ProgressCallback, SpotifyApiError, SpotifyClient
from spotarchive.spotify.models import Playlist


@dataclass
class FetchResult:
    playlist: Playlist
    folder: Path | None = None
    track_count: int = 0
    error: str | None = None


def update_selection(store: SessionStore, playlists: list[Playlist], selected_ids: list[str]) -> list[str]:
    """Record the selection for every listed playlist.

    Returns the names of previously selected playlists that are no longer in
    the user's library; they are dropped from the session.
    """
    listed = {p.id for p in playlists}
    missing = [s.name for pid, s in store.playlists.items() if s.selected and pid not in listed]
    chosen = set(selected_ids)
    store.playlists = {
        p.id: PlaylistState(
            name=p.name,
            selected=p.id in chosen,
            folder=store.playlists[p.id].folder if p.id in store.playlists else None,
        )
        for p in playlists
    }
    return missing


def fetch_setlists(
    client: SpotifyClient,
    store: SessionStore,
    data_dir: Path,
    playlists: list[Playlist],
    progress_for: Callable[[Playlist], ProgressCallback | None] = lambda p: None,
) -> list[FetchResult]:
    """Write a ``setlist.csv`` for each playlist in *playlists*.

    A failure on one playlist is reported in its result and doesn't stop
    the others. The session is saved after each playlist so folder moves are
    never forgotten.
    """
    fetching = {p.id for p in playlists}
    folders = assign_folders(
        {p.id: p.name for p in playlists},
        {pid: state.folder for pid, state in store.playlists.items()},
        # Folders of playlists not being fetched now still hold their files.
        reserved={s.folder for pid, s in store.playlists.items() if s.folder and pid not in fetching},
    )
    results = []
    for playlist in playlists:
        try:
            tracks = client.playlist_tracks(playlist, progress_for(playlist))
        except SpotifyApiError as exc:
            results.append(FetchResult(playlist, error=str(exc)))
            continue
        state = store.playlists.setdefault(playlist.id, PlaylistState(name=playlist.name, selected=True))
        folder = prepare_folder(data_dir, folders[playlist.id], state.folder)
        write_setlist(folder, tracks)
        state.folder = folder.name
        store.save()
        results.append(FetchResult(playlist, folder=folder, track_count=len(tracks)))
    return results
