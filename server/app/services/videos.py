"""Video lifecycle: enqueue, read, and channel-default bookkeeping.

Both `routes_api` (JSON, for the addon) and `routes_web` (HTML, for the
browser) call into this module. Neither of them holds business logic, so the
two representations never drift apart.
"""

import logging
from pathlib import Path
from typing import Any

from app.db import default_profile_id, get_conn
from app.services import jellyfin
from app.services.naming import build_paths, place_file, prune_empty_dir
from app.services.nfo import write_nfo

log = logging.getLogger(__name__)


class NotFound(Exception):
    """No such video."""


class NotEditable(Exception):
    """The video is mid-flight; editing or deleting it would race the worker."""

NOW = "datetime('now')"

# Every read joins the profile so callers can show which download template a
# video used. LEFT JOIN, not INNER: a video whose profile was deleted must
# still be listed rather than vanishing from the library.
VIDEO_SELECT = (
    "SELECT v.*, p.name AS profile_name FROM videos v"
    " LEFT JOIN profiles p ON p.id = v.profile_id"
)


def _row_to_dict(row) -> dict | None:
    return dict(row) if row is not None else None


def get(video_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            f"{VIDEO_SELECT} WHERE v.video_id = ?", (video_id,)
        ).fetchone()
    return _row_to_dict(row)


def get_many(video_ids: list[str]) -> dict[str, dict]:
    if not video_ids:
        return {}
    placeholders = ", ".join("?" for _ in video_ids)
    with get_conn() as conn:
        rows = conn.execute(
            f"{VIDEO_SELECT} WHERE v.video_id IN ({placeholders})", video_ids
        ).fetchall()
    return {row["video_id"]: dict(row) for row in rows}


def list_videos(
    *, status: str | None = None, needs_review: bool | None = None, limit: int = 500
) -> list[dict]:
    clauses, params = [], []
    if status:
        clauses.append("v.status = ?")
        params.append(status)
    if needs_review is not None:
        clauses.append("v.needs_review = ?")
        params.append(1 if needs_review else 0)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"{VIDEO_SELECT} {where} ORDER BY v.created_at DESC LIMIT ?", params
        ).fetchall()
    return [dict(r) for r in rows]


def remember_channel(
    channel_id: str | None,
    *,
    name: str | None = None,
    label: str | None = None,
    video_type: str | None = None,
    profile_id: int | None = None,
) -> None:
    """Record a channel and fill in its defaults.

    Called as a side effect of submitting a form — there is deliberately no
    channel admin screen. If maintaining channels were its own chore it would
    not happen, and the "converges to one click" property would be lost.
    Existing defaults are never overwritten, only filled in when empty.
    """
    if not channel_id:
        return
    # "mv" is the detection fallback, not a signal. Recording it would look
    # like a deliberate choice and, because defaults are only ever filled in
    # when empty, would permanently block the channel from learning a real one.
    if video_type == "mv":
        video_type = None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT channel_id, name, default_label, default_type,"
            " default_profile_id FROM channels WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO channels (channel_id, name, default_label,"
                " default_type, default_profile_id) VALUES (?, ?, ?, ?, ?)",
                (channel_id, name, label, video_type, profile_id),
            )
            log.info(
                "learned channel %s (%s) label=%s type=%s profile=%s",
                channel_id, name, label, video_type, profile_id,
            )
            return

        updates, params = [], []
        if name and not row["name"]:
            updates.append("name = ?")
            params.append(name)
        if label and not row["default_label"]:
            updates.append("default_label = ?")
            params.append(label)
        if video_type and not row["default_type"]:
            updates.append("default_type = ?")
            params.append(video_type)
        if profile_id and not row["default_profile_id"]:
            updates.append("default_profile_id = ?")
            params.append(profile_id)
        if updates:
            params.append(channel_id)
            conn.execute(
                f"UPDATE channels SET {', '.join(updates)}, updated_at = {NOW}"
                " WHERE channel_id = ?",
                params,
            )


