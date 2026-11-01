"""Colored terminal output, playlist checkboxes and progress bars."""

from __future__ import annotations

import sys
import threading

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from spotarchive.spotify.models import Playlist

console = Console(highlight=False)


def info(message: str) -> None:
    console.print(f"[cyan]::[/] {escape(message)}")


# Markers are ASCII so they render on legacy Windows consoles too.
def ok(message: str) -> None:
    console.print(f"[green]ok[/] {escape(message)}")


def warn(message: str) -> None:
    console.print(f"[yellow]![/] {escape(message)}")


def error(message: str) -> None:
    console.print(f"[bold red]error:[/] {escape(message)}")


def can_prompt() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def confirm(question: str, default: bool = True) -> bool | None:
    """Yes/no prompt; returns ``None`` if cancelled."""
    import questionary

    return questionary.confirm(question, default=default).ask()


def choose_playlists(playlists: list[Playlist], preselected: list[str]) -> list[str] | None:
    """Checkbox prompt; returns the chosen IDs, or ``None`` if cancelled."""
    import questionary

    chosen = set(preselected)
    choices = [
        questionary.Choice(title=_playlist_label(p), value=p.id, checked=p.id in chosen)
        for p in playlists
    ]
    return questionary.checkbox(
        "Which playlists should be cached?",
        choices=choices,
        instruction="(space: toggle, a: toggle all, enter: confirm)",
    ).ask()


def _playlist_label(playlist: Playlist) -> str:
    details = []
    if playlist.total is not None:
        details.append(f"{playlist.total} songs")
    if playlist.owner:
        details.append(f"by {playlist.owner}")
    return f"{playlist.name}  ({', '.join(details)})" if details else playlist.name


def progress() -> Progress:
    return Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    )


def _fancy_symbols() -> bool:
    # Legacy Windows consoles (and non-UTF-8 terminals) can't draw these.
    return not console.legacy_windows and console.encoding.lower().replace("-", "") == "utf8"


def counts(downloaded: int, errors: int, warnings: int) -> str:
    """'1 downloaded; 0 errors; 2 warnings' as markup, each number colored when it isn't 0."""
    def part(n: int, color: str, one: str, many: str) -> str:
        return f"[{color if n else 'white'}]{n} {one if n == 1 else many}[/]"
    return "; ".join([
        part(downloaded, "green", "downloaded", "downloaded"),
        part(errors, "red", "error", "errors"),
        part(warnings, "yellow", "warning", "warnings"),
    ])


class DownloadBoard:
    """Songs downloading at once, shown like ``docker pull``.

    Every running song gets a line with a spinner, and a progress bar under
    it while its progress is known. When it's done the bar goes away and the
    line turns into a check mark or a cross with what happened. Methods may
    be called from any thread.
    """

    def __init__(self, bar_width: int = 40):
        self.bar_width = bar_width
        fancy = _fancy_symbols()
        self._spinner = "dots" if fancy else "line"
        self._ok, self._failed = ("\u2714", "\u2718") if fancy else ("+", "x")
        self._lock = threading.Lock()
        # key -> [spinner, progress (0..1) or None]
        self._running: dict[object, list] = {}
        self._live = Live(self, console=console, refresh_per_second=10, transient=True)

    def __enter__(self) -> DownloadBoard:
        self._live.start()
        return self

    def __exit__(self, *exc) -> None:
        self._live.stop()

    def start(self, key: object, label: str) -> None:
        with self._lock:
            self._running[key] = [Spinner(self._spinner, text=Text(label), style="cyan"), None]

    def progress(self, key: object, fraction: float) -> None:
        with self._lock:
            if key in self._running:
                self._running[key][1] = fraction

    def finish(self, key: object, label: str, ok: bool, downloaded: int, errors: int, warnings: int) -> None:
        with self._lock:
            self._running.pop(key, None)
        mark = f"[green]{self._ok}[/]" if ok else f"[bold red]{self._failed}[/]"
        # Printed above the running downloads.
        self._live.console.print(f"{mark} {escape(label)}: {counts(downloaded, errors, warnings)}")

    def __rich__(self) -> Group:
        rows = []
        with self._lock:
            for spinner, fraction in self._running.values():
                rows.append(spinner)
                if fraction is not None:
                    bar = Table.grid(padding=(0, 1))
                    bar.add_row(
                        "  ",
                        ProgressBar(total=1.0, completed=fraction, width=self.bar_width),
                        f"{fraction:>4.0%}",
                    )
                    rows.append(bar)
        return Group(*rows)
