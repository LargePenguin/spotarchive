# Download backends

Spotarchive doesn't download anything itself. It hands each song of a setlist to a *download backend*, a separate download helper behind a small interface, so the helper can be swapped without touching the rest of the tool. Two backends are available:

- [SomeDL](#somedl) (`somedl`, the default): searches YouTube Music and tags the files with rich metadata.
- [yt-dlp](#yt-dlp) (`ytdlp`, the default [fallback](#fallback-backend)): searches all of YouTube and tags the files with the setlist's metadata.

## Fallback backend

A song the main backend can't download is tried again with the *fallback* backend, right away, in the same run. This is set in [`[download]`](configuration.md#download) of the configuration file:

```toml
[download]
backend = "somedl"    # tried first
fallback = "ytdlp"    # tried for the songs the first one can't download; "" for none
```

By default SomeDL comes first and yt-dlp catches what it misses, for example:

- songs that aren't on YouTube Music but are on regular YouTube (SomeDL: `Song could not be downloaded, error on initial lookup.`);
- songs SomeDL can't collect metadata for (SomeDL: `A critical exception occured when fetching the metadata for this song! ...`).

While a song is retried, the main backend's errors are logged as warnings starting with its name (`somedl: ...`), since the song may still download. The song only counts as failed if the fallback can't download it either; its failure reason then lists both (`somedl: ...; ytdlp: ...`). The [download record](downloading.md#the-downloadsjson-record) notes which backend downloaded each song, and [metadata cleanup](metadata.md#backend) uses that: a song yt-dlp downloaded keeps the setlist's tags instead of being sent to SomeDL.

If the fallback can't run (for example yt-dlp or FFmpeg is missing), Spotarchive warns and downloads with the main backend alone. Settings for the fallback go into its own [`[backends.<name>]`](configuration.md#backendsname) table, as for the main backend. Setting `backend = "ytdlp"` without a `fallback` line means no fallback (yt-dlp alone); set `fallback = "somedl"` to try SomeDL second.

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

SomeDL runs once per song, in a temporary folder inside the playlist folder (`.sd-*`, a short name on purpose), as:

```bash
somedl --here --skip-file-check -- "First Artist - Song Title"
```

- The search is the song's first artist and title from `setlist.csv`.
- `--here` saves the file in the playlist folder as `Artist - Song`, overriding the output folder and file name template from your SomeDL config, so every song lands where Spotarchive expects it. With [`flat = false`](configuration.md#backendssomedl) Spotarchive passes `--output .` instead: SomeDL then uses the output template from its config, inside the playlist folder.
- `--skip-file-check` makes SomeDL download the song even if it thinks a similar file exists: Spotarchive has already [decided the song is missing](downloading.md#skipping-songs-you-already-have).

The audio format (`--format`), the path to `somedl` and extra options can be set in [`[backends.somedl]`](configuration.md#backendssomedl) of Spotarchive's configuration file. Everything else comes from [SomeDL's own config file](https://github.com/ChemistryGull/SomeDL#post-install) (`somedl --generate-config`), for example the audio format when Spotarchive doesn't set one (`mp3` by default), lyrics and cover art settings.

With a [YouTube login](#youtube-login), `--cookies <file>` is added too.

SomeDL also [fixes metadata](metadata.md#backend) for files it didn't download, through `somedl import`.

SomeDL reports success even when a song couldn't be found, so Spotarchive decides by checking that an audio file (`.mp3`, `.m4a`, `.opus`, `.ogg` or `.flac`) appeared in the temporary folder. Everything SomeDL wrote there is then moved into the playlist folder (keeping subfolders) and the temporary folder is removed. The temporary folder is what lets several songs download into the same playlist folder at once without mixing up their files.

#### Long file names and paths

SomeDL names the file after the track it found, which can be very long ("Artist - Song (with ... ) (feat. ...)"). Windows allows only 260 characters for a whole path (and 255 for a file name, which most file systems share), and yt-dlp also writes `.part` files next to the final one. Spotarchive keeps within the limits like this, and every time it has to shorten something it says so in a warning (shown and written to the [log file](downloading.md#the-log-file)):

- The temporary folder has a short name, to leave as much room as possible.
- If yt-dlp still can't write its file because the path is too long, Spotarchive tries the song once more in your system's temporary folder, when that path is shorter, and moves the result back.
- When the finished file is moved into the playlist folder, its name is cut to fit the path limit of your operating system, keeping the extension: `File name shortened to fit the path limit: "<old name>" -> "<new name>"`. Only the end of the title is lost; the file is still recognized as downloaded.

If the playlist folder's own path leaves no room for a file name, or a shorter folder isn't available, the song fails with a "path too long" message. Moving the [`data_dir`](configuration.md#data_dir) closer to the root of the drive fixes it. Windows 10 and later can also [lift the 260 character limit](https://learn.microsoft.com/windows/win32/fileio/maximum-file-path-limitation).

Spotarchive reads SomeDL's output as it arrives. Its `WARNING` and `ERROR` lines go to the [log file](downloading.md#the-log-file), and the last error (or warning) of a failed song becomes its failure reason. When [several songs download at once](downloading.md#several-songs-at-once) the output isn't shown. Spotarchive then makes SomeDL draw its live status table into the captured output anyway (through the `FORCE_COLOR` and `TTY_COMPATIBLE` variables of [Rich](https://github.com/Textualize/rich), the library SomeDL prints with), so that it can read the download percentage there for the progress bar. With [one thread](downloading.md#one-song-at-a-time) the output is passed on to your terminal unchanged.

### YouTube login

When you've [logged in to YouTube](authentication.md#logging-in-to-youtube-optional), Spotarchive writes the saved youtube.com cookies to a temporary `cookies.txt` (Netscape format, readable only by your user) for each song and runs SomeDL with `--cookies <that file>`. SomeDL passes it to yt-dlp, which then downloads as your account, so age-restricted songs work. The file is deleted as soon as SomeDL is done with the song. This takes precedence over `cookies_path` and `cookies_from_browser` in SomeDL's own config file; an `--cookies` in [`extra_args`](configuration.md#backendssomedl) still wins over it.

Without a YouTube login nothing changes: SomeDL uses its own cookie settings, if any.

### Troubleshooting

With one thread, SomeDL's own messages appear in between Spotarchive's. With more they aren't shown, but its warnings and errors are in the [log file](downloading.md#the-log-file). The common ones:

- **`yt-dlp needs Deno to work properly`**: Deno isn't installed where yt-dlp looks for it (SomeDL's environment, then your `PATH`). Spotarchive prints a similar warning before downloading. Re-run the [install script](quickstart.md#1-install), or `pip install "yt-dlp[default,deno]"` into the environment that has SomeDL.
- **`unable to download video data: HTTP Error 403: Forbidden`**: YouTube refused the audio stream. This usually means Deno is missing (see above), so yt-dlp couldn't solve YouTube's challenge. Once Deno is installed, an occasional 403 can still happen. The song counts as failed, and the next `spotarchive --download` retries it. If 403s keep coming, update yt-dlp (`pip install -U "yt-dlp[default,deno]"`), since YouTube changes often, or wait a while in case your IP is being [rate limited](downloading.md#when-a-song-fails).
- **`Sign in to confirm your age`** (or another age-restriction error): the song needs a YouTube login; run `spotarchive --login youtube` and download again. If you're already logged in, the login may have been invalidated: log in again. See [Logging in to YouTube](authentication.md#logging-in-to-youtube-optional).
- **`Lyrics: lrclib error: 503`** (or another lrclib error): the lyrics site [lrclib.net](https://lrclib.net/) is down or busy. It's harmless: the song still downloads, without lyrics unless the fallback source (YouTube) has them. To stop using lrclib, set `lyrics_source = "youtube"` and `lyrics_fallback_source = "none"` in SomeDL's config file.

## yt-dlp

[yt-dlp](https://github.com/yt-dlp/yt-dlp) is the downloader SomeDL itself uses. On its own it searches regular YouTube rather than YouTube Music, so it finds uploads, live versions and songs that were never released there. It finds no music metadata, so Spotarchive tags its files with the setlist's: title, artists, album and release date from Spotify (no cover art or lyrics).

### Installing it

It comes with SomeDL: the [install script](quickstart.md#1-install) and the `somedl` extra install `yt-dlp[default,deno]` into Spotarchive's virtualenv. On its own: `pip install "yt-dlp[default,deno]"`. Like SomeDL it needs **FFmpeg** (to extract the audio; checked before downloading) and works best with **Deno** (see [SomeDL's requirements](#installing-it)). Spotarchive looks for the `yt-dlp` program next to the Python running Spotarchive first, then on your `PATH`.

### How Spotarchive runs it

yt-dlp runs once per song, in a temporary folder inside the playlist folder (`.ytdlp-download-*`), roughly as:

```bash
yt-dlp --ignore-config --extract-audio --audio-format mp3 --audio-quality 0 \
  --match-filters "!is_live & duration>=?170 & duration<=?231" --max-downloads 1 \
  --output "Artist - Song.%(ext)s" -- "ytsearch5:First Artist - Song Title"
```

- The search is the song's first artist and title from `setlist.csv`, on YouTube.
- Of the first [`search_results`](configuration.md#backendsytdlp) (5) videos, the first one whose length is within [`length_tolerance`](configuration.md#backendsytdlp) (30 seconds) of the song's length on Spotify is downloaded; live streams are skipped. This keeps out hour-long mixes and short clips. If none fits, the song fails with `no YouTube video within 30 s of the song's length among the first 5 results`. Set `length_tolerance = 0` to take the first result whatever its length.
- The audio is converted to [`audio_format`](configuration.md#backendsytdlp) (`mp3` by default) at the best quality, and saved in the playlist folder as `Artist - Song`, with the setlist's names.
- `--ignore-config`: yt-dlp's own config files aren't read, so the result is the same on every computer. Add options with [`extra_args`](configuration.md#backendsytdlp).
- With a [YouTube login](#youtube-login), `--cookies <file>` is added, written and deleted the same way as for SomeDL.

Right after downloading, the file gets the setlist's tags (with [mutagen](https://mutagen.readthedocs.io/)). If that fails (e.g. `wav`, which mutagen can't tag this way), the song still counts as downloaded and a warning is logged.

yt-dlp's `WARNING:` and `ERROR:` lines go to the [log file](downloading.md#the-log-file), and its `[download]  42%` lines drive the progress bar. With [one thread](downloading.md#one-song-at-a-time) its output is shown in your terminal.

### Imported links

yt-dlp also does every [link import](downloading.md#importing-a-link). It first lists the link (`--dump-single-json --flat-playlist --no-playlist`), then runs once per item, in the same kind of temporary folder, roughly as:

```bash
yt-dlp --ignore-config --extract-audio --audio-format mp3 --audio-quality 0 --no-playlist \
  --match-filters "!is_live & extractor!*=?spotify" --max-downloads 1 \
  --output "download.%(ext)s" --write-info-json --no-write-playlist-metafiles --write-thumbnail \
  -- "https://www.youtube.com/watch?v=..."
```

The file is [tagged](metadata.md#imported-links) from `download.info.json`, FFmpeg crops the thumbnail for the cover art, and the file is then moved into the folder under its final name. Everything else in the temporary folder is deleted. For a page whose items have no links of their own, `--playlist-items N` picks one.

[`spotarchive --overwrite`](downloading.md#fixing-a-wrong-match) downloads your link the same way, as a single item, then replaces the file's name and its title, artist, album and date with the setlist's.


## Writing a new backend

A backend is a subclass of `DownloadBackend` in [`src/spotarchive/backends/base.py`](../src/spotarchive/backends/base.py). Keep everything specific to your helper inside its own module in `src/spotarchive/backends/`.

```python
from pathlib import Path

from spotarchive.backends.base import (
    BackendUnavailable, DownloadBackend, DownloadEvents, DownloadFailed, Song, TaggingFailed,
)
from spotarchive.session import YouTubeSession


class ExampleBackend(DownloadBackend):
    name = "example"                     # shown in messages and stored in .downloads.json
    shows_own_output = False             # True if the helper prints its own progress
    audio_extensions = frozenset({".mp3"})
    can_tag = False                      # True if tag() is implemented
    tags_downloads = False               # True if download() already tags its files

    def __init__(self, quality: str = "high"):
        # Settings from [backends.example] in spotarchive.toml arrive as
        # keyword arguments; give every one a default.
        self.quality = quality

    def check(self) -> None:
        # Called once before downloading or tagging. Raise BackendUnavailable with a
        # helpful message if the helper can't run (not installed, etc.).
        ...

    def warnings(self) -> list[str]:
        # Optional, called after check(): problems that don't stop the
        # downloads (a recommended dependency is missing, ...).
        return []

    def use_youtube_login(self, login: YouTubeSession) -> None:
        # Optional: called before downloading when the user is logged in to
        # YouTube. The default stores it in self.youtube_login.
        super().use_youtube_login(login)

    def download(self, song: Song, folder: Path, events: DownloadEvents | None = None) -> list[Path]:
        # Download one song into `folder` and return the new audio files.
        # Raise DownloadFailed if nothing could be downloaded; the other
        # songs are still tried. Other songs may be downloading at the same
        # time, from other threads. Report through `events` (all optional):
        #   events.progress(0.42)    how far along, 0.0 to 1.0
        #   events.warning("...")    something went wrong, but it carries on
        #   events.error("...")      an error (still raise DownloadFailed if it failed)
        # and only write to the terminal when events.show_output is true.
        events = events or DownloadEvents()
        ...

    def tag(self, song: Song, file: Path) -> None:
        # Optional (with can_tag = True): rewrite the file's metadata with
        # the helper's own. Raise TaggingFailed if it can't.
        ...
```

Then register it in `BACKENDS` in [`src/spotarchive/backends/__init__.py`](../src/spotarchive/backends/__init__.py).

What a backend gets and must do:

- `Song` carries the setlist row: `name`, `artists` (a tuple, first artist first), `album`, `release_date`, `duration_ms`, `isrc` and `spotify_url`. Use them to find the song; never download from Spotify.
- `download` is only called for songs that are missing. A backend can be the main one or the [fallback](#fallback-backend); it doesn't need to know which: when it raises `DownloadFailed`, Spotarchive tries the next one. Spotarchive does the skipping and keeps the [download record](downloading.md#the-downloadsjson-record), so a backend doesn't need to remember anything between runs.
- Return only the audio files created for this song, and only with an extension listed in `audio_extensions`: these are what the skip check looks for.
- Put files in `folder`. Subfolders work too, as long as `download` returns the files' paths.
- Several songs may download at once, each in its own thread (see [several songs at once](downloading.md#several-songs-at-once)), possibly into the same `folder`. So don't find a song's files by looking at what changed in `folder`: download into a temporary folder of your own and move the files over, as the SomeDL backend does (folders starting with `.` are ignored by the skip check). Don't share mutable state between calls without a lock.
- Report progress, warnings and errors through `events` when the helper tells you about them: progress draws the song's progress bar, and warnings and errors go to the [log file](downloading.md#the-log-file) and the song's status line. The `DownloadFailed` message is logged as an error too, unless it's the same as the last `events.error`.
- YouTube login: when the user has [logged in to YouTube](authentication.md#logging-in-to-youtube-optional) (and the login hasn't expired), `use_youtube_login` is called once before the first `download`; the default implementation keeps it in `self.youtube_login` (otherwise `None`). It holds `cookies` (youtube.com cookies as Playwright cookie dicts: `name`, `value`, `domain`, `path`, `expires` in seconds since the epoch or `-1`, `secure`, `httpOnly`) and the `user_agent` of the browser they came from. Pass them to your helper in whatever form it takes. For helpers built on yt-dlp, `login.netscape_cookies()` returns a `cookies.txt` for `--cookies`; `cookie_file` in [`backends/util.py`](../src/spotarchive/backends/util.py) writes it to a private temporary file. Never log the cookies or leave them on disk.
- `tag` is used by [metadata cleanup](metadata.md#backend) with `source = "backend"`: for every song in the folder, unless `tags_downloads` is set and this backend downloaded it, or it was already tagged by it. It gets the setlist row and one file, and must leave the file where it is. With a [fallback backend](#fallback-backend), a song goes to the backend that downloaded it first, then to the other one if that raises `TaggingFailed`. Without `can_tag`, metadata cleanup falls back to the setlist; `write_setlist_tags` in [`tags.py`](../src/spotarchive/tags.py) writes the setlist's tags, for helpers that find no metadata of their own.
- Settings: each key of `[backends.<name>]` in the [configuration file](configuration.md#backendsname) is passed to the constructor as a keyword argument (empty strings are left out, so the default applies). Document them in `docs/configuration.md` and `spotarchive.example.toml`.
- With `shows_own_output = True` the helper may write to the terminal when `events.show_output` is true (one song at a time); Spotarchive then prints a `[n/total]` line before each song instead of drawing a progress bar. When `events.show_output` is false, capture the helper's output instead.

Tests for a backend must not download real audio: fake the helper (see `tests/test_somedl.py` and `tests/test_ytdlp.py`, which replace `subprocess.Popen` and `subprocess.run`).

A backend can also support [link imports](downloading.md#importing-a-link) by setting `can_import = True` and implementing `list_url(url)` and `import_item(item, folder, events)` (see their docstrings in `base.py`; they must refuse Spotify). Imports currently always use the backend named by `IMPORT_BACKEND` in `backends/__init__.py`, which is yt-dlp.

Back to the [documentation index](README.md).
