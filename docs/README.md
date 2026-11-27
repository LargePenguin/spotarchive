# Spotarchive documentation

> [!NOTE]
> Spotarchive is in early development. Installing, updating, logging in, fetching playlists, downloading, metadata cleanup and the configuration file work.

## Contents

- [Quickstart](quickstart.md) — install, first login, first fetch and download
- [Authentication](authentication.md) — the browser login, what is stored and how tokens are refreshed
- [Fetching playlists](fetching.md) — choosing playlists, folder names and the `setlist.csv` format
- [Downloading](downloading.md) — downloading the setlists, several songs at once, skipping songs you already have, failed songs, importing a link, fixing a wrong match, the log file
- [Download backends](backends.md) — SomeDL and yt-dlp, the fallback backend, how they are run, writing a new backend
- [Metadata cleanup](metadata.md) — fixing the songs' tags from the download helper or the setlist, and the tags of imported links
- [Configuration](configuration.md) — `spotarchive.toml`: download folder, backend and fallback, download threads, metadata source and backend settings
- [Updating](updating.md) — `--update`, the update check of a plain run, and failed updates
