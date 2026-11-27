# Downloading

Downloading turns the `setlist.csv` of every selected playlist into audio files in that playlist's folder. Spotarchive never takes audio from Spotify: the songs are sourced by a [download backend](backends.md), [SomeDL](https://github.com/ChemistryGull/SomeDL) by default, which finds them on YouTube Music. Songs SomeDL can't download are tried again with the [fallback backend](backends.md#fallback-backend), [yt-dlp](https://github.com/yt-dlp/yt-dlp) by default, which searches all of YouTube.

```bash
spotarchive --download     # download what's missing from the fetched setlists
spotarchive -fd            # fetch (choosing playlists), then download
spotarchive -fnd           # fetch the previous selection, then download
spotarchive -d -t 4        # download 4 songs at once
spotarchive -d -t 1        # one song at a time, showing SomeDL's own output
```

You need to have [fetched](fetching.md) the playlists first, and the backend has to be installed (see [SomeDL](backends.md#somedl)). Downloading itself doesn't talk to Spotify, so it works without a login once the setlists exist.

Plain `spotarchive` downloads too, right after fetching, and then [fixes the metadata](metadata.md). The backend, the fallback and their settings (audio format, folder structure) are set in the [configuration file](configuration.md#download).

## What gets downloaded

Every playlist that is currently selected and has been fetched is processed. For each one, Spotarchive reads its `setlist.csv`, works out which songs are [already there](#skipping-songs-you-already-have) and asks the backend for the rest, [several songs at once](#several-songs-at-once) by default. At the end it sums up every playlist and the whole run:

```text
✔ Road Trip: 11 downloaded, 30 already there
✘ Focus: 3 downloaded, 12 already there, 1 failed
✘ Total: 14 downloaded, 1 failed, 42 already there.
:: 1 error and 2 warnings were logged to data/logs/download-20260101-120000.log
```

Everywhere in Spotarchive, a green check mark means something worked, a red cross means it failed, a yellow `!` is a warning and `::` is information; steps that take a while (logging in, loading your playlists, checking for updates) show a spinner until they're done.

### Several songs at once

Spotarchive downloads as many songs at the same time as `-t N` (`--threads N`) says, or else [`threads`](configuration.md#download) in the configuration file. The default is half of the threads your CPU can run at once (for example 4 on a 4-core, 8-thread CPU). Songs from all selected playlists share the threads.

Each song gets a line with a spinner while it downloads, and a progress bar under it when the backend reports how far along it is (SomeDL does, once yt-dlp starts downloading the audio). When it's done, the progress bar goes away and the spinner turns into a green check mark or a red cross with what happened:

```text
:: Downloading 12 songs, up to 4 at a time...
✔ Road Trip: Example Artist - Example Song: 1 downloaded; 0 errors; 0 warnings
✘ Road Trip: Another Artist - Missing Song: 0 downloaded; 1 error; 1 warning
⠹ Road Trip: Third Artist - Third Song
   ━━━━━━━━━━━━━━━━━━━━━╺━━━━━━━━━━━━━━━━━━  52%
⠹ Road Trip: Fourth Artist - Fourth Song
```

The playlist's name is left out when only one playlist is downloaded. Legacy Windows consoles get ASCII symbols (`+`, `x` and a `-\|/` spinner) instead, here and in all of Spotarchive's other output.

More threads finish a big playlist sooner, but they also send more requests to YouTube Music and the metadata sites at once, which makes [rate limiting](#when-a-song-fails) more likely. Lower the number if many songs fail.

### One song at a time

With one thread (`-t 1`, or `threads = 1`) Spotarchive shows the backend's own output instead, under a line per song:

```text
:: Road Trip: 12 songs to download
:: [1/12] Example Artist - Example Song
   ... SomeDL's own output ...
:: [2/12] Another Artist - Another Song
   ...
```

With SomeDL, its own colored output shows each song's search, metadata and download progress. Backends that don't print anything get a progress bar instead.

Songs end up directly in the playlist folder, named the way the backend names them. SomeDL uses `Artist - Song.mp3`, with the artist and title it found on YouTube Music, which can differ slightly from Spotify's. With SomeDL's [`flat = false`](configuration.md#backendssomedl) they go into subfolders of the playlist folder instead, following SomeDL's own output template.

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
- an audio file in the folder is named `Artist - Song` after it (ignoring case, and with characters that aren't allowed in file names replaced by `_`). This catches files that were there before Spotarchive kept a record, or that you put there yourself. The artist can be the first artist or all of them joined with `, `, ` & ` or `; `. Files in subfolders count too (except in folders whose name starts with `.`).

So running `spotarchive --download` again only fetches what's new since last time, plus anything that failed or that you deleted.

Songs removed from a Spotify playlist are not deleted from its folder.

### The `.downloads.json` record

Because backends name files after what *they* found, the file name alone can't always tell which setlist row a file belongs to. Each playlist folder therefore keeps a small record, `.downloads.json`, mapping every downloaded song (by its Spotify link) to the backend that downloaded it, its files and, once its [metadata was fixed](metadata.md#what-gets-recorded), who tagged it:

```json
{
  "version": 1,
  "songs": {
    "https://open.spotify.com/track/...": {
      "label": "Example Artist - Example Song",
      "backend": "somedl",
      "files": ["Example Artist - Example Song.mp3"],
      "tagged": "setlist"
    }
  }
}
```

A song you [replaced with your own link](#fixing-a-wrong-match) has an `"override"` entry with that link instead of a `"backend"`.

It is updated after every song, so stopping a run with <kbd>Ctrl</kbd>+<kbd>C</kbd> loses nothing. It moves with the folder when a playlist is [renamed](fetching.md#folders). Deleting it is harmless: songs then fall back to the file name check, and anything that check misses is downloaded again.

## When a song fails

A song can fail when the backend can't find it or its download breaks. With a [fallback backend](backends.md#fallback-backend) it is first tried again with that one (with one thread you see a `! ... somedl couldn't download it (...); trying ytdlp` line), and only fails if the fallback can't download it either. Failures are shown as they happen, the other songs carry on, and the run ends with an error that counts them:

```text
✘ 3 songs couldn't be downloaded; run `spotarchive --download` again to retry them.
```

Failed songs aren't recorded, so the next `spotarchive --download` tries exactly those again (plus anything new). The [log file](#the-log-file) says why each one failed. Songs YouTube marks as age-restricted only download with a [YouTube login](authentication.md#logging-in-to-youtube-optional): run `spotarchive --login youtube`, then download again. For SomeDL error messages (HTTP 403, Deno, lyrics), see [troubleshooting SomeDL](backends.md#troubleshooting). Spotarchive exits with code `1` when any song or playlist failed, `130` when cancelled.

> [!TIP]
> Downloading hundreds of songs quickly can get your IP temporarily blocked by YouTube. If that happens, wait a while and run `spotarchive --download` again; it picks up where it stopped.

## Fixing a wrong match

Backends search by name, so now and then one downloads the wrong track: a cover, a remix, a different song altogether. Find the right one yourself and give Spotarchive its link:

```bash
spotarchive --overwrite "Artist Name - Song Name" "https://www.youtube.com/watch?v=..."
spotarchive -o "Artist Name - Song Name" "https://www.youtube.com/watch?v=..."    # the same
```

Put both in quotes (links often contain `&`). The audio at the link is downloaded once, and then replaces every copy of that song in the [download folder](configuration.md#data_dir). `--overwrite` needs no Spotify login and can only be combined with [`--config`](configuration.md).

> [!WARNING]
> The old files are deleted. This happens only after the download worked, and there is no confirmation question.

### Which files are replaced

Every folder in the download folder is searched, including playlists that aren't currently selected, with their subfolders, plus files lying directly in the download folder. Folders whose name starts with `.` are skipped. Case is ignored, and characters that aren't allowed in file names count as `_`. A copy is:

- **The song in a setlist.** `"Artist Name - Song Name"` is compared with each `setlist.csv` row (the artist can be the first one or all of them, as for [skipping](#skipping-songs-you-already-have)). Its files come from the [download record](#the-downloadsjson-record), so they are found even when the backend named them after the wrong track it found. If different versions of a song in your playlists share that artist and title, they all get your link's audio.
- **A file named like that**, with or without a setlist.

You can also give the name of the *wrong file* (say `"Other Band - Other Song.mp3"` was downloaded for your song: use `"Other Band - Other Song"`). The record then tells which song it was downloaded for, and that song gets your link. But if some setlist does have a song called what you typed, you are taken to mean that song: files the record gives to *another* song are left alone, with a warning naming it.

### What the new file looks like

- It is named `Artist - Song` after the setlist row, in the folder the old file was in. A file that matched no setlist row keeps its name. If another song's file already has the name, ` (2)` is added, so nothing else is overwritten.
- It gets the setlist's tags (title, artists, album, release date; each playlist's own). Without a setlist row, only the artist and title you typed are set. There is no cover art or lyrics.
- The record entry notes your link as `"override"`. [Metadata cleanup](metadata.md#songs-you-replaced) then never asks a download helper about the song, which could bring back the wrong match's tags, and later downloads skip it as usual.

### How the link is downloaded

[yt-dlp](backends.md#yt-dlp) downloads exactly what the link points to, whichever backends you've configured: nothing is searched for, and the [length check](configuration.md#backendsytdlp) doesn't apply. The audio format and other [`[backends.ytdlp]`](configuration.md#backendsytdlp) settings do, so the new file's type can differ from the old one's. A [YouTube login](authentication.md#logging-in-to-youtube-optional) is used if you have one. Any site yt-dlp supports works, except Spotify: Spotarchive never takes audio from there. The link must be a single video or track; playlists and live streams are refused. If the download fails, nothing is changed.

> [!NOTE]
> Only files that exist are replaced; the link isn't remembered for later downloads. If a playlist lists the song but doesn't have it yet, Spotarchive tells you; run `spotarchive -d` and then the same `--overwrite` again. The same goes for a playlist you add later. If you delete a replaced file, the next download searches for the song with the backend again.

## The log file

Every warning and error of a download run is written to a log file as soon as it happens, however many threads are used. The file is `logs/download-<date>-<time>.log` in the [download folder](configuration.md#data_dir), and the end of the run prints its path. A run without warnings or errors doesn't create one. Each entry has the time, the level, the playlist and song, and the message (indented):

```text
2026-01-01 12:00:03 WARNING Road Trip / Example Artist - Example Song
    Lyrics: lrclib error: 503
2026-01-01 12:00:09 ERROR   Road Trip / Another Artist - Missing Song
    Song could not be downloaded, error on metadata fetching.
```

With SomeDL, the warnings and errors are the `WARNING` and `ERROR` lines it prints (consecutive error lines, like yt-dlp's, become one entry); with yt-dlp, its `WARNING:` and `ERROR:` lines. When a song fails, the reason is logged as an error too. With a [fallback backend](backends.md#fallback-backend), each backend's messages start with its name (`somedl: ...`) and are logged as warnings, since the next backend may still download the song; if none does, one error lists every backend's reason. Problems that aren't about one song, like a missing Deno, are logged with `-` instead of a song. Old log files are never deleted; remove them when you no longer need them.

Back to the [documentation index](README.md).
