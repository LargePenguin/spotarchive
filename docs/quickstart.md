# Quickstart

> [!WARNING]
> Spotarchive is still in early development. If you encounter any problems, please open an issue.

## Legal note

Spotarchive never acquires music from Spotify. The only data taken from Spotify is information about the contents of your playlists. Audio is sourced through a download backend of your choice (currently only SomeDL is implemented; please feel free to request/implement other backends and open a PR)

## Requirements

- Python 3.10 or newer
- Linux or Windows
- A web browser for the login step
- [SomeDL](https://github.com/ChemistryGull/SomeDL) (the default download backend) and [FFmpeg](https://ffmpeg.org/). Both should be fetched by the included install scripts.

## 1. Install

You need Python and git. Then run one command.

**Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/LargePenguin/spotarchive/main/install.sh | bash
```

**Windows** (PowerShell):

```powershell
irm https://raw.githubusercontent.com/LargePenguin/spotarchive/main/install.ps1 | iex
```

The install script:

1. clones Spotarchive into `~/spotarchive` (`%USERPROFILE%\spotarchive` on Windows), or pulls the latest version if the clone is already there;
2. creates a virtualenv `.venv` in that folder and installs Spotarchive with all its Python dependencies, including SomeDL, the default [download backend](backends.md), and the Deno runtime its downloader yt-dlp needs for YouTube;
3. downloads the Chromium browser used for [logging in](authentication.md#which-browser-is-used);
4. installs [FFmpeg](https://ffmpeg.org/), which SomeDL needs, if it's missing. It asks first, then uses your package manager (apt, dnf, pacman, zypper or apk) or winget on Windows;
5. creates the [configuration file](configuration.md) `spotarchive.toml` in that folder from `spotarchive.example.toml`, unless it already exists;
6. puts the `spotarchive` command on your `PATH`. On Linux it's linked into `~/.local/bin`. On Windows it goes into `%LOCALAPPDATA%\spotarchive\bin`, which is added to your user `PATH`, so open a new terminal afterwards.

Running it again repairs or updates the install. If you already have a clone, run `./install.sh` or `powershell -ExecutionPolicy Bypass -File install.ps1` inside it to install that clone instead.

> [!TIP]
> Options: `--dir`/`-Dir` (where to clone), `--bin-dir`/`-BinDir` (where the command goes), `--no-browser`/`-NoBrowser` (skip Chromium if you use Chrome or Edge), `--no-ffmpeg`/`-NoFfmpeg`, `--no-link`/`-NoLink`, `--dev`/`-Dev` (test dependencies) and `--yes`/`-Yes` (don't ask). When piping the script, pass Linux options as `bash -s -- --no-browser`. On Windows, set `SPOTARCHIVE_DIR` or `SPOTARCHIVE_BIN_DIR` beforehand instead.

<details>
<summary>Installing by hand</summary>

```bash
git clone https://github.com/LargePenguin/spotarchive.git
cd spotarchive
python -m venv .venv
```

Activate the virtualenv (`source .venv/bin/activate` on Linux, `.venv\Scripts\activate` on Windows), then:

```bash
pip install -e ".[somedl]"
python -m playwright install chromium
```

The first command installs Spotarchive together with SomeDL and Deno. SomeDL also needs [FFmpeg](https://ffmpeg.org/) on your `PATH`; see [installing SomeDL](backends.md#installing-it). The last command downloads the browser used for logging in. You can skip it if Google Chrome or Microsoft Edge is installed. To change settings, copy `spotarchive.example.toml` to `spotarchive.toml` (see [Configuration](configuration.md)).

</details>

Spotarchive keeps its data in a `data/` folder inside the folder you run it from, so run it from wherever your music should go, or set a fixed folder with [`data_dir`](configuration.md#data_dir) in `spotarchive.toml`.

## 2. Log in

```bash
spotarchive --login
```

A browser window opens at Spotify's login page. Sign in; once Spotify forwards you to the web player, Spotarchive closes the window by itself, captures the session and caches it in the data folder. Running plain `spotarchive` starts this step automatically if there is no working login. Never share or commit the session file. Details: [Authentication](authentication.md).


> [!NOTE]
> Spotarchive does not directly capture or save your credentials. Your session info (stored in `data_dir/session.json`) is as important as credentials, so please treat the file as though it did contain raw credentials. Do not upload it; do not include it in any issues; never check it into source control.

## 3. Fetch your playlists

```bash
spotarchive --fetch
```

Choose which playlists to cache. Each gets a folder under the download folder (default `data/`, e.g. `data/MyPlaylistName/`) containing a `setlist.csv` with name, artist, album, release date and date added (plus duration and Spotify link). Use `--fetch --noselect` (`-fn`) to reuse your previous selection. Details: [Fetching playlists](fetching.md).

## 4. Download

```bash
spotarchive --download
```

SomeDL finds each song of your setlists on YouTube Music and saves it into the playlist's folder. Songs already present are skipped, so running it again only downloads what's new or failed before. `spotarchive -fnd` refreshes your setlists and downloads in one go. Details: [Downloading](downloading.md).

## 5. Fix the metadata

```bash
spotarchive --meta
```

Gives the downloaded songs proper tags (title, artists, album, ...), from the download helper (default) or from Spotify's metadata in the setlists. `spotarchive -dm` downloads and fixes in one go. Details: [Metadata cleanup](metadata.md).

## 6. Everything at once

```bash
spotarchive
```

Checks GitHub for a newer version first and asks before installing it. Then it logs in if needed, fetches (asking which playlists), downloads and fixes the metadata.

To update at any time, run `spotarchive --update` (`-u`). Details: [Updating](updating.md).

## Next steps

See [Configuration](configuration.md) to change the download folder, the audio format or where the tags come from.

Back to the [documentation index](README.md).
