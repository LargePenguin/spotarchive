# Quickstart

> [!WARNING]
> Spotarchive is still being built. Steps 1–3 work today; downloading (step 4), the all-in-one run's download/metadata/update steps and the install script are planned.

## Legal note

Spotarchive never acquires music from Spotify. The only data taken from Spotify is information about the contents of your playlists. Audio is sourced through a download backend by legal means.

## Requirements

- Python 3.10 or newer
- Linux or Windows
- A web browser for the login step
- [SomeDL](https://github.com/ChemistryGull/SomeDL) (the default download backend; installed by the setup script)

## 1. Install

A single-command install script will fetch dependencies and initialize the project. *(Planned; not available yet.)* Until then, install from a clone:

```bash
git clone https://github.com/LargePenguin/spotarchive.git
cd spotarchive
python -m venv .venv
```

Activate the virtualenv (`source .venv/bin/activate` on Linux, `.venv\Scripts\activate` on Windows), then:

```bash
pip install -e .
python -m playwright install chromium
```

The last command downloads the browser used for logging in. You can skip it if Google Chrome or Microsoft Edge is installed; see [which browser is used](authentication.md#which-browser-is-used).

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

Songs already present in a playlist folder are skipped. Add `--meta` (`-dm` together) to clean up metadata afterwards.

## 5. Everything at once

```bash
spotarchive
```

Checks for updates, logs in if needed, then fetches, downloads and fixes metadata.

## Next steps

Back to the [documentation index](README.md).
