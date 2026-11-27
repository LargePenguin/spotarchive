"""Download the songs of the selected playlists' setlists through a backend.

Each playlist folder gets a ``.downloads.json`` record mapping every song
(by its Spotify link) to the files the backend produced for it. Helpers name
files after what they found, which doesn't always match Spotify's names, so
this record is what lets later runs skip songs that are already there.

Several songs can download at once (``threads``); the backend calls then run
in worker threads, while the records are only written from the calling one.

Wherever a backend is taken, a list of them may be given instead: a song is
then tried with each in turn until one downloads it (the main backend, then
the fallback), and the record notes which one did.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence, Union

from spotarchive.backends import DownloadBackend, DownloadEvents, DownloadFailed, Song
from spotarchive.downloadlog import DownloadLog
from spotarchive.session import SessionStore
from spotarchive.setlist import SETLIST_FILENAME, read_setlist

RECORD_FILENAME = ".downloads.json"
RECORD_VERSION = 1

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# One backend, or several to try in turn.
Backends = Union[DownloadBackend, Sequence[DownloadBackend]]

# (song, position, number of songs to download)
StartCallback = Callable[[Song, int, int], None]
# (song, new files or None, error or None)
DoneCallback = Callable[[Song, "list[Path] | None", "str | None"], None]


@dataclass
class PlaylistDownload:
    name: str
    folder: Path
    total: int = 0
    already_present: int = 0
    downloaded: int = 0
    failed: list[tuple[Song, str]] = field(default_factory=list)
    # Songs still to download, and the folder's download record.
    pending: list[Song] = field(default_factory=list, repr=False)
    record: dict[str, dict] = field(default_factory=dict, repr=False)


@dataclass(eq=False)
class SongDownload:
    """One song to download, and how it went."""

    playlist: PlaylistDownload
    song: Song
    # Position among the playlist's pending songs (1-based), and their number.
    position: int
    count: int
    # The new files, once downloaded; None while running or when it failed.
    files: list[Path] | None = None
    # The backend that downloaded them.
    backend: DownloadBackend | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.files is not None


class DownloadListener:
    """Follows the downloads, e.g. to display them. The defaults do nothing.

    Every method but :meth:`finished` may be called from a worker thread when
    several songs download at once.
    """

    def started(self, job: SongDownload) -> None: ...

    def progress(self, job: SongDownload, fraction: float) -> None: ...

    def warning(self, job: SongDownload, message: str) -> None: ...

    def error(self, job: SongDownload, message: str) -> None: ...

    def retrying(self, job: SongDownload, failed: DownloadBackend, reason: str, next: DownloadBackend) -> None:
        """*failed* couldn't download the song; *next* tries it now."""

    def finished(self, job: SongDownload) -> None:
        """The song is done and recorded (``job.ok``) or failed; called from the calling thread."""


def selected_folders(store: SessionStore, data_dir: Path) -> list[tuple[str, Path]]:
    """The selected playlists that have been fetched, as (name, folder)."""
    return [
        (state.name, data_dir / state.folder)
        for state in store.playlists.values()
        if state.selected and state.folder
    ]


def load_record(folder: Path) -> dict[str, dict]:
    path = folder / RECORD_FILENAME
    if not path.exists():
        return {}
    try:
        songs = json.loads(path.read_text(encoding="utf-8")).get("songs", {})
    except (OSError, ValueError, AttributeError):
        # Only a cache: losing it means falling back to the file name check.
        return {}
    return songs if isinstance(songs, dict) else {}


