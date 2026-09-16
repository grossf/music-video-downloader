"""Filename sanitisation and library placement.

`place_file` is used by BOTH the initial download and the metadata-edit path:
"put this video where its current metadata says it belongs". That is why the
worker downloads into a staging directory first rather than letting yt-dlp
write straight into the library — the final name depends on metadata we may
not trust until it has been reviewed.
"""

import logging
import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

# Suffix appended to the title — in the filename and the NFO <title> alike —
# so an MV and its performance cut are told apart in Jellyfin's list, where
# both would otherwise just read "Song". An MV gets no suffix.
TYPE_SUFFIXES = {
    "mv": None,
    "performance": "Performance",
    "dance_practice": "Dance Practice",
    "live_stage": "Live Stage",
    "fancam": "Fancam",
    "relay_dance": "Relay Dance",
    "behind": "Behind",
    "other": None,
}

UNSORTED_DIR = "_Unsorted"

# Illegal on Windows, plus both path separators. Built as a translate table
# rather than a regex character class: escaping a backslash inside a class
# is easy to get subtly wrong, and getting it wrong lets a separator through.
_ILLEGAL_CHARS = chr(92) + '<>:"/|?*'
_ILLEGAL_MAP = {ord(c): "-" for c in _ILLEGAL_CHARS}
# Control chars and the invisible formatting characters that produce
# identical-looking-but-different filenames.
_INVISIBLE = "".join(chr(c) for c in range(0x20)) + "\x7f\u200b\u200c\u200d\ufeff"
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Filesystems cap a single name at 255 bytes. The longest suffix we append is
# "-thumb.jpg", so the stem must stay well under that.
MAX_COMPONENT_BYTES = 150   # for a standalone component, i.e. the artist folder
MAX_STEM_BYTES = 200        # for the assembled "Artist - Title (Version) [id]"


def _truncate_bytes(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    # errors="ignore" drops a partial multi-byte char at the cut point.
    return encoded[:limit].decode("utf-8", errors="ignore").rstrip()


def sanitize_component(value: str | None, fallback: str = "Unknown") -> str:
    """Make a single path component safe on NTFS and ext4.

    Emoji are preserved — they are legal on both filesystems and common in
    K-pop titles, so stripping them would mangle real metadata.
    """
    if not value:
        return fallback
    text = unicodedata.normalize("NFC", value)
    text = text.translate({ord(c): None for c in _INVISIBLE})
    text = text.translate(_ILLEGAL_MAP)
    text = re.sub(r"\s+", " ", text).strip()
    # Windows cannot address names ending in a dot or space.
    text = text.rstrip(". ")
    text = _truncate_bytes(text, MAX_COMPONENT_BYTES).rstrip(". ")
    if not text:
        return fallback
    if text.upper() in _WIN_RESERVED or text.upper().split(".")[0] in _WIN_RESERVED:
        text = f"_{text}"
    return text


def display_title(title: str | None, video_type: str = "mv") -> str | None:
    """The title with its type suffix, e.g. "Song (Performance)".

    A title that already ends with the suffix is left alone, so hand-typing
    "Song (Performance)" does not become "Song (Performance) (Performance)".
    Anything else in brackets, like "Song (Band Ver.)", is the user's own and
    still gets the suffix.
    """
    suffix = TYPE_SUFFIXES.get(video_type)
    if not title or not suffix:
        return title
    if title.rstrip().lower().endswith(f"({suffix.lower()})"):
        return title
    return f"{title} ({suffix})"


@dataclass(frozen=True)
class LibraryPaths:
    directory: Path
    media: Path
    nfo: Path
    thumb: Path

    @property
    def stem(self) -> str:
        return self.media.stem


def build_paths(
    *,
    video_id: str,
    artist: str | None,
    title: str | None,
    video_type: str = "mv",
    ext: str = "mkv",
    media_root: Path | None = None,
) -> LibraryPaths:
    """{media_root}/{Artist}/{Artist} - {Title} (Type) [videoId].{ext}

    With no artist the video lands in _Unsorted/ under its bare title, which
    makes un-reviewed items obvious in Jellyfin as well as in the web UI.
    """
    root = media_root or settings.media_root
    clean_artist = sanitize_component(artist, fallback="") if artist else ""
    clean_title = sanitize_component(display_title(title, video_type), fallback=video_id)

    directory = root / (clean_artist or UNSORTED_DIR)

    name = f"{clean_artist} - {clean_title}" if clean_artist else clean_title

    # The [videoId] tail is the identity key and must never be truncated, so
    # the descriptive part absorbs the whole budget cut.
    tail = f" [{video_id}]"
    budget = MAX_STEM_BYTES - len(tail.encode("utf-8"))
    name = _truncate_bytes(name, budget).rstrip(". ") or video_id
    stem = f"{name}{tail}"

    return LibraryPaths(
        directory=directory,
        media=directory / f"{stem}.{ext}",
        nfo=directory / f"{stem}.nfo",
        thumb=directory / f"{stem}-thumb.jpg",
    )


def prune_empty_dir(directory: Path, media_root: Path | None = None) -> None:
    """Remove an artist folder left empty by a move or delete."""
    root = (media_root or settings.media_root).resolve()
    try:
        resolved = directory.resolve()
    except OSError:
        return
    if resolved == root or root not in resolved.parents:
        return
    try:
        if not any(resolved.iterdir()):
            resolved.rmdir()
            log.info("pruned empty directory %s", resolved)
    except OSError as exc:
        log.debug("could not prune %s: %s", resolved, exc)


def place_file(
    *,
    source_media: Path,
    target: LibraryPaths,
    source_thumb: Path | None = None,
) -> LibraryPaths:
    """Move media (and thumbnail) to their target paths, creating the artist
    folder and pruning the old one if it is left empty.

    Safe to call when the file is already in place — used by the edit path,
    where most fields change but the file often does not move.
    """
    target.directory.mkdir(parents=True, exist_ok=True)
    previous_dir = source_media.parent

    def _move(source: Path, destination: Path) -> None:
        if source.resolve() == destination.resolve():
            return
        # os.rename refuses an existing destination on Windows, which a
        # re-download always hits because the target name is unchanged.
        destination.unlink(missing_ok=True)
        # shutil.move handles the staging -> library hop across docker volumes,
        # which may be different devices.
        shutil.move(str(source), str(destination))

    _move(source_media, target.media)
    log.info("placed %s", target.media)

    if source_thumb and source_thumb.exists():
        _move(source_thumb, target.thumb)

    if previous_dir != target.directory:
        prune_empty_dir(previous_dir)

    return target
