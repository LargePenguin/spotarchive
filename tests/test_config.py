from pathlib import Path

import pytest

from spotarchive import config
from spotarchive.backends import get_backend
from spotarchive.backends.somedl import SomeDLBackend
from spotarchive.backends.ytdlp import YtDlpBackend
from spotarchive.config import Config, ConfigError

# Before the autouse fixture in conftest.py pins it to 1.
DEFAULT_THREADS = config.default_threads
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
    assert (settings.data_dir, settings.backend, settings.metadata_source, settings.threads) == (
        Config.data_dir, Config.backend, Config.metadata_source, Config.threads)
    assert settings.fallback == Config.fallback
    assert settings.cache_dir == Config.cache_dir
    backend = get_backend(settings.backend, **settings.options_for(settings.backend))
    default = SomeDLBackend()
    assert vars(backend) == vars(default)
    fallback = get_backend(settings.fallback, **settings.options_for(settings.fallback))
    assert vars(fallback) == vars(YtDlpBackend())


def test_reads_every_setting(tmp_path):
    settings = config.load(write(tmp_path, """
data_dir = "~/music"
cache_dir = "~/music-cache"
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
    assert settings.cache_dir == Path("~/music-cache").expanduser()
    assert settings.cache_path(settings.data_dir) == settings.cache_dir
    assert settings.metadata_source == "setlist"
    assert settings.options_for("somedl") == {"audio_format": "opus", "flat": False, "extra_args": ["--sleep", "5"]}
    assert settings.options_for("other") == {}


@pytest.mark.parametrize("text, message", [
    ("data_dir = [", "Can't read"),
    ("dta_dir = 'x'", "unknown setting 'dta_dir'"),
    ("data_dir = ''", "data_dir must be a non-empty string"),
    ("cache_dir = 1", "cache_dir must be a string"),
    ("[metadata]\nsource = 'spotify'", "metadata.source must be one of"),
    ("[metadata]\nsorce = 'setlist'", "unknown setting 'sorce' in metadata"),
    ("download = 'somedl'", "download must be a table"),
    ("[download]\nthreads = -1", "download.threads must be a whole number"),
    ("[download]\nthreads = 'four'", "download.threads must be a whole number"),
    ("[download]\nthreads = true", "download.threads must be a whole number"),
    ("[backends]\nsomedl = 1", "backends.somedl must be a table"),
    ("[download]\nfallback = 1", "download.fallback must be a string"),
    ("[download]\nfallback = 'somedl'", "download.fallback must be another backend"),
])
def test_bad_files(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        config.load(write(tmp_path, text))


def test_threads_default_to_half_the_cpu(monkeypatch):
    monkeypatch.setattr(config, "default_threads", DEFAULT_THREADS)
    monkeypatch.setattr(config.os, "cpu_count", lambda: 8)
    assert Config().download_threads() == 4
    monkeypatch.setattr(config.os, "cpu_count", lambda: 1)
    assert Config().download_threads() == 1
    monkeypatch.setattr(config.os, "cpu_count", lambda: None)
    assert Config().download_threads() == 1


def test_threads_setting(tmp_path):
    settings = config.load(write(tmp_path, "[download]\nthreads = 3\n"))
    assert settings.threads == 3 and settings.download_threads() == 3


def test_login_browser(tmp_path):
    path = tmp_path / "spotarchive.toml"
    path.write_text('[login]\nbrowser = " Brave "\n')
    assert config.load(path).login_browser == "brave"
    path.write_text('[login]\nbrowser = "msedge"\n')
    assert config.load(path).login_browser == "edge"
    path.write_text('[login]\nbrowser = ""\n')
    assert config.load(path).login_browser == ""


def test_login_browser_unknown(tmp_path):
    path = tmp_path / "spotarchive.toml"
    path.write_text('[login]\nbrowser = "firefox"\n')
    with pytest.raises(ConfigError, match="login.browser"):
        config.load(path)


def test_fallback(tmp_path):
    assert config.load(write(tmp_path, "[download]\nfallback = ''")).fallback == ""
    settings = config.load(write(tmp_path, "[download]\nbackend = 'ytdlp'\nfallback = 'somedl'"))
    assert (settings.backend, settings.fallback) == ("ytdlp", "somedl")
    # Only the backend set to the default fallback: no fallback, not an error.
    assert config.load(write(tmp_path, "[download]\nbackend = 'ytdlp'")).fallback == ""


@pytest.mark.parametrize("text", ["", "cache_dir = ''", "cache_dir = '  '"])
def test_cache_dir_defaults_to_a_folder_in_data_dir(tmp_path, text):
    settings = config.load(write(tmp_path, text))
    assert settings.cache_dir is None
    assert settings.cache_path(tmp_path / "music") == tmp_path / "music" / "cache"
