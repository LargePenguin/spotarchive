"""Colored terminal output, playlist checkboxes and progress bars."""

from __future__ import annotations

import sys

from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn

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
