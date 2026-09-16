"""Metadata extraction: yt-dlp lookup plus the title-parsing rules.

Split deliberately in two:
  * `extract_metadata(info)` is pure and takes a yt-dlp info dict, so every
    title-parsing rule is unit-testable without touching the network.
  * `probe(url)` does the network call and layers channel defaults on top.
"""

import asyncio
import logging
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qs, urlparse

import yt_dlp

from app.db import VIDEO_TYPES, get_conn

log = logging.getLogger(__name__)

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Straight and curly quotes, as used interchangeably in YouTube titles.
QUOTE_CHARS = "\"'“”‘’"

# Type is detected from the RAW title, before noise stripping, because the
# phrases that identify the type are exactly the ones we strip as noise.
TYPE_PATTERNS: list[tuple[str, str]] = [
    ("dance_practice", r"dance\s*practice|choreograph|안무|연습"),
    ("relay_dance", r"relay\s*dance|릴레이\s*댄스"),
    ("fancam", r"fancam|직캠"),
    (
        "performance",
        r"performance\s*(video|ver|clip)|퍼포먼스|special\s*(clip|video)",
    ),
    (
        "live_stage",
        r"live\s*(stage|clip|performance)|무대|comeback\s*stage|\blive\b",
    ),
    ("behind", r"behind|making\s*(of|film)|비하인드"),
]

# Stripped from the title once the type has been determined.
NOISE_PATTERNS = [
    r"\[[^\]]*\]",  # [MV], [M/V], [Official Video], [4K]
    r"\((?:official\s*)?(?:m/?v|music\s*video|video|audio|lyrics?\s*video|"
    r"visualizer|teaser|performance\s*(?:video|ver\.?)|dance\s*practice|"
    r"choreography\s*(?:video|ver\.?)|color\s*coded[^)]*|han/?rom/?eng[^)]*)\)",
    r"\b(?:official\s*)?m/?v\b",
    r"\bofficial\s*(?:music\s*)?video\b",
    r"\b4k\b|\b8k\b|\b1080p\b|\bhd\b",
]

# Separators between artist and title. 1theK / M2 uploads use an underscore.
SEPARATORS = [" - ", " – ", " — ", " _ ", " | "]

_Q = re.escape(QUOTE_CHARS)
QUOTED_RE = re.compile(
    r"^(?P<artist>[^" + _Q + r"]{1,80}?)\s*[" + _Q + r"]"
    r"(?P<title>[^" + _Q + r"]+)[" + _Q + r"]"
)

STRIP_EDGES = " -–—_|·:"


@dataclass
class ProbeResult:
    video_id: str
    url: str
    channel_id: str | None = None
    channel_name: str | None = None
    artist: str | None = None
    title: str | None = None
    type: str = "mv"
    label: str | None = None
    year: int | None = None
    duration: int | None = None
    thumbnail: str | None = None
    raw_title: str | None = None
    # high   = yt-dlp supplied real music metadata
    # medium = parsed out of the title
    # low    = no artist found; needs a human
    confidence: str = "low"
    needs_review: bool = True
    available_heights: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def parse_video_id(url_or_id: str) -> str | None:
    """Accepts a watch URL, youtu.be link, shorts/embed link, music.youtube
    link, or a bare 11-character id."""
    value = (url_or_id or "").strip()
    if not value:
        return None
    if VIDEO_ID_RE.match(value):
        return value

    parsed = urlparse(value if "//" in value else "https://" + value)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]

    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
        return candidate if VIDEO_ID_RE.match(candidate) else None

    if host.endswith("youtube.com"):
        qs = parse_qs(parsed.query)
        if "v" in qs and VIDEO_ID_RE.match(qs["v"][0]):
            return qs["v"][0]
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live", "v"}:
            return parts[1] if VIDEO_ID_RE.match(parts[1]) else None
    return None


def detect_type(raw_title: str | None, channel_name: str | None = None) -> str:
    haystack = (raw_title or "") + " " + (channel_name or "")
    for video_type, pattern in TYPE_PATTERNS:
        if re.search(pattern, haystack, re.IGNORECASE):
            return video_type
    return "mv"


def strip_noise(title: str) -> str:
    cleaned = title
    for pattern in NOISE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(STRIP_EDGES)


