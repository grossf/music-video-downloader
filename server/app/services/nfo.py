"""Kodi-style <musicvideo> NFO sidecars.

Jellyfin's Music Videos library performs NO external metadata lookup, so this
file is the only thing that gives a video an artist, title or tag. That makes
the NFO authoritative rather than a nice-to-have: if it is wrong or missing,
Jellyfin has nothing else to fall back on.
"""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

from app.db import TYPE_TAGS
from app.services.naming import display_title

log = logging.getLogger(__name__)

XML_DECL = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'


def nfo_title(
    title: str | None, artist: str | None = None, video_type: str = "mv"
) -> str | None:
    """"ILLIT - Song (Performance)". Jellyfin's music video lists show only
    the title, so without the artist every entry is a bare song name; with it
    the list also sorts by artist."""
    shown = display_title(title, video_type)
    if shown and artist:
        return f"{artist} - {shown}"
    return shown


def build_nfo(
    *,
    video_id: str,
    title: str | None,
    artist: str | None = None,
    year: int | None = None,
    premiered: str | None = None,
    video_type: str = "mv",
    studio: str | None = None,
    plot: str | None = None,
    runtime: int | None = None,
    thumb_name: str | None = None,
) -> str:
    root = ET.Element("musicvideo")

    def add(tag: str, value) -> None:
        if value is None or value == "":
            return
        ET.SubElement(root, tag).text = str(value)

    add("title", nfo_title(title, artist, video_type) or video_id)
    add("artist", artist)
    add("year", year)
    # Jellyfin reads <premiered> as the full release date; <year> alone loses
    # the month and day.
    add("premiered", premiered)
    # The uploading channel. Only <studio> now — it used to be duplicated as a
    # <tag> as well, which carried no extra information.
    add("studio", studio)
    add("plot", plot)
    if runtime:
        add("runtime", max(1, round(runtime / 60)))  # Kodi expects minutes

    type_tag = TYPE_TAGS.get(video_type)
    if type_tag:
        ET.SubElement(root, "tag").text = str(type_tag)

    if thumb_name:
        ET.SubElement(root, "thumb").text = thumb_name

    uid = ET.SubElement(root, "uniqueid", {"type": "youtube", "default": "true"})
    uid.text = video_id

    ET.indent(root, space="  ")
    return XML_DECL + ET.tostring(root, encoding="unicode") + "\n"


def write_nfo(path: Path, **fields) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_nfo(**fields), encoding="utf-8")
    log.debug("wrote nfo %s", path)
    return path


def read_nfo(path: Path) -> dict:
    """Minimal reader, so the library can be re-indexed from disk if the
    database is ever lost."""
    tree = ET.parse(path)
    root = tree.getroot()

    def text(tag: str) -> str | None:
        el = root.find(tag)
        return el.text if el is not None else None

    return {
        "title": text("title"),
        "artist": text("artist"),
        "year": int(text("year")) if (text("year") or "").isdigit() else None,
        "premiered": text("premiered"),
        "studio": text("studio"),
        "video_id": text("uniqueid"),
        "tags": [el.text for el in root.findall("tag") if el.text],
    }
