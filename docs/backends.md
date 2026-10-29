# Download backends

Spotarchive doesn't download anything itself. It hands each song of a setlist to a *download backend*, a separate download helper behind a small interface, so the helper can be swapped without touching the rest of the tool. The default and currently only backend is [SomeDL](#somedl).

## SomeDL

[SomeDL](https://github.com/ChemistryGull/SomeDL) searches YouTube Music for each song, downloads the audio with [yt-dlp](https://github.com/yt-dlp/yt-dlp) and tags it with metadata from YouTube, MusicBrainz, Genius and Deezer.

### Installing it

Install it into the same environment as Spotarchive:

```bash
pip install -e ".[somedl]"     # from a clone of Spotarchive
```

or on its own, for example with [pipx](https://pipx.pypa.io/): `pipx install somedl`. Spotarchive looks for the `somedl` program next to the Python running Spotarchive first, then on your `PATH`.

SomeDL also needs:

- **[FFmpeg](https://ffmpeg.org/)** (required) to convert the audio. Spotarchive checks for it before downloading. See SomeDL's [FFmpeg guide](https://github.com/ChemistryGull/SomeDL/blob/main/docs/how_to_install_ffmpeg.md).
- **[Deno](https://docs.deno.com/runtime/getting_started/installation/)** (recommended): yt-dlp works better with it and warns without it.

If SomeDL or FFmpeg is missing, `spotarchive --download` stops with a message saying which one, before downloading anything.

### How Spotarchive runs it

SomeDL runs once per song, inside the playlist folder, as:

```bash
somedl --here --skip-file-check -- "First Artist - Song Title"
```

- The search is the song's first artist and title from `setlist.csv`.
- `--here` saves the file in the playlist folder as `Artist - Song`, overriding the output folder and file name template from your SomeDL config, so every song lands where Spotarchive expects it.
- `--skip-file-check` makes SomeDL download the song even if it thinks a similar file exists: Spotarchive has already [decided the song is missing](downloading.md#skipping-songs-you-already-have).

Everything else comes from [SomeDL's own config file](https://github.com/ChemistryGull/SomeDL#post-install) (`somedl --generate-config`), for example the audio format (`mp3` by default), lyrics and cover art settings. Spotarchive's own configuration file, where these can be set per backend, is planned.

SomeDL reports success even when a song couldn't be found, so Spotarchive decides by checking that a new audio file (`.mp3`, `.m4a`, `.opus`, `.ogg` or `.flac`) appeared in the folder.

## Writing a new backend

A backend is a subclass of `DownloadBackend` in [`src/spotarchive/backends/base.py`](../src/spotarchive/backends/base.py). Keep everything specific to your helper inside its own module in `src/spotarchive/backends/`.

```python
from pathlib import Path

from spotarchive.backends.base import BackendUnavailable, DownloadBackend, DownloadFailed, Song


class ExampleBackend(DownloadBackend):
    name = "example"                     # shown in messages and stored in .downloads.json
    shows_own_output = False             # True if the helper prints its own progress
    audio_extensions = frozenset({".mp3"})

    def check(self) -> None:
        # Called once before any download. Raise BackendUnavailable with a
        # helpful message if the helper can't run (not installed, etc.).
        ...

    def download(self, song: Song, folder: Path) -> list[Path]:
        # Download one song into `folder` and return the new audio files.
        # Raise DownloadFailed if nothing could be downloaded; the other
        # songs are still tried.
        ...
```

Then register it in `BACKENDS` in [`src/spotarchive/backends/__init__.py`](../src/spotarchive/backends/__init__.py).

What a backend gets and must do:

- `Song` carries the setlist row: `name`, `artists` (a tuple, first artist first), `album`, `duration_ms`, `isrc` and `spotify_url`. Use them to find the song; never download from Spotify.
- `download` is only called for songs that are missing. Spotarchive does the skipping and keeps the [download record](downloading.md#the-downloadsjson-record), so a backend doesn't need to remember anything between runs.
- Return only the audio files created for this song, and only with an extension listed in `audio_extensions`: these are what the skip check looks for.
- Put files in `folder`. Subfolders work too, as long as `download` returns the files' paths.
- With `shows_own_output = True` the helper may write to the terminal; Spotarchive prints a `[n/total]` line before each song instead of drawing a progress bar.

Tests for a backend must not download real audio: fake the helper (see `tests/test_somedl.py`, which replaces `subprocess.run`).

Back to the [documentation index](README.md).
