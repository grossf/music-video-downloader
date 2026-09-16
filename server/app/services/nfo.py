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

log = logging.getLogger(__name__)

XML_DECL = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'


def build_nfo(
    *,
    video_id: str,
    title: str | None,
    artist: str | None = None,
    year: int | None = None,
    premiered: str | None = None,
    video_type: str = "mv",
    label: str | None = None,
    plot: str | None = None,
    runtime: int | None = None,
    thumb_name: str | None = None,
) -> str:
    root = ET.Element("musicvideo")

    def add(tag: str, value) -> None:
        if value is None or value == "":
            return
        ET.SubElement(root, tag).text = str(value)

    add("title", title or video_id)
    add("artist", artist)
    add("year", year)
    # Jellyfin reads <premiered> as the full release date; <year> alone loses
    # the month and day.
    add("premiered", premiered)
    # <studio> carries the label semantically; the duplicate <tag> below is
    # what actually makes it filterable in the Jellyfin UI.
    add("studio", label)
    add("plot", plot)
    if runtime:
        add("runtime", max(1, round(runtime / 60)))  # Kodi expects minutes

    for tag_value in (TYPE_TAGS.get(video_type), label):
        if tag_value:
            ET.SubElement(root, "tag").text = str(tag_value)

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
        "label": text("studio"),
        "video_id": text("uniqueid"),
        "tags": [el.text for el in root.findall("tag") if el.text],
    }
