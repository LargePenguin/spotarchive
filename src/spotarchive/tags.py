"""Writing metadata into audio files (mutagen).

Spotify's metadata from ``setlist.csv`` is used by the metadata cleanup
(``metadata.source = "setlist"``) and by backends whose helper finds no
metadata of its own (yt-dlp), which also tags imported links.
"""

from __future__ import annotations

import base64
from pathlib import Path

from spotarchive.backends.base import Song, TaggingFailed

# Formats setlist tags can be written to.
TAGGABLE_EXTENSIONS = frozenset({".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".opus"})


def setlist_tags(song: Song) -> dict[str, list[str]]:
    tags = {"title": [song.name], "artist": list(song.artists), "album": [song.album], "date": [song.release_date]}
    return {key: values for key, values in tags.items() if any(values)}


def write_setlist_tags(path: Path, song: Song) -> bool:
    """Tag *path* with *song*'s setlist metadata; False when it already had it."""
    return write_tags(path, setlist_tags(song))


def write_tags(path: Path, tags: dict[str, list[str]]) -> bool:
    """Set *tags* (mutagen's "easy" names) on *path*; False when it already had them."""
    import mutagen

    try:
        audio = mutagen.File(path, easy=True)
        if audio is None:
            raise TaggingFailed(f"{path.suffix or 'this file type'} files can't be tagged")
        if audio.tags is None:
            audio.add_tags()
        changes = {k: v for k, v in tags.items() if list(audio.tags.get(k, [])) != v}
        if not changes:
            return False
        for key, values in changes.items():
            audio[key] = values
        audio.save()
    # TypeError: formats without "easy" tag names, like WAV, refuse plain text.
    except (mutagen.MutagenError, OSError, TypeError) as exc:
        raise TaggingFailed(f"couldn't tag {path.name}: {exc}") from exc
    return True


def embed_cover(path: Path, jpeg: bytes) -> None:
    """Make *jpeg* the front cover of *path*."""
    import mutagen
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.ogg import OggFileType

    picture = Picture()
    picture.type = 3  # front cover
    picture.mime = "image/jpeg"
    picture.data = jpeg
    try:
        audio = mutagen.File(path)
        if isinstance(audio, (MP3, MP4, FLAC, OggFileType)):
            if audio.tags is None:
                audio.add_tags()
            if isinstance(audio, MP3):
                audio.tags.delall("APIC")
                audio.tags.add(APIC(encoding=3, mime=picture.mime, type=picture.type, desc="Cover", data=jpeg))
            elif isinstance(audio, MP4):
                audio["covr"] = [MP4Cover(jpeg, imageformat=MP4Cover.FORMAT_JPEG)]
            elif isinstance(audio, FLAC):
                audio.clear_pictures()
                audio.add_picture(picture)
            else:
                audio["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
            audio.save()
            return
    except (mutagen.MutagenError, OSError) as exc:
        raise TaggingFailed(f"couldn't add cover art to {path.name}: {exc}") from exc
    raise TaggingFailed(f"{path.suffix or 'this file type'} files can't hold cover art")
