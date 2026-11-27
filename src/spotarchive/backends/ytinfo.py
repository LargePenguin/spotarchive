"""Song metadata from what yt-dlp says about an imported link (its "info" dict).

No searches and no other services: only what the site itself reports. When it
names the track and the artists (YouTube's official audio does), those are
used as they are. Otherwise they are guessed from the title and the uploader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TrackInfo:
    title: str
    artists: tuple[str, ...] = ()
    album: str = ""
    # "YYYY-MM-DD" or "YYYY"; never the upload date.
    release_date: str = ""
    track_number: int | None = None
    genres: tuple[str, ...] = ()
    # The site named both the track and the artists.
    official: bool = False

    @property
    def label(self) -> str:
        return f"{self.artists[0]} - {self.title}" if self.artists else self.title

    def tags(self) -> dict[str, list[str]]:
        tags = {
            "title": [self.title],
            "artist": list(self.artists),
            "album": [self.album],
            "date": [self.release_date],
            "tracknumber": [str(self.track_number or "")],
            "genre": list(self.genres),
        }
        return {key: values for key, values in tags.items() if any(values)}


# "Artist - Title", with a hyphen or a dash between spaces.
_DASH = re.compile(r"\s+[-\u2013\u2014]\s+")
# A part of a title in (), [] or the full-width lenticular brackets.
_BRACKETS = re.compile(r"\s*(?:\(([^()]*)\)|\[([^][]*)\]|\u3010([^\u3010\u3011]*)\u3011)")
# Such a part is a label about the video when it has one of these words ...
_VIDEO_WORDS = frozenset({"official", "oficial", "video", "lyric", "lyrics", "visualizer", "visualiser", "audio",
                          "hd", "hq", "4k", "720p", "1080p", "mv"})
# ... and no words but these. So "(Live)", "(Official Live Video)",
# "(2011 Remaster)" or "(Video Game Remix)" stay.
_LABEL_WORDS = _VIDEO_WORDS | {"music", "remaster", "remastered", "full", "upgrade", "the"}
# YouTube names the channels of its official audio "Artist - Topic".
_TOPIC = re.compile(r"\s+-\s+Topic$")


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _names(info: dict, plural: str, singular: str) -> tuple[str, ...]:
    """A field yt-dlp has as a list (newer) or a single string."""
    values = info.get(plural)
    if not isinstance(values, list) or not values:
        values = [info.get(singular)]
    return tuple(dict.fromkeys(name for name in map(_text, values) if name))


def clean_title(title: str) -> str:
    """*title* without labels like "(Official Video)"."""
    def replace(match: re.Match) -> str:
        words = re.findall(r"\w+", next(g for g in match.groups() if g is not None).casefold())
        is_label = _VIDEO_WORDS.intersection(words) and _LABEL_WORDS.issuperset(words)
        return "" if is_label else match.group(0)

    return " ".join(_BRACKETS.sub(replace, title).split()) or title


def _release_date(info: dict) -> str:
    # For a YouTube premiere or stream, "release_date" is when it aired.
    if info.get("extractor_key") == "Youtube" and info.get("release_timestamp"):
        return ""
    date = _text(info.get("release_date"))
    if re.fullmatch(r"\d{8}", date):
        return f"{date[:4]}-{date[4:6]}-{date[6:]}"
    year = info.get("release_year")
    return str(year) if isinstance(year, int) else ""


def _guess(title: str, artists: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Title and artists from an "Artist - Title" video title."""
    parts = _DASH.split(title, maxsplit=1)
    if len(parts) != 2 or not all(parts):
        return title, artists
    if not artists:
        return parts[1], (parts[0],)
    # With the artist known, it is only cut off the front of the title.
    known = {name.casefold() for name in (*artists, ", ".join(artists), " & ".join(artists))}
    return (parts[1] if parts[0].casefold() in known else title), artists


def track_info(info: dict) -> TrackInfo:
    track = _text(info.get("track"))
    artists = _names(info, "artists", "artist")
    official = bool(track and artists)
    title = track
    if not official:
        guessed_title, artists = _guess(clean_title(_text(info.get("title"))), artists)
        title = track or guessed_title
        if not artists:
            uploader = _TOPIC.sub("", _text(info.get("channel")) or _text(info.get("uploader")))
            artists = (uploader,) if uploader else ()
    number = info.get("track_number")
    return TrackInfo(
        title=title or _text(info.get("id")) or "Unknown title",
        artists=artists,
        album=_text(info.get("album")),
        release_date=_release_date(info),
        track_number=number if isinstance(number, int) and number > 0 else None,
        genres=_names(info, "genres", "genre"),
        official=official,
    )
