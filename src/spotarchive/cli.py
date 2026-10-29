"""Command-line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

import requests

from spotarchive import __version__, ui
from spotarchive.fetch import fetch_setlists, update_selection
from spotarchive.session import DEFAULT_DATA_DIR, SessionError, SessionStore
from spotarchive.spotify.auth import (
    AuthError,
    LoginRequired,
    SessionTokenProvider,
    interactive_login,
)
from spotarchive.spotify.client import SpotifyApiError, SpotifyClient


# Profile of the login browser window, next to session.json.
BROWSER_PROFILE_DIRNAME = "browser-profile"


class CliError(Exception):
    """A user-facing error that ends the run."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spotarchive",
        description=(
            "Cache metadata about your Spotify playlists into per-playlist "
            "setlist.csv files. No audio is ever taken from Spotify."
        ),
        epilog="With no options: log in if needed, then fetch.",
    )
    parser.add_argument("-l", "--login", action="store_true", help="log in to Spotify in a browser window")
    parser.add_argument("-f", "--fetch", action="store_true", help="choose playlists and rebuild their setlist.csv files")
    parser.add_argument(
        "-n", "--noselect", action="store_true",
        help="with --fetch: skip the selection and reuse the previous one (-fn)",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def make_client(store: SessionStore) -> SpotifyClient:
    if store.spotify is None:
        raise LoginRequired("Not logged in to Spotify.")
    return SpotifyClient(SessionTokenProvider(store), user_agent=store.spotify.user_agent)


def run_login(store: SessionStore) -> None:
    ui.info("Opening a browser for the Spotify login...")
    store.spotify = interactive_login(store.path.parent / BROWSER_PROFILE_DIRNAME, on_status=ui.info)
    user = make_client(store).current_user()
    store.spotify.user_id = user.get("id")
    store.spotify.display_name = user.get("display_name") or user.get("id")
    store.save()
    ui.ok(f"Logged in to Spotify as {store.spotify.display_name}.")


def ensure_login(store: SessionStore) -> None:
    """Run the login helper unless the saved login still works."""
    if store.spotify is None:
        run_login(store)
        return
    try:
        make_client(store).current_user()
    except (AuthError, SpotifyApiError) as exc:
        ui.warn(f"The saved Spotify login doesn't work ({exc}).")
        run_login(store)


def run_fetch(store: SessionStore, data_dir: Path, noselect: bool) -> None:
    client = make_client(store)
    ui.info("Loading your playlists...")
    playlists = client.playlists()

    if noselect:
        selected_ids = store.selected_ids()
        if not selected_ids:
            raise CliError("No previous playlist selection; run `spotarchive --fetch` without --noselect first.")
    else:
        selected_ids = ui.choose_playlists(playlists, preselected=store.selected_ids())
        if selected_ids is None:
            raise KeyboardInterrupt

    for name in update_selection(store, playlists, selected_ids):
        ui.warn(f"Playlist {name!r} is no longer in your library; skipping it.")
    store.save()

    chosen = [p for p in playlists if store.playlists[p.id].selected]
    if not chosen:
        ui.warn("No playlists selected; nothing to fetch.")
        return

    with ui.progress() as bar:
        def progress_for(playlist):
            task = bar.add_task(playlist.name, total=playlist.total)

            def update(done, total):
                bar.update(task, completed=done, total=total)
            return update

        results = fetch_setlists(client, store, data_dir, chosen, progress_for)

    failed = 0
    for result in results:
        if result.error:
            failed += 1
            ui.error(f"{result.playlist.name}: {result.error}")
        else:
            ui.ok(f"{result.playlist.name}: {result.track_count} songs -> {result.folder}")
    if failed:
        raise CliError(f"{failed} of {len(results)} playlists couldn't be fetched.")


def main(argv: list[str] | None = None, data_dir: Path = DEFAULT_DATA_DIR) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.noselect and not args.fetch:
        parser.error("--noselect only works together with --fetch (e.g. -fn)")

    try:
        store = SessionStore.load(data_dir)
        if args.login:
            run_login(store)
        if args.fetch:
            run_fetch(store, data_dir, args.noselect)
        if not (args.login or args.fetch):
            ensure_login(store)
            run_fetch(store, data_dir, noselect=False)
    except KeyboardInterrupt:
        ui.warn("Cancelled.")
        return 130
    except LoginRequired as exc:
        ui.error(f"{exc} Run `spotarchive --login` to log in.")
        return 1
    except (AuthError, SessionError, SpotifyApiError, CliError) as exc:
        ui.error(str(exc))
        return 1
    except requests.RequestException as exc:
        ui.error(f"Couldn't reach Spotify: {exc}")
        return 1
    return 0