def save_record(folder: Path, songs: dict[str, dict]) -> None:
    text = json.dumps({"version": RECORD_VERSION, "songs": songs}, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f"{RECORD_FILENAME}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, folder / RECORD_FILENAME)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def as_chain(backends: Backends) -> list[DownloadBackend]:
    """*backends* as a list, in the order to try them."""
    return [backends] if isinstance(backends, DownloadBackend) else list(backends)


def audio_extensions(backends: Backends) -> frozenset[str]:
    """The file types any of *backends* produces."""
    return frozenset().union(*(b.audio_extensions for b in as_chain(backends)))


def setlist_songs(folder: Path) -> list[Song]:
    """The songs in the folder's setlist, in order, without duplicates."""
    songs: dict[str, Song] = {}
    for row in read_setlist(folder):
        song = Song.from_row(row)
        if song.name:
            songs.setdefault(song.key, song)
    return list(songs.values())


def normalize(stem: str) -> str:
    return " ".join(_INVALID_CHARS.sub("_", stem).split()).casefold()


def expected_stems(song: Song) -> set[str]:
    """File names (without extension) a helper would likely give *song*."""
    if not song.artists:
        return {normalize(song.name)}
    artist_forms = {song.artists[0], ", ".join(song.artists), " & ".join(song.artists), "; ".join(song.artists)}
    return {normalize(f"{artist} - {song.name}") for artist in artist_forms}


def find_present(songs: list[Song], folder: Path, record: dict[str, dict],
                 extensions: frozenset[str]) -> dict[str, list[str]]:
    """Map the key of every song already in *folder* to its files.

    A song counts as present when a file recorded for it still exists, or
    when an audio file is named like ``Artist - Song`` (files from before
    the record existed, or added by hand), also in subfolders.
    """
    by_stem: dict[str, str] = {}
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder)
        if p.is_file() and p.suffix.lower() in extensions and not rel.parts[0].startswith("."):
            by_stem.setdefault(normalize(p.stem), rel.as_posix())

    present: dict[str, list[str]] = {}
    for song in songs:
        recorded = [f for f in record.get(song.key, {}).get("files", []) if (folder / f).is_file()]
        if recorded:
            present[song.key] = recorded
            continue
        matches = sorted({by_stem[s] for s in expected_stems(song) if s in by_stem})
        if matches:
            present[song.key] = matches
    return present


def record_present(folder: Path, record: dict[str, dict], present: dict[str, list[str]]) -> None:
    """Write the files :func:`find_present` found back to the record, if they changed."""
    changed = False
    for key, files in present.items():
        entry = record.get(key, {})
        if entry.get("files") != files:
            # Other files than the recorded ones: whoever downloaded or tagged
            # those didn't make these.
            kept = {k: v for k, v in entry.items() if k not in ("backend", "tagged", "override")}
            record[key] = {**kept, "files": files}
            changed = True
    if changed:
        save_record(folder, record)


def prepare_playlist(backend: Backends, name: str, folder: Path) -> PlaylistDownload:
    """Work out which songs of *folder*'s setlist still have to be downloaded.

    Songs found by file name are written to the record right away.
    """
    if not (folder / SETLIST_FILENAME).is_file():
        raise FileNotFoundError(f"{folder / SETLIST_FILENAME} doesn't exist; fetch the playlist first.")
    songs = setlist_songs(folder)
    record = load_record(folder)
    present = find_present(songs, folder, record, audio_extensions(backend))
    record_present(folder, record, present)
    return PlaylistDownload(
        name, folder, total=len(songs), already_present=len(present),
        pending=[s for s in songs if s.key not in present], record=record,
    )


class _SongEvents(DownloadEvents):
    """Passes what the backend reports for one song on to the log and the listener.

    With a *prefix* (several backends to try) every message is marked with
    it, and errors count as warnings: another backend may still download the
    song, and if none does, one error sums up why.
    """

    def __init__(self, job: SongDownload, listener: DownloadListener, log: DownloadLog | None, show_output: bool,
                 prefix: str = ""):
        super().__init__(show_output)
        self.job = job
        self.listener = listener
        self.log = log
        self.prefix = prefix
        # The last error the backend reported itself.
        self.last_error: str | None = None

    def progress(self, fraction: float) -> None:
        self.listener.progress(self.job, max(0.0, min(fraction, 1.0)))

    def warning(self, message: str) -> None:
        if self.prefix:
            message = f"{self.prefix}: {message}"
        self._warning(message)

    def error(self, message: str) -> None:
        self.last_error = message
        if self.prefix:
            self._warning(f"{self.prefix}: {message}")
        else:
            self._error(message)

    def _warning(self, message: str) -> None:
        self.job.warnings.append(message)
        if self.log:
            self.log.warning(message, self.job.playlist.name, self.job.song.label)
        self.listener.warning(self.job, message)

    def _error(self, message: str) -> None:
        self.job.errors.append(message)
        if self.log:
            self.log.error(message, self.job.playlist.name, self.job.song.label)
        self.listener.error(self.job, message)


