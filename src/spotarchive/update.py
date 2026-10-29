"""Self-update: compare against the version on GitHub and update a git install in place."""

from __future__ import annotations

import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests

import spotarchive

GITHUB_REPO = "LargePenguin/spotarchive"
REPO_URL = f"https://github.com/{GITHUB_REPO}"
# The version on GitHub is whatever __version__ says on the main branch.
REMOTE_VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/src/spotarchive/__init__.py"
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
    """The git clone Spotarchive runs from, or ``None`` if it wasn't installed from one."""
    package_dir = package_dir or Path(spotarchive.__file__).resolve().parent
    root = package_dir.parent.parent
    if (root / VERSION_FILE).is_file() and (root / "pyproject.toml").is_file() and (root / ".git").exists():
        return root
    return None


def install_update(root: Path, run=subprocess.run) -> str:
    """Pull the latest version into the clone at ``root``; returns the new version.

    Dependencies are reinstalled only when ``pyproject.toml`` changed: the
    install is editable, so the pulled code is used as is.
    """
    if (root / ".git").is_file():
        raise UpdateError(f"{root} is a git worktree; update the main checkout instead.")
    git = shutil.which("git")
    if git is None:
        raise UpdateError("git is needed to update Spotarchive; install it and try again.")

    def git_run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        # No credential prompts: the repository is public.
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        result = run([git, "-C", str(root), *args], capture_output=True, text=True, env=env)
        if check and result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()
            raise UpdateError(f"`git {' '.join(args)}` failed: {output or f'exit code {result.returncode}'}")
        return result

    before = git_run("rev-parse", "HEAD").stdout.strip()
    git_run("pull", "--ff-only")
    after = git_run("rev-parse", "HEAD").stdout.strip()
    if before == after:
        raise UpdateError(
            f"`git pull` in {root} brought in nothing new; check that the clone is on the main branch."
        )

    changed = git_run("diff", "--quiet", before, after, "--", "pyproject.toml", check=False)
    if changed.returncode != 0:
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
