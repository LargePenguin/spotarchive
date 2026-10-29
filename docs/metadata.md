# Metadata cleanup

Metadata cleanup gives the songs in your playlist folders proper tags (title, artists, album, date, ...), so music players show and sort them correctly. It doesn't download or rename anything.

```bash
spotarchive --meta      # fix the tags of the downloaded songs
spotarchive -dm         # download what's missing, then fix the tags
spotarchive -fndm       # fetch the previous selection, download, fix the tags
```

Plain `spotarchive` runs it too, after downloading. It works on every selected playlist that has been [fetched](fetching.md), and on the songs of its `setlist.csv` that are in the folder: the ones Spotarchive [downloaded](downloading.md) and the ones it [recognizes by name](downloading.md#skipping-songs-you-already-have), such as files you added yourself. It doesn't talk to Spotify.

When some downloads fail during `-dm` or plain `spotarchive`, the metadata of the songs that did download is still fixed; the run then ends with the download error.

## Choosing a source

The tags come from one of two places, chosen with [`source` in `[metadata]`](configuration.md#metadata) in the configuration file:

| `source` | Tags come from | Good for |
| --- | --- | --- |
| `"backend"` (default) | The download helper's own metadata. SomeDL looks songs up on YouTube Music, MusicBrainz, Genius and Deezer and also adds cover art, lyrics, track numbers and genre. | The richest tags. |
| `"setlist"` | Spotify's metadata in the playlist's `setlist.csv`: title, all artists, album and release date. Other tags already in the file (cover art, lyrics, ...) are kept. | Tags that match exactly what you see on Spotify. |

```toml
[metadata]
source = "setlist"
```

### `backend`

Download helpers that tag their own downloads, like SomeDL, have already done the work for the songs they downloaded, so those are left alone. Only the other songs (files you added yourself, or that another backend downloaded) are handed to the helper.

With SomeDL, each such file is run through [`somedl import`](https://github.com/ChemistryGull/SomeDL) in its *update* mode: SomeDL searches for the song by the setlist's first artist and title (`Artist - Song`, the same search it downloads with), then rewrites the file's tags. The file keeps its name and place; SomeDL's import cache and download report are not left in the folder. A song SomeDL can't identify is reported as failed.

If the configured backend can't tag files at all, Spotarchive says so and uses the `setlist` source instead.

### `setlist`

Writes the tags with [mutagen](https://mutagen.readthedocs.io/), which handles MP3, M4A, FLAC, Ogg Vorbis and Opus. A file is only rewritten when one of its tags differs from the setlist, so running it again is quick and changes nothing. Multiple artists are stored as separate values of the artist tag. Empty setlist fields (Liked Songs have no album date, for example) are skipped rather than erased.

## What gets recorded

Which source last tagged a song is kept in the playlist's [download record](downloading.md#the-downloadsjson-record) as `"tagged"` (`"somedl"`, `"setlist"`, ...). This lets the `backend` source skip songs it has tagged before, and retag songs the `setlist` source changed if you switch back. Deleting the record is harmless: with `backend`, recognized files are simply tagged again.

## Output

A progress bar runs per playlist, then a summary:

```text
:: Fixing metadata with somedl...
Road Trip ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 42/42
! Example Artist - Example Song: SomeDL couldn't identify the song
! Road Trip: 3 fixed, 38 already fine, 1 failed
error: 1 songs' metadata couldn't be fixed.
```

*Fixed* songs were retagged, *already fine* ones were left alone. Failed songs are tried again on the next run. Spotarchive exits with code `1` when any song or playlist failed.

Back to the [documentation index](README.md).
