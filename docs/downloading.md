# Downloading

Downloading turns the `setlist.csv` of every selected playlist into audio files in that playlist's folder. Spotarchive never takes audio from Spotify: the songs are sourced by a [download backend](backends.md), [SomeDL](https://github.com/ChemistryGull/SomeDL) by default, which finds them on YouTube Music.

```bash
spotarchive --download     # download what's missing from the fetched setlists
spotarchive -fd            # fetch (choosing playlists), then download
spotarchive -fnd           # fetch the previous selection, then download
```

You need to have [fetched](fetching.md) the playlists first, and the backend has to be installed (see [SomeDL](backends.md#somedl)). Downloading itself doesn't talk to Spotify, so it works without a login once the setlists exist.

Plain `spotarchive` downloads too, right after fetching.

## What gets downloaded

Every playlist that is currently selected and has been fetched is processed in turn. For each one, Spotarchive reads its `setlist.csv`, works out which songs are [already there](#skipping-songs-you-already-have) and asks the backend for the rest, one song at a time:

```text
:: Road Trip: checking which songs are missing...
:: [1/12] Example Artist - Example Song
   ... SomeDL's own output ...
:: [2/12] Another Artist - Another Song
   ...
! Road Trip: 11 downloaded, 30 already there, 1 failed
```

With SomeDL, its own colored output shows each song's search, metadata and download progress. Backends that don't print anything get a progress bar instead.

Songs end up directly in the playlist folder, named the way the backend names them. SomeDL uses `Artist - Song.mp3`, with the artist and title it found on YouTube Music, which can differ slightly from Spotify's.

```text
data/
├── session.json
└── Road Trip/
    ├── setlist.csv
    ├── .downloads.json
    ├── Example Artist - Example Song.mp3
    └── Another Artist - Another Song.mp3
```

A song that appears twice in a playlist is downloaded once.

## Skipping songs you already have

Before downloading, Spotarchive checks each song of the setlist against the playlist folder and skips it when either:

- the download record (`.downloads.json`, see below) lists a file for it that still exists, or
- an audio file in the folder is named `Artist - Song` after it (ignoring case, and with characters that aren't allowed in file names replaced by `_`). This catches files that were there before Spotarchive kept a record, or that you put there yourself. The artist can be the first artist or all of them joined with `, `, ` & ` or `; `.

So running `spotarchive --download` again only fetches what's new since last time, plus anything that failed or that you deleted.

Songs removed from a Spotify playlist are not deleted from its folder.

### The `.downloads.json` record

Because backends name files after what *they* found, the file name alone can't always tell which setlist row a file belongs to. Each playlist folder therefore keeps a small record, `.downloads.json`, mapping every downloaded song (by its Spotify link) to its files:

```json
{
  "version": 1,
  "songs": {
    "https://open.spotify.com/track/...": {
      "label": "Example Artist - Example Song",
      "backend": "somedl",
      "files": ["Example Artist - Example Song.mp3"]
    }
  }
}
```

It is updated after every song, so stopping a run with <kbd>Ctrl</kbd>+<kbd>C</kbd> loses nothing. It moves with the folder when a playlist is [renamed](fetching.md#folders). Deleting it is harmless: songs then fall back to the file name check, and anything that check misses is downloaded again.

## When a song fails

A song can fail when the backend can't find it or its download breaks. Failures are listed as they happen, the other songs carry on, and the run ends with an error that counts them:

```text
error: 3 songs couldn't be downloaded; run `spotarchive --download` again to retry them.
```

Failed songs aren't recorded, so the next `spotarchive --download` tries exactly those again (plus anything new). Spotarchive exits with code `1` when any song or playlist failed, `130` when cancelled.

> [!TIP]
> Downloading hundreds of songs quickly can get your IP temporarily blocked by YouTube. If that happens, wait a while and run `spotarchive --download` again; it picks up where it stopped.

Back to the [documentation index](README.md).
