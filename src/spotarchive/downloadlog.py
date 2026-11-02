"""The log of a download run: every warning and error, written as it happens.

One file per run, ``logs/download-<date>-<time>.log`` in the data folder,
created with the first entry (a run without problems leaves no log).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

LOG_DIRNAME = "logs"


class DownloadLog:
    def __init__(self, path: Path):
        self.path = path
        self.warnings = 0
        self.errors = 0
        self._lock = threading.Lock()

    @classmethod
    def for_run(cls, data_dir: Path) -> DownloadLog:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return cls(data_dir / LOG_DIRNAME / f"download-{stamp}.log")

    @property
    def written(self) -> bool:
        return bool(self.warnings or self.errors)

    def warning(self, message: str, playlist: str | None = None, song: str | None = None) -> None:
        self._write("WARNING", message, playlist, song)

    def error(self, message: str, playlist: str | None = None, song: str | None = None) -> None:
        self._write("ERROR", message, playlist, song)

    def _write(self, level: str, message: str, playlist: str | None, song: str | None) -> None:
        where = " / ".join(part for part in (playlist, song) if part)
        lines = str(message).splitlines() or [""]
        entry = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {level:<7} {where or '-'}\n"
        entry += "".join(f"    {line}\n" for line in lines)
        with self._lock:
            if level == "ERROR":
                self.errors += 1
            else:
                self.warnings += 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Opened per entry and closed right away, so nothing is lost if
            # the run is interrupted.
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(entry)