def split_artist_title(raw_title: str) -> tuple[str | None, str]:
    """Best-effort split. Returns (artist_or_None, title).

    Deliberately conservative: when nothing matches, the whole string becomes
    the title and the artist stays None, so the row is flagged for review
    rather than filled with a plausible-looking guess.
    """
    cleaned = strip_noise(raw_title)

    # Pattern: TWICE "Song Name" M/V — very common for K-pop label uploads.
    match = QUOTED_RE.match(cleaned)
    if match:
        artist = match.group("artist").strip(STRIP_EDGES)
        title = match.group("title").strip()
        if artist and title:
            return artist, title

    for separator in SEPARATORS:
        if separator in cleaned:
            left, right = cleaned.split(separator, 1)
            left, right = left.strip(STRIP_EDGES), right.strip(STRIP_EDGES)
            if left and right:
                return left, (strip_noise(right) or right)

    return None, (cleaned or raw_title.strip())


def _first(value):
    """yt-dlp returns either a scalar or a list depending on the field."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def extract_metadata(info: dict) -> ProbeResult:
    """Pure: turn a yt-dlp info dict into a ProbeResult."""
    video_id = info.get("id") or ""
    raw_title = info.get("title") or ""
    channel_name = info.get("channel") or info.get("uploader")

    video_type = detect_type(raw_title, channel_name)

    # yt-dlp's music metadata is the only trustworthy source; prefer it.
    artist = _first(info.get("artists")) or info.get("artist") or info.get("creator")
    track = info.get("track")

    if artist and track:
        title, confidence = track, "high"
    elif artist:
        title, confidence = strip_noise(raw_title), "high"
    else:
        parsed_artist, title = split_artist_title(raw_title)
        artist = parsed_artist
        confidence = "medium" if parsed_artist else "low"

    # NEVER fall back to the channel name for artist. For K-pop the channel is
    # the label (HYBE LABELS, SMTOWN, JYP), so that fallback is actively wrong
    # and would quietly organise the library by record company.

    year = info.get("release_year")
    upload_date = info.get("upload_date") or ""
    if not year and upload_date[:4].isdigit():
        year = int(upload_date[:4])

    heights = sorted(
        {f["height"] for f in (info.get("formats") or []) if f.get("height")}
    )

    return ProbeResult(
        video_id=video_id,
        url=info.get("webpage_url")
        or "https://www.youtube.com/watch?v=" + video_id,
        channel_id=info.get("channel_id"),
        channel_name=channel_name,
        artist=(artist or "").strip() or None,
        title=(title or "").strip() or None,
        type=video_type if video_type in VIDEO_TYPES else "mv",
        year=int(year) if year else None,
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
        raw_title=raw_title,
        confidence=confidence,
        needs_review=not bool(artist),
        available_heights=heights,
    )


def apply_channel_defaults(result: ProbeResult) -> ProbeResult:
    """Layer the channel's remembered label/type on top of what was detected.

    The label is a property of the channel, not the video, so it never has to
    be typed twice for the same channel.
    """
    if not result.channel_id:
        return result
    with get_conn() as conn:
        row = conn.execute(
            "SELECT default_label, default_type FROM channels WHERE channel_id = ?",
            (result.channel_id,),
        ).fetchone()
    if not row:
        return result
    if row["default_label"] and not result.label:
        result.label = row["default_label"]
    # A type detected from the title is more specific than a channel default,
    # so the default only fills in when detection found nothing but "mv".
    if row["default_type"] and result.type == "mv":
        result.type = row["default_type"]
    return result


def fetch_info(url: str) -> dict:
    """Metadata only — no download."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def probe(url_or_id: str, *, with_defaults: bool = True) -> ProbeResult:
    video_id = parse_video_id(url_or_id)
    if not video_id:
        raise ValueError("not a recognisable YouTube video URL or id: " + repr(url_or_id))
    info = fetch_info("https://www.youtube.com/watch?v=" + video_id)
    result = extract_metadata(info)
    return apply_channel_defaults(result) if with_defaults else result


async def probe_async(url_or_id: str, *, with_defaults: bool = True) -> ProbeResult:
    """`probe` off the event loop — the yt-dlp lookup is blocking network I/O
    and would otherwise stall every other request for a second or two."""
    # Raise a bad URL immediately rather than paying for a thread hop first.
    if not parse_video_id(url_or_id):
        raise ValueError("not a recognisable YouTube video URL or id: " + repr(url_or_id))
    return await asyncio.to_thread(probe, url_or_id, with_defaults=with_defaults)