def enqueue(
    *,
    video_id: str,
    artist: str | None = None,
    title: str | None = None,
    video_type: str = "mv",
    label: str | None = None,
    year: int | None = None,
    duration: int | None = None,
    channel_id: str | None = None,
    channel_name: str | None = None,
    needs_review: bool = False,
    source: str = "web",
    profile_id: int | None = None,
) -> dict[str, Any]:
    """Queue a video for download.

    Re-queues a previously failed or deleted row. A video already on disk is
    returned untouched — re-downloading it is the upgrade worker's job, not
    this one's.
    """
    # Only a profile that differs from the global default counts as a choice
    # worth remembering. Recording the default itself would look deliberate and
    # pin the channel to today's default forever — the same trap that made
    # default_type stick on the fallback value.
    global_default = default_profile_id()
    deliberate_profile = profile_id if profile_id and profile_id != global_default else None

    remember_channel(
        channel_id,
        name=channel_name,
        label=label,
        video_type=video_type,
        profile_id=deliberate_profile,
    )
    profile_id = profile_id or global_default
    existing = get(video_id)

    if existing and existing["status"] in ("done", "queued", "downloading"):
        return existing | {"already_present": True}

    fields = {
        "video_id": video_id,
        "channel_id": channel_id,
        "profile_id": profile_id,
        "artist": artist,
        "title": title,
        "type": video_type,
        "label": label,
        "year": year,
        "duration": duration,
        "status": "queued",
        "needs_review": 1 if needs_review else 0,
        "source": source,
        "error": None,
    }

    with get_conn() as conn:
        if existing:
            assignments = ", ".join(f"{k} = :{k}" for k in fields if k != "video_id")
            conn.execute(
                f"UPDATE videos SET {assignments}, updated_at = {NOW}"
                " WHERE video_id = :video_id",
                fields,
            )
        else:
            columns = ", ".join(fields)
            placeholders = ", ".join(f":{k}" for k in fields)
            conn.execute(
                f"INSERT INTO videos ({columns}) VALUES ({placeholders})", fields
            )
    log.info("queued %s (%s - %s)", video_id, artist, title)
    return get(video_id) | {"already_present": False}


def requeue(video_id: str, *, profile_id: int | None = None) -> dict:
    """Download a video again, keeping the metadata already corrected by hand.

    Used to pick up a better quality profile. The file is replaced in place;
    the NFO and any manual artist/title fixes survive untouched.
    """
    row = get(video_id)
    if row is None:
        raise NotFound(video_id)
    if row["status"] in ("queued", "downloading"):
        raise NotEditable(f"{video_id} is already {row['status']}")

    with get_conn() as conn:
        conn.execute(
            "UPDATE videos SET status = 'queued', error = NULL, profile_id = ?,"
            f" updated_at = {NOW} WHERE video_id = ?",
            (profile_id or row["profile_id"] or default_profile_id(), video_id),
        )
    log.info("requeued %s for re-download", video_id)
    return get(video_id)


def _blank_to_none(value: str | None) -> str | None:
    return (value or "").strip() or None


