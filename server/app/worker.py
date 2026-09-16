"""Download queue.

The videos table *is* the queue — at this scale a broker would add a container
and a class of failure modes without buying anything. Workers claim a row with
a conditional UPDATE, so two of them never take the same job.

Downloads land in a staging directory first. The final path depends on
metadata we may not trust yet, and staging means a failed download never
leaves a half-written file inside the Jellyfin library.
"""

import asyncio
import json
import logging
import shutil
from pathlib import Path

import yt_dlp

from app.config import settings
from app.db import get_conn
from app.services import jellyfin, videos
from app.services.formats import compile_selector, describe
from app.services.naming import build_paths, place_file
from app.services.nfo import write_nfo
from app.services.probe import dates_from

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5
MEDIA_EXTENSIONS = {".mkv", ".mp4", ".webm", ".m4a", ".opus", ".mov"}
THUMB_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def claim_next() -> dict | None:
    """Atomically take the oldest queued row. The conditional UPDATE is what
    makes this safe with DOWNLOAD_CONCURRENCY > 1."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT video_id FROM videos WHERE status = 'queued'"
            " ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        cursor = conn.execute(
            "UPDATE videos SET status = 'downloading', updated_at = datetime('now')"
            " WHERE video_id = ? AND status = 'queued'",
            (row["video_id"],),
        )
        if cursor.rowcount == 0:
            return None  # another worker got it first
        video_id = row["video_id"]
    # Re-read through the service so the row carries its joined channel and
    # profile names, which a bare SELECT * on videos does not have.
    return videos.get(video_id)


def load_profile(profile_id: int | None) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    return dict(row) if row else {}


def build_ydl_opts(profile: dict, staging: Path) -> dict:
    postprocessing = {}
    try:
        postprocessing = json.loads(profile.get("postprocessing") or "{}")
    except json.JSONDecodeError:
        log.warning("profile %s has invalid postprocessing json", profile.get("id"))

    compiled = compile_selector(profile)
    log.info(
        "profile %r (%s): %s",
        profile.get("name", "?"),
        compiled["source"],
        describe(profile),
    )

    opts = {
        "format": compiled["format"],
        "format_sort": compiled["format_sort"],
        "merge_output_format": compiled["merge_output_format"],
        "outtmpl": {"default": str(staging / "%(id)s.%(ext)s")},
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "postprocessors": [],
    }

    if postprocessing.get("thumbnail", True):
        opts["writethumbnail"] = True
        # Jellyfin is reliable with jpg; webp support varies by client.
        opts["postprocessors"].append(
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"}
        )

    # Deliberately NOT embedding metadata into the container: it would write
    # yt-dlp's raw title, which is exactly the unreliable value the review
    # workflow exists to correct. The NFO is the authoritative record.
    return opts


def _find_media(staging: Path, video_id: str) -> Path | None:
    candidates = [
        p
        for p in staging.glob(video_id + ".*")
        if p.suffix.lower() in MEDIA_EXTENSIONS
    ]
    if not candidates:
        return None
    # Largest wins, so a leftover audio-only fragment never beats the mux.
    return max(candidates, key=lambda p: p.stat().st_size)


def _find_thumb(staging: Path, video_id: str) -> Path | None:
    for ext in THUMB_EXTENSIONS:
        candidate = staging / (video_id + ext)
        if candidate.exists():
            return candidate
    return None


def download(video: dict) -> dict:
    """Blocking. Runs in a worker thread."""
    video_id = video["video_id"]
    staging = settings.staging_dir / video_id
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    profile = load_profile(video.get("profile_id"))
    opts = build_ydl_opts(profile, staging)
    url = "https://www.youtube.com/watch?v=" + video_id

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    media = _find_media(staging, video_id)
    if media is None:
        raise RuntimeError("yt-dlp produced no media file in " + str(staging))

    requested = (info.get("requested_downloads") or [{}])[0]
    upload_date, release_date = dates_from(info)

    def pick(key):
        return requested.get(key) if requested.get(key) is not None else info.get(key)

    return {
        "media": media,
        "thumb": _find_thumb(staging, video_id),
        "staging": staging,
        "height": pick("height"),
        "vcodec": pick("vcodec"),
        "acodec": pick("acodec"),
        "fps": pick("fps"),
        "duration": info.get("duration"),
        "year": info.get("release_year"),
        "upload_date": upload_date,
        "release_date": release_date,
        "plot": info.get("description"),
        "filesize": media.stat().st_size,
    }


def finalise(video: dict, result: dict) -> dict:
    """Move the download into the library and write its NFO.

    The row's metadata wins over anything yt-dlp reported — it may already
    have been corrected by hand at capture time.
    """
    video_id = video["video_id"]
    target = build_paths(
        video_id=video_id,
        artist=video.get("artist"),
        title=video.get("title"),
        video_type=video.get("type") or "mv",
        ext=result["media"].suffix.lstrip("."),
    )

    placed = place_file(
        source_media=result["media"],
        target=target,
        source_thumb=result.get("thumb"),
    )

    write_nfo(
        placed.nfo,
        video_id=video_id,
        title=video.get("title"),
        artist=video.get("artist"),
        year=video.get("year") or result.get("year"),
        premiered=result.get("release_date") or result.get("upload_date"),
        video_type=video.get("type") or "mv",
        studio=video.get("channel_name"),
        plot=(result.get("plot") or "")[:2000] or None,
        runtime=video.get("duration") or result.get("duration"),
        thumb_name=placed.thumb.name if placed.thumb.exists() else None,
    )

    shutil.rmtree(result["staging"], ignore_errors=True)

    return {
        "file_path": str(placed.media),
        "nfo_path": str(placed.nfo),
        "thumb_path": str(placed.thumb) if placed.thumb.exists() else None,
        "downloaded_height": result.get("height"),
        "downloaded_vcodec": result.get("vcodec"),
        "downloaded_acodec": result.get("acodec"),
        "downloaded_fps": result.get("fps"),
        "filesize": result.get("filesize"),
        "duration": video.get("duration") or result.get("duration"),
        "upload_date": result.get("upload_date"),
        "release_date": result.get("release_date"),
    }


def process(video: dict) -> None:
    video_id = video["video_id"]
    try:
        log.info("downloading %s", video_id)
        result = download(video)
        fields = finalise(video, result)
        videos.mark_done(video_id, **fields)
        log.info(
            "done %s -> %s (%sp %s)",
            video_id,
            fields["file_path"],
            fields.get("downloaded_height"),
            fields.get("downloaded_vcodec"),
        )
        jellyfin.refresh()
    except Exception as exc:  # noqa: BLE001 - the queue must survive any failure
        log.exception("failed %s", video_id)
        videos.mark_failed(video_id, f"{type(exc).__name__}: {exc}")


async def worker_loop(name: str, stop: asyncio.Event) -> None:
    log.info("worker %s started", name)
    while not stop.is_set():
        video = claim_next()
        if video is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue
        await asyncio.to_thread(process, video)
    log.info("worker %s stopped", name)


def requeue_interrupted() -> int:
    """A row left in 'downloading' means the process died mid-job."""
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE videos SET status = 'queued', updated_at = datetime('now')"
            " WHERE status = 'downloading'"
        )
        return cursor.rowcount
