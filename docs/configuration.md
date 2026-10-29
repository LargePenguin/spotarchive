# Configuration

Spotarchive reads its settings from `spotarchive.toml`, a [TOML](https://toml.io/) file in the project folder (the clone, `~/spotarchive` by default). The [install script](quickstart.md#1-install) creates it from [`spotarchive.example.toml`](../spotarchive.example.toml), which lists every setting with its default. Every setting is optional: anything you leave out, or a missing file, means the default.

`spotarchive.toml` is yours: it isn't tracked by git, so updating Spotarchive never overwrites it. When a new version adds settings, compare your file with `spotarchive.example.toml`.

```toml
data_dir = "data"

[download]
backend = "somedl"

[metadata]
source = "backend"

[backends.somedl]
executable = ""
audio_format = ""
flat = true
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

### `[download]`

| Setting | Default | Meaning |
| --- | --- | --- |
| `backend` | `"somedl"` | The [download backend](backends.md) that sources the songs. Currently only `"somedl"`. |

### `[metadata]`

| Setting | Default | Meaning |
| --- | --- | --- |
| `source` | `"backend"` | Where [metadata cleanup](metadata.md) takes the tags from: `"backend"` (the download helper's own metadata) or `"setlist"` (Spotify's metadata from `setlist.csv`). See [choosing a source](metadata.md#choosing-a-source). |

### `[backends.<name>]`

Settings for one backend; only the table of the backend chosen in `[download]` is used. An empty string (`""`) means the backend's default.

#### `[backends.somedl]`

| Setting | Default | Meaning |
| --- | --- | --- |
| `executable` | `""` | Path to the `somedl` program. Empty: look next to Spotarchive's Python, then on your `PATH`. |
| `audio_format` | `""` | Audio format passed as `--format`: `"mp3"`, `"m4a"`, `"opus"`, `"vorbis"`, `"flac"`, `"best"`, ... Empty: the format from [SomeDL's own config](https://github.com/ChemistryGull/SomeDL#post-install) (`mp3` unless you changed it). |
| `flat` | `true` | Folder structure. `true`: every song directly in its playlist folder, named `Artist - Song`. `false`: SomeDL's own output template (from its config file, e.g. `{album_artist}/{album}/{artist} - {song}`), inside the playlist folder. See [how Spotarchive runs SomeDL](backends.md#how-spotarchive-runs-it). |
| `extra_args` | `[]` | Extra command-line options for every download, e.g. `["--sleep", "5"]` to pause between songs. |

Switching `flat` or `audio_format` doesn't redownload anything: songs already there are still [recognized](downloading.md#skipping-songs-you-already-have).

## Errors

Spotarchive checks the file before doing anything and stops with a message naming the problem, for example:

```text
error: /home/<your-username>/spotarchive/spotarchive.toml: unknown setting 'sorce' in metadata
```

Misspelled settings are reported rather than ignored. A setting a backend doesn't know (in `[backends.somedl]`) is reported when that backend is first needed.

Back to the [documentation index](README.md).
