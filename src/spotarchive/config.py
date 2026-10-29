"""The configuration file, ``spotarchive.toml`` in the project folder.

Every setting is optional; a missing file means the defaults. The install
scripts copy ``spotarchive.example.toml`` (which lists every setting) into
place. See ``docs/configuration.md``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from spotarchive.session import DEFAULT_DATA_DIR

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

CONFIG_FILENAME = "spotarchive.toml"
# Where the metadata cleanup takes the tags from.
METADATA_SOURCES = ("backend", "setlist")

_TOP_LEVEL = {"data_dir", "download", "metadata", "backends"}


class ConfigError(Exception):
    """The configuration file exists but can't be used."""


@dataclass
class Config:
    # The file the settings came from; None when the defaults are used.
    path: Path | None = None
    # Relative paths are relative to the current directory.
    data_dir: Path = DEFAULT_DATA_DIR
    backend: str = "somedl"
    metadata_source: str = "backend"
    # Backend name -> keyword arguments for its constructor.
    backend_options: dict[str, dict] = field(default_factory=dict)

    def options_for(self, backend: str) -> dict:
        # An empty string means "use the backend's default".
        return {k: v for k, v in self.backend_options.get(backend, {}).items() if v != ""}


def default_path() -> Path:
    """``spotarchive.toml`` in the project folder, or the current directory when not run from a clone."""
    project = Path(__file__).resolve().parents[2]
    if (project / "pyproject.toml").is_file():
        return project / CONFIG_FILENAME
    return Path(CONFIG_FILENAME)


def load(path: Path | None = None) -> Config:
    """Read the configuration; *path* must exist when given, the default one may be missing."""
    if path is None:
        path = default_path()
        if not path.is_file():
            return Config()
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Can't read the configuration file {path}: {exc}") from exc
    return parse(raw, Path(path))


def parse(raw: dict, path: Path) -> Config:
    def fail(message: str) -> ConfigError:
        return ConfigError(f"{path}: {message}")

    def table(value, name: str, allowed: set[str] | None = None) -> dict:
        if not isinstance(value, dict):
            raise fail(f"{name} must be a table ([{name}])")
        unknown = sorted(set(value) - allowed) if allowed is not None else []
        if unknown:
            raise fail(f"unknown setting {unknown[0]!r} in {name}")
        return value

    def text(value, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise fail(f"{name} must be a non-empty string")
        return value.strip()

    raw = table(raw, "the file", _TOP_LEVEL)
    config = Config(path=path)
    if "data_dir" in raw:
        config.data_dir = Path(text(raw["data_dir"], "data_dir")).expanduser()
    download = table(raw.get("download", {}), "download", {"backend"})
    if "backend" in download:
        config.backend = text(download["backend"], "download.backend")
    metadata = table(raw.get("metadata", {}), "metadata", {"source"})
    if "source" in metadata:
        source = text(metadata["source"], "metadata.source")
        if source not in METADATA_SOURCES:
            raise fail(f"metadata.source must be one of {', '.join(map(repr, METADATA_SOURCES))}, not {source!r}")
        config.metadata_source = source
    backends = table(raw.get("backends", {}), "backends")
    config.backend_options = {name: dict(table(options, f"backends.{name}")) for name, options in backends.items()}
    return config
