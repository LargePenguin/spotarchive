"""Writing Spotify's metadata from ``setlist.csv`` into audio files (mutagen).

Used by the metadata cleanup (``metadata.source = "setlist"``) and by
backends whose helper finds no metadata of its own (yt-dlp).
"""

from __future__ import annotations

from pathlib import Path

from spotarchive.backends.base import Song, TaggingFailed

# Formats setlist tags can be written to.
TAGGABLE_EXTENSIONS = frozenset({".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".opus"})


def setlist_tags(song: Song) -> dict[str, list[str]]:
    tags = {"title": [song.name], "artist": list(song.artists), "album": [song.album], "date": [song.release_date]}
    return {key: values for key, values in tags.items() if any(values)}


def write_setlist_tags(path: Path, song: Song) -> bool:
    """Tag *path* with *song*'s setlist metadata; False when it already had it."""
    import mutagen

    try:
        audio = mutagen.File(path, easy=True)
        if audio is None:
            raise TaggingFailed(f"{path.suffix or 'this file type'} files can't be tagged")
        if audio.tags is None:
            audio.add_tags()
        changes = {k: v for k, v in setlist_tags(song).items() if list(audio.tags.get(k, [])) != v}
        if not changes:
            return False
        for key, values in changes.items():
            audio[key] = values
        audio.save()
    # TypeError: formats without "easy" tag names, like WAV, refuse plain text.
    except (mutagen.MutagenError, OSError, TypeError) as exc:
        raise TaggingFailed(f"couldn't tag {path.name}: {exc}") from exc
    return True
