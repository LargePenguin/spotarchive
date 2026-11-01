"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import requests

from spotarchive import __version__, config, ui, update
from spotarchive.backends import BackendUnavailable, DownloadBackend, get_backend
from spotarchive.config import Config, ConfigError
from spotarchive.download import PlaylistDownload, download_playlist, selected_folders
from spotarchive.fetch import fetch_setlists, update_selection
from spotarchive.meta import PlaylistMeta, fix_playlist_metadata
from spotarchive.session import SessionError, SessionStore
from spotarchive.spotify.auth import (
    AuthError,
    LoginRequired,
    SessionTokenProvider,
    interactive_login,
)
from spotarchive.spotify.client import QueryOutdated, RateLimited, SpotifyApiError, SpotifyClient
from spotarchive.youtube import auth as youtube_auth

# Profiles of the login browser windows, next to session.json.
BROWSER_PROFILE_DIRNAME = "browser-profile"
YOUTUBE_PROFILE_DIRNAME = "browser-profile-youtube"
LOGIN_SITES = ("spotify", "youtube")
YOUTUBE_LOGIN_HINT = "You can log in to YouTube any time with `spotarchive -l youtube`."
# The tests replace this.
youtube_login = youtube_auth.interactive_login


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
        epilog=(
            "With no options: check for updates, log in if needed (offering the optional YouTube "
            "login), fetch, download, then fix the metadata."
        ),
    )
    parser.add_argument(
        "-l", "--login", nargs="?", const=True, metavar="SITE",
        help="log in to SITE (spotify or youtube) in a browser window; asks which without SITE",
    )
    parser.add_argument("-f", "--fetch", action="store_true", help="choose playlists and rebuild their setlist.csv files")
    parser.add_argument(
        "-n", "--noselect", action="store_true",
        help="with --fetch: skip the selection and reuse the previous one (-fn)",
    )
    parser.add_argument(
        "-d", "--download", action="store_true",
        help="download the songs of the fetched setlists that aren't there yet",
    )
    parser.add_argument(
        "-m", "--meta", action="store_true",
        help="fix the metadata (tags) of the downloaded songs (-dm: download, then fix)",
    )
    parser.add_argument(
        "-c", "--config", type=Path, metavar="FILE",
        help=f"configuration file to use (default: {config.CONFIG_FILENAME} in the project folder)",
    )
    parser.add_argument(
        "-u", "--update", action="store_true",
        help="update Spotarchive to the latest version on GitHub",
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


def run_login(store: SessionStore, site: str | bool, browser: str = "") -> None:
    """``--login [SITE]``: log in to *site*, or ask which (``True``)."""
    if site is True:
        site = choose_login_site()
    if site == "youtube":
        run_youtube_login(store, browser)
    else:
        run_spotify_login(store, browser)


def choose_login_site() -> str:
    if not ui.can_prompt():
        raise CliError("Say which site to log in to: `spotarchive -l spotify` or `spotarchive -l youtube`.")
    site = ui.choose_one(
        "Which site do you want to log in to?",
        [("Spotify", "spotify"), ("YouTube (lets age-restricted songs download)", "youtube")],
    )
    if site is None:
        raise KeyboardInterrupt
    return site


def run_spotify_login(store: SessionStore, browser: str = "") -> None:
    ui.info("Opening a browser for the Spotify login...")
    store.spotify = interactive_login(
        store.path.parent / BROWSER_PROFILE_DIRNAME, on_status=ui.info, browser=browser or None
    )
    # Keep the login even if the next request fails (e.g. rate limiting).
    store.save()
    user = make_client(store).current_user()
    store.spotify.user_id = user.get("id")
    store.spotify.display_name = user.get("display_name") or user.get("id")
    store.save()
    ui.ok(f"Logged in to Spotify as {store.spotify.display_name}.")


def run_youtube_login(store: SessionStore, browser: str = "") -> None:
    ui.info("Opening a browser for the YouTube login...")
    store.youtube = youtube_login(
        store.path.parent / YOUTUBE_PROFILE_DIRNAME, on_status=ui.info, browser=browser or None
    )
    # Someone who logged in wants to hear about it when the login expires.
    store.offer_youtube_login = True
    store.save()
    ui.ok("Logged in to YouTube; age-restricted songs can be downloaded now.")


def offer_youtube_login(store: SessionStore, browser: str = "") -> None:
    """Plain runs: offer the optional YouTube login when there's no working one.

    A failed YouTube login doesn't stop the run; downloads go on without it.
    """
    if youtube_auth.is_logged_in(store.youtube) or not store.offer_youtube_login or not ui.can_prompt():
        return
    if store.youtube is not None:
        ui.warn("The saved YouTube login has expired.")
    answer = ui.choose_one(
        "Log in to YouTube too? Some age-restricted songs only download with a login.",
        [("Yes, log in now", "yes"), ("No, not now", "no"), ("No, and don't ask again", "never")],
        instruction="(arrow keys: move, enter: confirm; `spotarchive -l youtube` logs in any time)",
    )
    if answer is None:
        raise KeyboardInterrupt
    if answer == "never":
        store.offer_youtube_login = False
        store.save()
    if answer != "yes":
        ui.info(YOUTUBE_LOGIN_HINT)
        return
    try:
        run_youtube_login(store, browser)
    except AuthError as exc:
        ui.warn(f"The YouTube login didn't work ({exc}); continuing without it. {YOUTUBE_LOGIN_HINT}")


def ensure_login(store: SessionStore, browser: str = "") -> None:
    """Run the login helper unless the saved login still works."""
    if store.spotify is None:
        run_spotify_login(store, browser)
        return
    try:
        make_client(store).current_user()
    except (RateLimited, QueryOutdated):
        # The login is fine; logging in again wouldn't help.
        raise
    except (AuthError, SpotifyApiError) as exc:
        ui.warn(f"The saved Spotify login doesn't work ({exc}).")
        run_spotify_login(store, browser)


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


def make_backend(settings: Config) -> DownloadBackend:
    try:
        return get_backend(settings.backend, **settings.options_for(settings.backend))
    except TypeError as exc:
        # An option the backend doesn't take.
        raise ConfigError(f"{settings.path}: bad setting in [backends.{settings.backend}]: {exc}") from exc


def run_download(store: SessionStore, data_dir: Path, settings: Config) -> None:
    folders = selected_folders(store, data_dir)
    if not folders:
        raise CliError("No playlists have been fetched yet; run `spotarchive --fetch` first.")
    backend = make_backend(settings)
    backend.check()
    for warning in backend.warnings():
        ui.warn(warning)
    if youtube_auth.is_logged_in(store.youtube):
        backend.use_youtube_login(store.youtube)
    elif store.youtube is not None:
        ui.warn(
            "The saved YouTube login has expired, so age-restricted songs may fail; "
            "run `spotarchive -l youtube` to log in again."
        )

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


def run_meta(store: SessionStore, data_dir: Path, settings: Config) -> None:
    folders = selected_folders(store, data_dir)
    if not folders:
        raise CliError("No playlists have been fetched yet; run `spotarchive --fetch` first.")
    backend = None
    if settings.metadata_source == "backend":
        backend = make_backend(settings)
        if backend.can_tag:
            backend.check()
            ui.info(f"Fixing metadata with {backend.name}...")
        else:
            ui.info(f"The {backend.name} backend can't tag files; using the setlists' metadata instead.")
            backend = None
    if backend is None:
        ui.info("Fixing metadata from the setlists...")

    failed_songs = unfetched = 0
    for name, folder in folders:
        try:
            result = _fix_with_progress(backend, name, folder)
        except FileNotFoundError as exc:
            unfetched += 1
            ui.error(f"{name}: {exc}")
            continue
        failed_songs += len(result.failed)
        summary = f"{name}: {result.updated} fixed, {result.unchanged} already fine"
        if result.failed:
            ui.warn(f"{summary}, {len(result.failed)} failed")
        else:
            ui.ok(summary)

    problems = []
    if failed_songs:
        problems.append(f"{failed_songs} songs' metadata couldn't be fixed.")
    if unfetched:
        problems.append(f"{unfetched} playlists have no setlist; run `spotarchive --fetch` first.")
    if problems:
        raise CliError(" ".join(problems))


def _fix_with_progress(backend: DownloadBackend | None, name: str, folder: Path) -> PlaylistMeta:
    failures = []
    with ui.progress() as bar:
        task = bar.add_task(name, total=None)

        def on_start(song, i, n):
            bar.update(task, completed=i - 1, total=n)

        def on_done(song, error):
            bar.advance(task)
            if error:
                failures.append(f"{song.label}: {error}")

        result = fix_playlist_metadata(name, folder, backend, on_start, on_done)
        done = result.updated + result.unchanged + len(result.failed)
        bar.update(task, completed=done, total=done)
    for failure in failures:
        ui.warn(failure)
    return result


def run_update() -> None:
    try:
        latest = update.latest_version()
    except update.UpdateError as exc:
        raise CliError(f"Couldn't check for updates: {exc}") from exc
    if not update.is_newer(latest, __version__):
        ui.ok(f"Spotarchive {__version__} is up to date.")
        return
    _install_update(latest)


def _install_update(latest: str) -> None:
    root = update.project_root()
    if root is None:
        raise CliError(
            f"Spotarchive {latest} is available, but this install isn't an editable checkout, so it can't "
            f"update itself. Run the install script to update: {update.REPO_URL}#quick-start"
        )
    ui.info(f"Updating Spotarchive {__version__} -> {latest} in {root}...")
    try:
        installed = update.install_update(root)
    except update.UpdateError as exc:
        raise CliError(f"Update failed: {exc}") from exc
    ui.ok(f"Updated Spotarchive to {installed}.")


def check_for_update() -> bool:
    """The update check of a plain run; returns whether an update was installed.

    Never fails the run: a failed check or update carries on with the
    installed version.
    """
    if update.skip_check_requested():
        return False
    try:
        latest = update.latest_version(timeout=5)
    except update.UpdateError as exc:
        ui.warn(f"Couldn't check for updates ({exc}); continuing.")
        return False
    if not update.is_newer(latest, __version__):
        return False
    ui.info(f"Spotarchive {latest} is available (you have {__version__}).")
    if update.project_root() is None:
        ui.warn(f"This install can't update itself; run the install script to update: {update.REPO_URL}#quick-start")
        return False
    if not ui.can_prompt():
        ui.info("Run `spotarchive --update` to install it.")
        return False
    answer = ui.confirm("Install the update now?")
    if answer is None:
        raise KeyboardInterrupt
    if not answer:
        return False
    try:
        _install_update(latest)
    except CliError as exc:
        ui.warn(f"{exc} Continuing with {__version__}.")
        return False
    return True


def relaunch(argv: list[str]) -> int:
    """Run the updated code in a new process; this one still has the old code loaded."""
    env = {**os.environ, update.SKIP_CHECK_ENV: "1"}
    return subprocess.run([sys.executable, "-m", "spotarchive", *argv], env=env).returncode


def main(argv: list[str] | None = None, data_dir: Path | None = None) -> int:
    """Run the CLI; *data_dir* overrides the configured data folder (for tests)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if isinstance(args.login, str):
        args.login = args.login.lower()
        if args.login not in LOGIN_SITES:
            parser.error(f"unknown login site {args.login!r}: use `-l spotify` or `-l youtube`")
    if args.noselect and not args.fetch:
        parser.error("--noselect only works together with --fetch (e.g. -fn)")
    if args.update and (args.login or args.fetch or args.download or args.meta):
        parser.error("--update can't be combined with other options")
    plain = not (args.login or args.fetch or args.download or args.meta)

    try:
        if args.update:
            run_update()
            return 0
        if plain and check_for_update():
            return relaunch(sys.argv[1:] if argv is None else argv)
        settings = config.load(args.config)
        data_dir = settings.data_dir if data_dir is None else data_dir
        store = SessionStore.load(data_dir)
        if args.login:
            run_login(store, args.login, settings.login_browser)
        if plain:
            ensure_login(store, settings.login_browser)
            offer_youtube_login(store, settings.login_browser)
            run_fetch(store, data_dir, noselect=False)
            # With nothing selected, fetching has already said so.
            if not selected_folders(store, data_dir):
                return 0
            args.download = args.meta = True
        elif args.fetch:
            run_fetch(store, data_dir, args.noselect)
        download_error = None
        if args.download:
            try:
                run_download(store, data_dir, settings)
            except CliError as exc:
                if not args.meta:
                    raise
                # Still fix the songs that did download.
                download_error = exc
        if args.meta:
            try:
                run_meta(store, data_dir, settings)
            except CliError as exc:
                if download_error:
                    raise CliError(f"{download_error} {exc}") from exc
                raise
        if download_error:
            raise download_error
    except KeyboardInterrupt:
        ui.warn("Cancelled.")
        return 130
    except LoginRequired as exc:
        ui.error(f"{exc} Run `spotarchive --login spotify` to log in.")
        return 1
    except RateLimited as exc:
        ui.error(rate_limit_message(exc))
        return 1
    except (AuthError, BackendUnavailable, ConfigError, SessionError, SpotifyApiError, CliError) as exc:
        ui.error(str(exc))
        return 1
    except requests.RequestException as exc:
        ui.error(f"Couldn't reach Spotify: {exc}")
        return 1
    return 0
