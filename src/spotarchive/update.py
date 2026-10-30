"""Self-update: compare against the version on GitHub and replace the install's files with a fresh download."""

from __future__ import annotations

import importlib.metadata
import io
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

import requests

import spotarchive

GITHUB_REPO = "LargePenguin/spotarchive"
REPO_URL = f"https://github.com/{GITHUB_REPO}"
# The version on GitHub is whatever __version__ says on the main branch.
REMOTE_VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/src/spotarchive/__init__.py"
ARCHIVE_URL = f"{REPO_URL}/archive/refs/heads/main.zip"
VERSION_FILE = Path("src") / "spotarchive" / "__init__.py"

# Set in the relaunched process after an update (and usable to opt out of the
# check that plain `spotarchive` runs).
SKIP_CHECK_ENV = "SPOTARCHIVE_NO_UPDATE_CHECK"

_VERSION_RE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.MULTILINE)


class UpdateError(Exception):
    """The update check or the update itself failed."""


def read_version(source: str) -> str:
    """The ``__version__`` assigned in the text of an ``__init__.py``."""
    match = _VERSION_RE.search(source)
    if not match:
        raise UpdateError("No __version__ found.")
    return match.group(1)


def version_key(version: str) -> tuple[int, ...]:
    """Numeric parts of a version, e.g. ``"1.2.3rc1"`` -> ``(1, 2, 3)``."""
    parts = []
    for part in version.split("."):
        digits = re.match(r"\d+", part)
        if not digits:
            break
        parts.append(int(digits.group()))
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    return version_key(remote) > version_key(local)


def latest_version(http=requests, timeout: float = 10) -> str:
    try:
        response = http.get(REMOTE_VERSION_URL, timeout=timeout)
    except requests.RequestException as exc:
        raise UpdateError(f"Couldn't reach GitHub: {exc}") from exc
    if not response.ok:
        raise UpdateError(f"GitHub answered {response.status_code} {response.reason}.")
    try:
        return read_version(response.text)
    except UpdateError:
        raise UpdateError("Couldn't read the version on GitHub.") from None


def skip_check_requested() -> bool:
    return bool(os.environ.get(SKIP_CHECK_ENV))


def project_root(package_dir: Path | None = None) -> Path | None:
    """The checkout Spotarchive runs from, or ``None`` if it isn't an editable install of one."""
    package_dir = package_dir or Path(spotarchive.__file__).resolve().parent
    root = package_dir.parent.parent
    if (root / VERSION_FILE).is_file() and (root / "pyproject.toml").is_file():
        return root
    return None


def download_archive(http=requests, timeout: float = 60) -> bytes:
    """The zip of the repository's ``main`` branch."""
    try:
        response = http.get(ARCHIVE_URL, timeout=timeout)
    except requests.RequestException as exc:
        raise UpdateError(f"Couldn't download the update: {exc}") from exc
    if not response.ok:
        raise UpdateError(f"GitHub answered {response.status_code} {response.reason}.")
    return response.content


def extract_archive(data: bytes, root: Path) -> None:
    """Write every file of a GitHub branch zip over the same path in ``root``.

    Files that exist only locally (settings, data, the virtualenv) stay where
    they are; they aren't part of the repository.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UpdateError("The downloaded update isn't a valid zip file.") from exc
    with archive:
        # GitHub wraps everything in a single "<repo>-<branch>/" folder.
        entries = [(info, PurePosixPath(info.filename).parts[1:]) for info in archive.infolist() if not info.is_dir()]
        entries = [(info, parts) for info, parts in entries if parts and parts[0] != ".git"]
        if not any(parts == VERSION_FILE.parts for _, parts in entries):
            raise UpdateError("The downloaded update doesn't look like Spotarchive.")
        root = root.resolve()
        targets = []
        for info, parts in entries:
            if ".." in parts or PurePosixPath(*parts).is_absolute() or ":" in parts[0]:
                raise UpdateError(f"The downloaded update contains an unsafe path: {info.filename}")
            targets.append((info, root.joinpath(*parts)))
        for info, target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Write beside the target first so a failure never leaves a half-written file.
            temp = target.with_name(target.name + ".update-new")
            temp.write_bytes(archive.read(info))
            mode = (info.external_attr >> 16) & 0o777
            if mode and os.name == "posix":
                temp.chmod(mode)
            os.replace(temp, target)


def install_update(root: Path, run=subprocess.run, http=requests) -> str:
    """Download the latest version over the checkout at ``root``; returns the new version.

    Dependencies are reinstalled only when ``pyproject.toml`` changed: the
    install is editable, so the new code is used as is. Files removed from the
    repository since the installed version are left behind.
    """
    if (root / ".git").is_file():
        raise UpdateError(f"{root} is a git worktree; update the main checkout instead.")

    pyproject = root / "pyproject.toml"
    before = pyproject.read_bytes() if pyproject.is_file() else None
    extract_archive(download_archive(http=http), root)

    if pyproject.read_bytes() != before:
        extras = _installed_extras()
        target = f"{root}[{','.join(extras)}]" if extras else str(root)
        if run([sys.executable, "-m", "pip", "install", "--editable", target]).returncode != 0:
            raise UpdateError(
                "The code was updated, but reinstalling the dependencies failed. "
                "Run the install script again to repair the install."
            )

    return read_version((root / VERSION_FILE).read_text(encoding="utf-8"))


def _installed_extras() -> list[str]:
    """The optional dependency groups that are installed, so the reinstall keeps them."""
    extras = []
    for extra, package in (("dev", "pytest"), ("somedl", "somedl")):
        try:
            importlib.metadata.distribution(package)
        except importlib.metadata.PackageNotFoundError:
            continue
        extras.append(extra)
    return extras
