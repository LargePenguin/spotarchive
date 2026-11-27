# Configuration

Spotarchive reads its settings from `spotarchive.toml`, a [TOML](https://toml.io/) file in the project folder (the clone, `~/spotarchive` by default). The [install script](quickstart.md#1-install) creates it from [`spotarchive.example.toml`](../spotarchive.example.toml), which lists every setting with its default. Every setting is optional: anything you leave out, or a missing file, means the default.

`spotarchive.toml` is yours: it isn't tracked by git, so updating Spotarchive never overwrites it. When a new version adds settings, compare your file with `spotarchive.example.toml`.

```toml
data_dir = "data"
import_dir = "Imports"

[download]
backend = "somedl"
fallback = "ytdlp"
threads = 0

[metadata]
source = "backend"

[backends.somedl]
executable = ""
audio_format = ""
flat = true
extra_args = []

[backends.ytdlp]
executable = ""
audio_format = "mp3"
search_results = 5
length_tolerance = 30
extra_args = []
```

To use another file, pass it with `--config` (`-c`), for example `spotarchive -d --config ~/music/spotarchive.toml`. A file given this way must exist.

When Spotarchive isn't installed from a clone (for example with `pip install` into a virtualenv), there is no project folder: it looks for `spotarchive.toml` in the current directory instead.

## Settings

### `data_dir`

The download folder: it holds `session.json` (your [login](authentication.md#what-is-stored) and playlist selection) and one [folder per playlist](fetching.md#folders). A relative path is relative to the folder you run `spotarchive` in; `~` stands for your home folder. Default: `"data"`.

```toml
data_dir = "~/Music/spotarchive"
```

Moving `data_dir` doesn't move existing files: move the old folder's contents yourself, or you'll have to log in and fetch again.

### `import_dir`

The folder [imported links](downloading.md#importing-a-link) (`spotarchive -d LINK`) go to. A relative path is relative to [`data_dir`](#data_dir); an absolute path, including one starting with `~`, is used as it is. Missing folders are created. Default: `"Imports"`.

```toml
import_dir = "~/Music/imported"
```

> [!TIP]
> On Windows, write paths with forward slashes (`"D:/Music/imported"`) or in single quotes (`'D:\Music\imported'`): in a double-quoted TOML string, a backslash starts an escape sequence.

- It can't be `data_dir` itself or a folder that contains it.
- If it is inside `data_dir`, the folder directly in `data_dir` that is or holds it is never used for a Spotify playlist: see [the import folder and playlists](downloading.md#the-import-folder-and-playlists).
- Changing it doesn't move earlier imports. Move the old folder's contents yourself, including the hidden `.downloads.json` files, or importing the same link again downloads another copy.

### `[login]`

| Setting | Default | Meaning |
| --- | --- | --- |
| `browser` | `""` | Browser for the [login windows](authentication.md#which-browser-is-used) (Spotify and YouTube): `"chrome"`, `"edge"`, `"brave"`, `"vivaldi"`, `"opera"` or `"chromium"` (case-insensitive). Empty: the first of Chrome, Edge and Playwright's bundled Chromium that is found. A named browser that isn't installed is an error, with no fallback. |

### `[download]`

| Setting | Default | Meaning |
| --- | --- | --- |
| `backend` | `"somedl"` | The [download backend](backends.md) that sources the songs: `"somedl"` or `"ytdlp"`. |
| `fallback` | `"ytdlp"` | The [fallback backend](backends.md#fallback-backend), tried for every song the main backend can't download. `""`: no fallback. Must differ from `backend`. Setting `backend = "ytdlp"` without a `fallback` line means no fallback. |
| `threads` | `0` | How many songs to [download at once](downloading.md#several-songs-at-once). `0`: half of the threads your CPU can run at once. `1`: [one at a time](downloading.md#one-song-at-a-time), showing the backend's own output. `spotarchive --threads N` (`-t N`) overrides it for one run. |

### `[metadata]`

| Setting | Default | Meaning |
| --- | --- | --- |
| `source` | `"backend"` | Where [metadata cleanup](metadata.md) takes the tags from: `"backend"` (the download helper's own metadata) or `"setlist"` (Spotify's metadata from `setlist.csv`). See [choosing a source](metadata.md#choosing-a-source). |

### `[backends.<name>]`

Settings for one backend; only the tables of the backends chosen in `[download]` (`backend` and `fallback`) are used. An empty string (`""`) means the backend's default.

#### `[backends.somedl]`

| Setting | Default | Meaning |
| --- | --- | --- |
| `executable` | `""` | Path to the `somedl` program. Empty: look next to Spotarchive's Python, then on your `PATH`. |
| `audio_format` | `""` | Audio format passed as `--format`: `"mp3"`, `"m4a"`, `"opus"`, `"vorbis"`, `"flac"`, `"best"`, ... Empty: the format from [SomeDL's own config](https://github.com/ChemistryGull/SomeDL#post-install) (`mp3` unless you changed it). |
| `flat` | `true` | Folder structure. `true`: every song directly in its playlist folder, named `Artist - Song`. `false`: SomeDL's own output template (from its config file, e.g. `{album_artist}/{album}/{artist} - {song}`), inside the playlist folder. See [how Spotarchive runs SomeDL](backends.md#how-spotarchive-runs-it). |
| `extra_args` | `[]` | Extra command-line options for every download, e.g. `["--sleep", "5"]` to pause between songs. |

Switching `flat` or `audio_format` doesn't redownload anything: songs already there are still [recognized](downloading.md#skipping-songs-you-already-have).

#### `[backends.ytdlp]`

| Setting | Default | Meaning |
| --- | --- | --- |
| `executable` | `""` | Path to the `yt-dlp` program. Empty: look next to Spotarchive's Python, then on your `PATH`. |
| `audio_format` | `"mp3"` | Audio format passed as `--audio-format`: `"mp3"`, `"m4a"`, `"opus"`, `"vorbis"`, `"flac"`, `"alac"`, `"aac"`, `"wav"` or `"best"` (whatever YouTube has, not converted). The setlist's tags can't be written to `wav` and `aac` files. |
| `search_results` | `5` | How many YouTube search results to look through for a video of the right length. |
| `length_tolerance` | `30` | Seconds a video may be shorter or longer than the song on Spotify. `0`: any length (the first result). See [how Spotarchive runs yt-dlp](backends.md#how-spotarchive-runs-it-1). |
| `extra_args` | `[]` | Extra command-line options for every download, e.g. `["--sleep-requests", "1"]`. yt-dlp's own config files are ignored, so put options here. |

[Link imports](downloading.md#importing-a-link) always use yt-dlp, with `executable`, `audio_format` and `extra_args` from this table.

## Errors

Spotarchive checks the file before doing anything and stops with a message naming the problem, for example:

```text
✘ /home/<your-username>/spotarchive/spotarchive.toml: unknown setting 'sorce' in metadata
```

Misspelled settings are reported rather than ignored. A setting a backend doesn't know (in `[backends.somedl]` or `[backends.ytdlp]`), or a bad value for one, is reported when that backend is first needed.

Back to the [documentation index](README.md).
