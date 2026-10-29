from pathlib import Path

import pytest

from spotarchive import config
from spotarchive.backends import get_backend
from spotarchive.backends.somedl import SomeDLBackend
from spotarchive.config import Config, ConfigError

EXAMPLE = Path(__file__).resolve().parents[1] / "spotarchive.example.toml"


def write(tmp_path, text):
    path = tmp_path / "spotarchive.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_default_file_means_defaults():
    settings = config.load()
    assert settings == Config()
    assert (settings.data_dir, settings.backend, settings.metadata_source) == (Path("data"), "somedl", "backend")


def test_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="Can't read"):
        config.load(tmp_path / "nope.toml")


def test_example_file_matches_the_defaults():
    settings = config.load(EXAMPLE)
    assert (settings.data_dir, settings.backend, settings.metadata_source) == (
        Config.data_dir, Config.backend, Config.metadata_source)
    backend = get_backend(settings.backend, **settings.options_for(settings.backend))
    default = SomeDLBackend()
    assert vars(backend) == vars(default)


def test_reads_every_setting(tmp_path):
    settings = config.load(write(tmp_path, """
data_dir = "~/music"
[download]
backend = "somedl"
[metadata]
source = "setlist"
[backends.somedl]
executable = ""
audio_format = "opus"
flat = false
extra_args = ["--sleep", "5"]
"""))
    assert settings.data_dir == Path("~/music").expanduser()
    assert settings.metadata_source == "setlist"
    assert settings.options_for("somedl") == {"audio_format": "opus", "flat": False, "extra_args": ["--sleep", "5"]}
    assert settings.options_for("other") == {}


@pytest.mark.parametrize("text, message", [
    ("data_dir = [", "Can't read"),
    ("dta_dir = 'x'", "unknown setting 'dta_dir'"),
    ("data_dir = ''", "data_dir must be a non-empty string"),
    ("[metadata]\nsource = 'spotify'", "metadata.source must be one of"),
    ("[metadata]\nsorce = 'setlist'", "unknown setting 'sorce' in metadata"),
    ("download = 'somedl'", "download must be a table"),
    ("[backends]\nsomedl = 1", "backends.somedl must be a table"),
])
def test_bad_files(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        config.load(write(tmp_path, text))
