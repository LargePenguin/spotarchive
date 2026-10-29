# Fetching playlists

Fetching reads the contents of your Spotify playlists and writes them to a `setlist.csv` per playlist. Only metadata is read from Spotify; no audio is ever taken from it.

```bash
spotarchive --fetch        # choose playlists, then rebuild their setlists
spotarchive -fn            # same, reusing the previous choice (--fetch --noselect)
```

You need a working [login](authentication.md) first.

## Choosing playlists

`spotarchive --fetch` lists your Liked Songs plus every playlist you own or follow, with a checkbox each:

- <kbd>Space</kbd> toggles a playlist, <kbd>A</kbd> toggles all of them, <kbd>Enter</kbd> confirms.
- Your previous choice is pre-ticked.
- <kbd>Ctrl</kbd>+<kbd>C</kbd> cancels without changing anything.

The choice is saved in `session.json` in the download folder, so `spotarchive -fn` can refresh the same playlists without asking. Playlists that have disappeared from your library since are reported and dropped from the choice.

Playlists inside folders are listed too. Spotarchive reads your library through the same API the web player uses ([why](#where-the-data-comes-from)), so every playlist you can open in the web player can be fetched. A playlist that can't be read (for example, one that was deleted) is reported as failed; the rest are still fetched.

## Folders

Every selected playlist gets a folder in the download folder (default `data/` in the current directory):

```text
data/
├── session.json
├── Liked Songs/
│   └── setlist.csv
└── Road Trip/
    └── setlist.csv
```

[Downloading](downloading.md) later puts the songs into the same folders.

Folder names are the playlist names, adjusted to be valid on both Linux and Windows:

- `< > : " / \ | ? *` and control characters become `_`, and repeated whitespace collapses to one space.
- Trailing dots and spaces are removed, and names are capped at 100 characters.
- Names Windows reserves (`CON`, `NUL`, `COM1`, ...) get a leading `_`.
- Two playlists whose names would clash (ignoring case) are told apart by appending the playlist ID, e.g. `Chill (abc123)`. A playlist that already had the plain name keeps it.

When you rename a playlist on Spotify, its folder is renamed on the next fetch, together with anything already downloaded into it.

## The `setlist.csv` format

A UTF-8 CSV file with a header row and one row per song, in playlist order. Each fetch rebuilds the file from scratch.

| Column | Example | Notes |
| --- | --- | --- |
| `name` | `Example Song` | Song title |
| `artist` | `First Artist; Second Artist` | All artists, separated by `; ` (artist names can contain commas) |
| `album` | `Example Album` | |
| `release_date` | `2001-02-03` | Album release date; Spotify sometimes only knows the year (`2001`) or month (`2001-02`). Empty for Liked Songs |
| `date_added` | `2024-05-06T07:08:09Z` | When the song was added to the playlist (UTC) |
| `duration_ms` | `180000` | Length in milliseconds |
| `isrc` | | International Standard Recording Code. Currently always empty: the web player's API doesn't return it. The column is kept so the file layout stays stable |
| `spotify_url` | `https://open.spotify.com/track/...` | Empty for local files |

Podcast episodes and songs that are no longer available on Spotify are left out.

## Where the data comes from

Spotarchive reads your library through the API Spotify's own web player uses, with the token the web player issues (see [Authentication](authentication.md#how-tokens-are-refreshed)). Spotify's public Web API (`api.spotify.com`) rate-limits those tokens so heavily that it's unusable for this.

The web player identifies each of its requests by a hash defined in its JavaScript. Spotarchive reads these hashes from the web player each time it refreshes the token. If Spotify changes them in between, Spotarchive notices, refreshes and tries again. If a request still fails after that, Spotify has changed the web player in a way Spotarchive doesn't know yet. You'll see an error asking you to update Spotarchive.

Back to the [documentation index](README.md).