def update_metadata(
    video_id: str,
    *,
    artist: str | None = None,
    title: str | None = None,
    video_type: str | None = None,
    label: str | None = None,
    year: int | None = None,
    version: str | None = None,
    profile_id: int | None = None,
    refresh_jellyfin: bool = True,
) -> dict:
    """Correct a video's metadata, then make the library match.

    Renaming reuses `place_file` — the same function the initial download uses
    — so "put this video where its metadata says it belongs" has exactly one
    implementation.
    """
    row = get(video_id)
    if row is None:
        raise NotFound(video_id)
    if row["status"] != "done":
        raise NotEditable(
            f"cannot edit a video with status {row['status']!r}; only completed "
            "downloads can be edited"
        )

    artist = _blank_to_none(artist)
    title = _blank_to_none(title)
    label = _blank_to_none(label)
    version = _blank_to_none(version)
    video_type = video_type or row["type"]

    old_media = Path(row["file_path"]) if row["file_path"] else None
    old_nfo = Path(row["nfo_path"]) if row["nfo_path"] else None
    old_thumb = Path(row["thumb_path"]) if row["thumb_path"] else None

    ext = old_media.suffix.lstrip(".") if old_media else "mkv"
    target = build_paths(
        video_id=video_id,
        artist=artist,
        title=title,
        video_type=video_type,
        version=version,
        ext=ext,
    )

    if old_media and old_media.exists():
        placed = place_file(
            source_media=old_media, target=target, source_thumb=old_thumb
        )
    else:
        # Row says done but the file is gone (moved or deleted outside the app).
        # Still record the corrected metadata rather than refusing the edit.
        log.warning("media file missing for %s; updating metadata only", video_id)
        placed = target

    write_nfo(
        placed.nfo,
        video_id=video_id,
        title=title,
        artist=artist,
        year=year if year is not None else row["year"],
        # Carried through explicitly: an edit rewrites the whole NFO, so
        # anything not passed here is silently lost from the file.
        premiered=row["release_date"] or row["upload_date"],
        video_type=video_type,
        label=label,
        runtime=row["duration"],
        thumb_name=placed.thumb.name if placed.thumb.exists() else None,
    )
    if old_nfo and old_nfo.exists() and old_nfo != placed.nfo:
        old_nfo.unlink()
        if old_media:
            prune_empty_dir(old_nfo.parent)

    remember_channel(row["channel_id"], label=label, video_type=video_type)

    fields = {
        "artist": artist,
        "title": title,
        "type": video_type,
        "label": label,
        "version": version,
        "year": year if year is not None else row["year"],
        "file_path": str(placed.media),
        "nfo_path": str(placed.nfo),
        "thumb_path": str(placed.thumb) if placed.thumb.exists() else None,
        # An artist is the thing review exists to supply; having one clears it.
        "needs_review": 0 if artist else 1,
        # Changing the profile does not re-download on its own — it takes
        # effect the next time this video is fetched.
        "profile_id": profile_id or row["profile_id"],
        "video_id": video_id,
    }
    assignments = ", ".join(f"{k} = :{k}" for k in fields if k != "video_id")
    with get_conn() as conn:
        conn.execute(
            f"UPDATE videos SET {assignments}, updated_at = {NOW}"
            " WHERE video_id = :video_id",
            fields,
        )

    log.info("updated %s -> %s", video_id, placed.media)
    if refresh_jellyfin:
        jellyfin.refresh()
    return get(video_id)


def delete_video(video_id: str, *, refresh_jellyfin: bool = True) -> dict:
    """Remove the files but keep the row as a tombstone.

    Hard-deleting the row would make the addon report the video as "not
    downloaded" again, so the next visit to that page would re-download
    something that was deliberately removed.
    """
    row = get(video_id)
    if row is None:
        raise NotFound(video_id)
    if row["status"] in ("queued", "downloading"):
        raise NotEditable(
            f"cannot delete a video with status {row['status']!r}; it would race "
            "the download worker"
        )

    directories = set()
    for key in ("file_path", "nfo_path", "thumb_path"):
        raw = row[key]
        if not raw:
            continue
        path = Path(raw)
        directories.add(path.parent)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("could not remove %s: %s", path, exc)

    for directory in directories:
        prune_empty_dir(directory)

    with get_conn() as conn:
        conn.execute(
            "UPDATE videos SET status = 'deleted', file_path = NULL,"
            " nfo_path = NULL, thumb_path = NULL, filesize = NULL,"
            f" updated_at = {NOW} WHERE video_id = ?",
            (video_id,),
        )

    log.info("deleted %s (tombstoned)", video_id)
    if refresh_jellyfin:
        jellyfin.refresh()
    return get(video_id)


def mark_failed(video_id: str, error: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE videos SET status = 'failed', error = ?,"
            " retry_count = retry_count + 1,"
            f" updated_at = {NOW} WHERE video_id = ?",
            (error[:2000], video_id),
        )


def mark_done(video_id: str, **fields) -> None:
    assignments = ", ".join(f"{k} = :{k}" for k in fields)
    params = fields | {"video_id": video_id}
    with get_conn() as conn:
        conn.execute(
            f"UPDATE videos SET status = 'done', error = NULL, {assignments},"
            f" updated_at = {NOW} WHERE video_id = :video_id",
            params,
        )