def download_all(
    backend: Backends,
    playlists: list[PlaylistDownload],
    threads: int = 1,
    listener: DownloadListener | None = None,
    log: DownloadLog | None = None,
) -> None:
    """Download the pending songs of *playlists*, *threads* songs at a time.

    *backend* may be several backends, tried in turn for every song. With
    one thread the backend may show its own output; with more it must keep
    quiet. A song that fails is reported (and logged) and doesn't stop
    the others. The record is saved after every song, so an interrupted run
    loses nothing.
    """
    listener = listener or DownloadListener()
    show_output = threads <= 1
    jobs = [
        SongDownload(playlist, song, i, len(playlist.pending))
        for playlist in playlists
        for i, song in enumerate(playlist.pending, start=1)
    ]

    chain = as_chain(backend)

    def work(job: SongDownload) -> SongDownload:
        listener.started(job)
        failures: list[tuple[str, str]] = []
        for i, candidate in enumerate(chain):
            prefix = candidate.name if len(chain) > 1 else ""
            events = _SongEvents(job, listener, log, show_output, prefix)
            try:
                job.files = candidate.download(job.song, job.playlist.folder, events)
            except DownloadFailed as exc:
                reason = str(exc) or "download failed"
                # The backend may already have reported it as an error.
                if events.last_error != reason:
                    events.error(reason)
                failures.append((candidate.name, reason))
                if i + 1 < len(chain):
                    listener.retrying(job, candidate, reason, chain[i + 1])
                continue
            job.backend = candidate
            return job
        if len(chain) > 1:
            events._error("; ".join(f"{name}: {reason}" for name, reason in failures))
        return job

    if threads <= 1:
        for job in jobs:
            _finish(work(job), listener)
        return

    pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="download")
    try:
        running = {pool.submit(work, job) for job in jobs}
        while running:
            # A timeout so Ctrl+C gets through on Windows too.
            done, running = wait(running, timeout=0.2, return_when=FIRST_COMPLETED)
            for future in done:
                _finish(future.result(), listener)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def _finish(job: SongDownload, listener: DownloadListener) -> None:
    playlist = job.playlist
    if job.files is None:
        playlist.failed.append((job.song, job.errors[-1] if job.errors else "download failed"))
    else:
        playlist.record[job.song.key] = {
            "label": job.song.label,
            "backend": job.backend.name,
            "files": [p.relative_to(playlist.folder).as_posix() for p in job.files],
        }
        save_record(playlist.folder, playlist.record)
        playlist.downloaded += 1
    listener.finished(job)


class _CallbackListener(DownloadListener):
    def __init__(self, on_start: StartCallback, on_done: DoneCallback):
        self.on_start = on_start
        self.on_done = on_done

    def started(self, job: SongDownload) -> None:
        self.on_start(job.song, job.position, job.count)

    def finished(self, job: SongDownload) -> None:
        self.on_done(job.song, job.files, None if job.ok else job.playlist.failed[-1][1])


def download_playlist(
    backend: Backends,
    name: str,
    folder: Path,
    on_start: StartCallback = lambda song, i, n: None,
    on_done: DoneCallback = lambda song, files, error: None,
    threads: int = 1,
    log: DownloadLog | None = None,
) -> PlaylistDownload:
    """Download the songs of *folder*'s setlist that aren't there yet (see :func:`download_all`)."""
    playlist = prepare_playlist(backend, name, folder)
    download_all(backend, [playlist], threads, _CallbackListener(on_start, on_done), log)
    return playlist
