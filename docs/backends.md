# Download backends

Spotarchive doesn't download anything itself. It hands each song of a setlist to a *download backend*, a separate download helper behind a small interface, so the helper can be swapped without touching the rest of the tool. The default and currently only backend is [SomeDL](#somedl).

## SomeDL

[SomeDL](https://github.com/ChemistryGull/SomeDL) searches YouTube Music for each song, downloads the audio with [yt-dlp](https://github.com/yt-dlp/yt-dlp) and tags it with metadata from YouTube, MusicBrainz, Genius and Deezer.

### Installing it

The [install script](quickstart.md#1-install) installs SomeDL, Deno and FFmpeg for you. To install it by hand, put it into the same environment as Spotarchive:

```bash
pip install -e ".[somedl]"     # from a clone of Spotarchive
```

or on its own, for example with [pipx](https://pipx.pypa.io/): `pipx install somedl`, then `pipx inject somedl "yt-dlp[default,deno]"`. Spotarchive looks for the `somedl` program next to the Python running Spotarchive first, then on your `PATH`.

SomeDL also needs:

- **[FFmpeg](https://ffmpeg.org/)** (required) to convert the audio. Spotarchive checks for it before downloading. See SomeDL's [FFmpeg guide](https://github.com/ChemistryGull/SomeDL/blob/main/docs/how_to_install_ffmpeg.md).
- **[Deno](https://deno.com/)** (strongly recommended): yt-dlp runs YouTube's JavaScript challenges with it. The `somedl` extra installs it into the virtualenv from PyPI (`yt-dlp[default,deno]`), together with yt-dlp's challenge solver; a system-wide [Deno](https://docs.deno.com/runtime/getting_started/installation/) on your `PATH` works too.

If SomeDL or FFmpeg is missing, `spotarchive --download` stops with a message saying which one, before downloading anything. If Deno is missing it only warns, since some songs may still download.

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

### Troubleshooting

SomeDL's own messages appear in between Spotarchive's. The common ones:

- **`yt-dlp needs Deno to work properly`**: Deno isn't installed where yt-dlp looks for it (SomeDL's environment, then your `PATH`). Spotarchive prints a similar warning before downloading. Re-run the [install script](quickstart.md#1-install), or `pip install "yt-dlp[default,deno]"` into the environment that has SomeDL.
- **`unable to download video data: HTTP Error 403: Forbidden`**: YouTube refused the audio stream. This usually means Deno is missing (see above), so yt-dlp couldn't solve YouTube's challenge. Once Deno is installed, an occasional 403 can still happen. The song counts as failed, and the next `spotarchive --download` retries it. If 403s keep coming, update yt-dlp (`pip install -U "yt-dlp[default,deno]"`), since YouTube changes often, or wait a while in case your IP is being [rate limited](downloading.md#when-a-song-fails).
- **`Lyrics: lrclib error: 503`** (or another lrclib error): the lyrics site [lrclib.net](https://lrclib.net/) is down or busy. It's harmless: the song still downloads, without lyrics unless the fallback source (YouTube) has them. To stop using lrclib, set `lyrics_source = "youtube"` and `lyrics_fallback_source = "none"` in SomeDL's config file.

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

    def warnings(self) -> list[str]:
        # Optional, called after check(): problems that don't stop the
        # downloads (a recommended dependency is missing, ...).
        return []

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
