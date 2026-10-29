"""Command-line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

import requests

from spotarchive import __version__, ui
from spotarchive.backends import BackendUnavailable, DownloadBackend, get_backend
from spotarchive.download import PlaylistDownload, download_playlist, selected_folders
from spotarchive.fetch import fetch_setlists, update_selection
from spotarchive.session import DEFAULT_DATA_DIR, SessionError, SessionStore
from spotarchive.spotify.auth import (
    AuthError,
    LoginRequired,
    SessionTokenProvider,
    interactive_login,
)
from spotarchive.spotify.client import QueryOutdated, RateLimited, SpotifyApiError, SpotifyClient


# Profile of the login browser window, next to session.json.
BROWSER_PROFILE_DIRNAME = "browser-profile"


class CliError(Exception):
    """A user-facing error that ends the run."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spotarchive",
        description=(
            "Cache metadata about your Spotify playlists into per-playlist "
            "setlist.csv files, then source the songs legally through a download "
            "backend. No audio is ever taken from Spotify."
        ),
        epilog="With no options: log in if needed, fetch, then download.",
    )
    parser.add_argument("-l", "--login", action="store_true", help="log in to Spotify in a browser window")
    parser.add_argument("-f", "--fetch", action="store_true", help="choose playlists and rebuild their setlist.csv files")
    parser.add_argument(
        "-n", "--noselect", action="store_true",
        help="with --fetch: skip the selection and reuse the previous one (-fn)",
    )
    parser.add_argument(
        "-d", "--download", action="store_true",
        help="download the songs of the fetched setlists that aren't there yet",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def make_client(store: SessionStore) -> SpotifyClient:
    if store.spotify is None:
        raise LoginRequired("Not logged in to Spotify.")
    return SpotifyClient(
        SessionTokenProvider(store),
        user_agent=store.spotify.user_agent,
        on_wait=lambda s: ui.warn(f"Spotify is rate-limiting requests; retrying in {s:.0f}s..."),
    )


def rate_limit_message(exc: RateLimited) -> str:
    wait = ""
    if exc.retry_after_s:
        wait = f" Spotify asked to wait {_duration(exc.retry_after_s)}."
    return f"Spotify is rate-limiting this login's requests ({exc}).{wait} Try again later."


def _duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 2:
        return f"{seconds:.0f} seconds"
    if minutes < 120:
        return f"about {minutes} minutes"
    return f"about {round(minutes / 60)} hours"


def run_login(store: SessionStore) -> None:
    ui.info("Opening a browser for the Spotify login...")
    store.spotify = interactive_login(store.path.parent / BROWSER_PROFILE_DIRNAME, on_status=ui.info)
    # Keep the login even if the next request fails (e.g. rate limiting).
    store.save()
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
    except (RateLimited, QueryOutdated):
        # The login is fine; logging in again wouldn't help.
        raise
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


def make_backend() -> DownloadBackend:
    return get_backend()


def run_download(store: SessionStore, data_dir: Path) -> None:
    folders = selected_folders(store, data_dir)
    if not folders:
        raise CliError("No playlists have been fetched yet; run `spotarchive --fetch` first.")
    backend = make_backend()
    backend.check()

    failed_songs = unfetched = 0
    for name, folder in folders:
        try:
            if backend.shows_own_output:
                result = _download_showing_backend(backend, name, folder)
            else:
                result = _download_with_progress(backend, name, folder)
        except FileNotFoundError as exc:
            unfetched += 1
            ui.error(f"{name}: {exc}")
            continue
        failed_songs += len(result.failed)
        summary = f"{name}: {result.downloaded} downloaded, {result.already_present} already there"
        if result.failed:
            ui.warn(f"{summary}, {len(result.failed)} failed")
        else:
            ui.ok(summary)

    problems = []
    if failed_songs:
        problems.append(
            f"{failed_songs} songs couldn't be downloaded; run `spotarchive --download` again to retry them."
        )
    if unfetched:
        problems.append(f"{unfetched} playlists have no setlist; run `spotarchive --fetch` first.")
    if problems:
        raise CliError(" ".join(problems))


def _download_showing_backend(backend: DownloadBackend, name: str, folder: Path) -> PlaylistDownload:
    ui.info(f"{name}: checking which songs are missing...")

    def on_start(song, i, n):
        ui.info(f"[{i}/{n}] {song.label}")

    def on_done(song, files, error):
        if error:
            ui.warn(f"{song.label}: {error}")

    return download_playlist(backend, name, folder, on_start, on_done)


def _download_with_progress(backend: DownloadBackend, name: str, folder: Path) -> PlaylistDownload:
    failures = []
    with ui.progress() as bar:
        task = bar.add_task(name, total=None)

        def on_start(song, i, n):
            bar.update(task, completed=i - 1, total=n)

        def on_done(song, files, error):
            bar.advance(task)
            if error:
                failures.append(f"{song.label}: {error}")

        result = download_playlist(backend, name, folder, on_start, on_done)
        attempted = result.downloaded + len(result.failed)
        bar.update(task, completed=attempted, total=attempted)
    for failure in failures:
        ui.warn(failure)
    return result


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
        if args.download:
            run_download(store, data_dir)
        if not (args.login or args.fetch or args.download):
            ensure_login(store)
            run_fetch(store, data_dir, noselect=False)
            # With nothing selected, fetching has already said so.
            if selected_folders(store, data_dir):
                run_download(store, data_dir)
    except KeyboardInterrupt:
        ui.warn("Cancelled.")
        return 130
    except LoginRequired as exc:
        ui.error(f"{exc} Run `spotarchive --login` to log in.")
        return 1
    except RateLimited as exc:
        ui.error(rate_limit_message(exc))
        return 1
    except (AuthError, BackendUnavailable, SessionError, SpotifyApiError, CliError) as exc:
        ui.error(str(exc))
        return 1
    except requests.RequestException as exc:
        ui.error(f"Couldn't reach Spotify: {exc}")
        return 1
    return 0
