# Metadata cleanup

Metadata cleanup gives the songs in your playlist folders proper tags (title, artists, album, date, ...), so music players show and sort them correctly. It doesn't download or rename anything.

```bash
spotarchive --meta      # fix the tags of the downloaded songs
spotarchive -dm         # download what's missing, then fix the tags
spotarchive -fndm       # fetch the previous selection, download, fix the tags
```

Plain `spotarchive` runs it too, after downloading. [Imported links](#imported-links) are tagged as they download, and `--meta` leaves them alone. It works on every selected playlist that has been [fetched](fetching.md), and on the songs of its `setlist.csv` that are in the folder: the ones Spotarchive [downloaded](downloading.md) and the ones it [recognizes by name](downloading.md#skipping-songs-you-already-have), such as files you added yourself. It doesn't talk to Spotify.

When some downloads fail during `-dm` or plain `spotarchive`, the metadata of the songs that did download is still fixed; the run then ends with the download error.

## Choosing a source

The tags come from one of two places, chosen with [`source` in `[metadata]`](configuration.md#metadata) in the configuration file:

| `source` | Tags come from | Good for |
| --- | --- | --- |
| `"backend"` (default) | The download helper's own metadata. SomeDL looks songs up on YouTube Music, MusicBrainz, Genius and Deezer and also adds cover art, lyrics, track numbers and genre. yt-dlp finds no metadata and uses the setlist's. | The richest tags. |
| `"setlist"` | Spotify's metadata in the playlist's `setlist.csv`: title, all artists, album and release date. Other tags already in the file (cover art, lyrics, ...) are kept. | Tags that match exactly what you see on Spotify. |

```toml
[metadata]
source = "setlist"
```

### `backend`

Download helpers that tag their own downloads, like SomeDL, have already done the work for the songs they downloaded, so those are left alone. Only the other songs (files you added yourself, or that another backend downloaded) are handed to the helper.

With SomeDL, each such file is run through [`somedl import`](https://github.com/ChemistryGull/SomeDL) in its *update* mode: SomeDL searches for the song by the setlist's first artist and title (`Artist - Song`, the same search it downloads with), then rewrites the file's tags. The file keeps its name and place; SomeDL's import cache and download report are not left in the folder. A song SomeDL can't identify is reported as failed.

With a [fallback backend](backends.md#fallback-backend), each song goes to the backend that downloaded it (from the [download record](downloading.md#the-downloadsjson-record)) first, and to the other one if that one fails; songs neither downloaded (added by hand) go to the main backend first. So songs [yt-dlp](backends.md#yt-dlp) downloaded keep the setlist's tags rather than being looked up by SomeDL, and a song SomeDL can't identify still gets the setlist's tags from yt-dlp. A song only fails when every backend fails; the reason then lists each one's.

If the configured backends can't tag files at all, Spotarchive says so and uses the `setlist` source instead.

### `setlist`

Writes the tags with [mutagen](https://mutagen.readthedocs.io/), which handles MP3, M4A, FLAC, Ogg Vorbis and Opus. A file is only rewritten when one of its tags differs from the setlist, so running it again is quick and changes nothing. Multiple artists are stored as separate values of the artist tag. Empty setlist fields (Liked Songs have no album date, for example) are skipped rather than erased.

## Songs you replaced

Songs [replaced with your own link](downloading.md#fixing-a-wrong-match) (`spotarchive --overwrite`) get the setlist's tags right away. With the `backend` source they are never handed to a helper afterwards, since a helper searches by name and could write the wrong match's tags again. The `setlist` source treats them like any other song.

## What gets recorded

Which source last tagged a song is kept in the playlist's [download record](downloading.md#the-downloadsjson-record) as `"tagged"` (`"somedl"`, `"ytdlp"`, `"setlist"`, ...). This lets the `backend` source skip songs one of the configured backends has tagged before, and retag songs the `setlist` source changed if you switch back. Deleting the record is harmless: with `backend`, recognized files are simply tagged again.

## Output

A progress bar runs per playlist, then a summary:

```text
:: Fixing metadata with somedl (or ytdlp)...
Road Trip ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 42/42
✘ Example Artist - Example Song: SomeDL couldn't identify the song
✘ Road Trip: 3 fixed, 38 already fine, 1 failed
✘ 1 songs' metadata couldn't be fixed.
```

*Fixed* songs were retagged, *already fine* ones were left alone. Failed songs are tried again on the next run. Spotarchive exits with code `1` when any song or playlist failed.

## Imported links

A [link you import](downloading.md#importing-a-link) has no setlist, so its tags come only from what yt-dlp reports about it. No other service is searched, so a file is never given another recording's tags. The `[metadata]` settings don't apply.

**When the site names both the track and the artists**, as YouTube does for official audio (the "art tracks" of YouTube Music and the `Artist - Topic` channels), these are used as they are: title, artists, album, release date, and track number and genre if given. The video's thumbnail, cut to a centered square, becomes the cover art.

**Otherwise** (music videos and ordinary uploads, typically) the tags are guessed:

- If the site names an artist, it is used. If not, a title like `Artist - Title` is split at the first dash with spaces around it.
- Bracketed labels about the video are removed from the title, such as `(Official Video)`, `[Lyric Video]`, `(Visualizer)` or `(4K Remaster)`. Ones about the recording stay: `(Live)`, `(Remix)`, `(feat. ...)`, `(2011 Remaster)`.
- With no artist from either, the channel's or uploader's name is the artist (without ` - Topic`).
- Album, release date, track number and genre are set only if the site gives them. The upload date is never used as the release date, nor is the date a YouTube premiere or stream aired.
- No cover art is added.

> [!WARNING]
> A guess can be wrong. A video titled `Title - Artist`, for example, ends up with the two swapped. Check these imports and fix the tags in a tag editor if needed.

Cover art and tags work for MP3, M4A, FLAC, Ogg Vorbis and Opus. With other formats, or when tagging or the cover fails, the file is kept and a warning is logged. YouTube has no lyrics or ISRC to offer. Only YouTube has been checked against live links; what other sites report varies.

Back to the [documentation index](README.md).
