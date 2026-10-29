# Quickstart

> [!WARNING]
> Spotarchive is still being built. This page is a stub that outlines the intended setup flow; commands below do not work yet.

## Legal note

Spotarchive never acquires music from Spotify. The only data taken from Spotify is information about the contents of your playlists. Audio is sourced through a download backend by legal means.

## Requirements

- Python 3.10 or newer
- Linux or Windows
- A web browser for the login step
- [SomeDL](https://github.com/ChemistryGull/SomeDL) (the default download backend; installed by the setup script)

## 1. Install

A single-command install script will fetch dependencies and initialize the project. *(Planned; not available yet.)*

## 2. Log in

```bash
spotarchive --login
```

A browser opens at Spotify's login page. After you sign in, Spotarchive captures the session and caches it in the data folder. Running plain `spotarchive` starts this step automatically if there is no working login. Never share or commit the session file.

## 3. Fetch your playlists

```bash
spotarchive --fetch
```

Choose which playlists to cache. Each gets a folder under the download folder (default `data/`, e.g. `data/MyPlaylistName/`) containing a `setlist.csv` with name, artist, album, release date and date added. Use `--fetch --noselect` (`-fn`) to reuse your previous selection.

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
