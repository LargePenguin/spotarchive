import io

import pytest
from rich.console import Console

from spotarchive import ui


@pytest.fixture
def out(monkeypatch):
    def use(terminal: bool = False) -> io.StringIO:
        buffer = io.StringIO()
        monkeypatch.setattr(ui, "console", Console(file=buffer, force_terminal=terminal, width=200,
                                                   legacy_windows=False, color_system=None))
        return buffer
    return use


def test_ok_and_error_use_check_mark_and_cross(out):
    buffer = out()
    ui.ok("Fetched [Road Trip]")
    ui.error("Something broke")
    assert buffer.getvalue().splitlines() == ["✔ Fetched [Road Trip]", "✘ Something broke"]


def test_legacy_consoles_get_ascii_marks(out, monkeypatch):
    buffer = out()
    monkeypatch.setattr(ui, "_fancy_symbols", lambda: False)
    ui.ok("fine")
    ui.error("broken")
    assert buffer.getvalue().splitlines() == ["+ fine", "x broken"]
    assert ui.DownloadBoard()._spinner == "line"


def test_working_without_terminal_prints_its_messages(out):
    buffer = out()
    with ui.working("Loading your playlists...") as status:
        status.update("Still loading...")
    assert buffer.getvalue().splitlines() == [":: Loading your playlists...", ":: Still loading..."]


def test_working_on_a_terminal_leaves_nothing_behind(out):
    buffer = out(terminal=True)
    with ui.working("Loading your playlists...") as status:
        status.update("Still loading...")
    ui.ok("Done")
    # The spinner is transient: only what came after it stays.
    assert buffer.getvalue().rstrip().endswith("✔ Done")
    assert ":: " not in buffer.getvalue()
