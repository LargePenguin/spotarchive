# Quickstart

> [!WARNING]
> Spotarchive is still being built. Steps 1–4 work today; metadata cleanup (`--meta`), the all-in-one run's update and metadata steps and the install script are planned.

## Legal note

Spotarchive never acquires music from Spotify. The only data taken from Spotify is information about the contents of your playlists. Audio is sourced through a download backend by legal means.

## Requirements

- Python 3.10 or newer
- Linux or Windows
- A web browser for the login step
- [SomeDL](https://github.com/ChemistryGull/SomeDL) (the default download backend) and [FFmpeg](https://ffmpeg.org/)

## 1. Install

A single-command install script will fetch dependencies and initialize the project. *(Planned; not available yet.)* Until then, install from a clone:

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

The first command installs Spotarchive together with SomeDL, the default [download backend](backends.md). SomeDL also needs [FFmpeg](https://ffmpeg.org/) on your `PATH`; see [installing SomeDL](backends.md#installing-it). The last command downloads the browser used for logging in. You can skip it if Google Chrome or Microsoft Edge is installed; see [which browser is used](authentication.md#which-browser-is-used).

## 2. Log in

```bash
spotarchive --login
```

A browser window opens at Spotify's login page. Sign in; once Spotify forwards you to the web player, Spotarchive closes the window by itself, captures the session and caches it in the data folder. Running plain `spotarchive` starts this step automatically if there is no working login. Never share or commit the session file. Details: [Authentication](authentication.md).

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

Cleaning up metadata afterwards with `--meta` (`-dm` together) is planned.

## 5. Everything at once

```bash
spotarchive
```

Logs in if needed, then fetches (asking which playlists) and downloads. Checking for updates first and fixing metadata at the end are planned.

## Next steps

Back to the [documentation index](README.md).
