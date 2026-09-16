"""Compile a quality profile into yt-dlp's format selector and sort order.

Two ways to express a profile:

  * structured fields (max_height, prefer_codecs, prefer_fps) which the app can
    reason about — they are what a future upgrade worker compares against what
    YouTube now offers;
  * `format_override`, a raw yt-dlp selector that bypasses all of it.

The override exists so the structured model never becomes a cage. The
structured fields exist so "is 2160p an upgrade over what I have?" is
answerable, which a raw string cannot be.
"""

import logging

log = logging.getLogger(__name__)

DEFAULT_FORMAT = "bv*+ba/b"
KNOWN_CODECS = ("vp9", "av01", "h264", "h265", "vp8")


def _codec_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip().lower() for part in value.split(",") if part.strip()]


def compile_selector(profile: dict) -> dict:
    """Return the yt-dlp options a profile implies."""
    container = (profile.get("container") or "mkv").strip()
    override = (profile.get("format_override") or "").strip()

    if override:
        return {
            "format": override,
            "format_sort": [],
            "merge_output_format": container,
            "source": "override",
        }

    max_height = profile.get("max_height")
    fmt = DEFAULT_FORMAT
    if max_height:
        # A hard cap belongs in the filter, not the sort: `-S res:2160` prefers
        # the closest match and will happily go above the target if that is all
        # that exists. The trailing plain "b" keeps a download possible when
        # nothing satisfies the cap.
        fmt = (
            f"bv*[height<={max_height}]+ba/"
            f"b[height<={max_height}]/b"
        )

    sort: list[str] = ["res"]
    if profile.get("prefer_fps"):
        sort.append("fps")

    codecs = _codec_list(profile.get("prefer_codecs"))
    if codecs:
        # yt-dlp takes ONE preferred value per sort field; everything else falls
        # back to its built-in ranking (av01 > vp9 > h264 > ...). So only the
        # first entry is expressible, which is why the order matters: putting
        # vp9 first buys hardware decoding on far more clients than av01.
        sort.append(f"vcodec:{codecs[0]}")
        if len(codecs) > 1:
            log.debug(
                "profile %r lists %s codecs; yt-dlp honours only the first (%s)",
                profile.get("name"),
                len(codecs),
                codecs[0],
            )

    return {
        "format": fmt,
        "format_sort": sort,
        "merge_output_format": container,
        "source": "compiled",
    }


def describe(profile: dict) -> str:
    """One-line human summary for the profiles table."""
    compiled = compile_selector(profile)
    if compiled["source"] == "override":
        return f"override: {compiled['format']} -> {compiled['merge_output_format']}"
    parts = [compiled["format"]]
    if compiled["format_sort"]:
        parts.append("-S " + ",".join(compiled["format_sort"]))
    parts.append("-> " + compiled["merge_output_format"])
    return " ".join(parts)
