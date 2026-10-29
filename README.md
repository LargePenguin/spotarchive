```text
  ██████  ██▓███   ▒█████  ▄▄▄█████▓ ▄▄▄       ██▀███   ▄████▄   ██░ ██  ██▓ ██▒   █▓▓█████ 
▒██    ▒ ▓██░  ██▒▒██▒  ██▒▓  ██▒ ▓▒▒████▄    ▓██ ▒ ██▒▒██▀ ▀█  ▓██░ ██▒▓██▒▓██░   █▒▓█   ▀ 
░ ▓██▄   ▓██░ ██▓▒▒██░  ██▒▒ ▓██░ ▒░▒██  ▀█▄  ▓██ ░▄█ ▒▒▓█    ▄ ▒██▀▀██░▒██▒ ▓██  █▒░▒███   
  ▒   ██▒▒██▄█▓▒ ▒▒██   ██░░ ▓██▓ ░ ░██▄▄▄▄██ ▒██▀▀█▄  ▒▓▓▄ ▄██▒░▓█ ░██ ░██░  ▒██ █░░▒▓█  ▄ 
▒██████▒▒▒██▒ ░  ░░ ████▓▒░  ▒██▒ ░  ▓█   ▓██▒░██▓ ▒██▒▒ ▓███▀ ░░▓█▒░██▓░██░   ▒▀█░  ░▒████▒
▒ ▒▓▒ ▒ ░▒▓▒░ ░  ░░ ▒░▒░▒░   ▒ ░░    ▒▒   ▓▒█░░ ▒▓ ░▒▓░░ ░▒ ▒  ░ ▒ ░░▒░▒░▓     ░ ▐░  ░░ ▒░ ░
░ ░▒  ░ ░░▒ ░       ░ ▒ ▒░     ░      ▒   ▒▒ ░  ░▒ ░ ▒░  ░  ▒    ▒ ░▒░ ░ ▒ ░   ░ ░░   ░ ░  ░
░  ░  ░  ░░       ░ ░ ░ ▒    ░        ░   ▒     ░░   ░ ░         ░  ░░ ░ ▒ ░     ░░     ░   
      ░               ░ ░                 ░  ░   ░     ░ ░       ░  ░  ░ ░        ░     ░  ░
                                                       ░                         ░          
```

[![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Downloads](https://img.shields.io/github/downloads/LargePenguin/spotarchive/total)](https://github.com/LargePenguin/spotarchive/releases)
[![Stars](https://img.shields.io/github/stars/LargePenguin/spotarchive?style=flat)](https://github.com/LargePenguin/spotarchive/stargazers)
[![Last commit](https://img.shields.io/github/last-commit/LargePenguin/spotarchive)](https://github.com/LargePenguin/spotarchive/commits/main)
[![License](https://img.shields.io/github/license/LargePenguin/spotarchive)](LICENSE)
[![Platforms](https://img.shields.io/badge/platform-linux%20%7C%20windows-lightgrey)](docs/quickstart.md)

Archiver for Spotify and YouTube Music: cache your playlists' metadata, then legally source the songs through a swappable download backend.

> [!WARNING]
> Early development: Spotify login, playlist fetching, downloading, metadata cleanup, the configuration file, the install script and self-updates work.

## Features

- Browser-based Spotify login
- Per-playlist `setlist.csv` metadata caches
- Pluggable download backend (default: [SomeDL](https://github.com/ChemistryGull/SomeDL))
- Smart downloading that skips songs you already have
- Metadata cleanup, from the download helper or from Spotify's metadata
- A single configuration file (`spotarchive.toml`)
- Linux and Windows support

Spotarchive only reads playlist *metadata* from Spotify; it never downloads audio from Spotify.

## Quick start

```bash
# Linux
curl -fsSL https://raw.githubusercontent.com/LargePenguin/spotarchive/main/install.sh | bash
# Windows (PowerShell)
irm https://raw.githubusercontent.com/LargePenguin/spotarchive/main/install.ps1 | iex
```

Then run `spotarchive`. See the [quickstart guide](docs/quickstart.md) and the [documentation index](docs/README.md).
