"""HTML routes for the browser.

These render HTMX partials; `routes_api` renders JSON for the addon. Neither
holds business logic — both call `services.videos`, so the two representations
cannot drift apart.
"""

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db import TYPE_TAGS, get_conn
from app.services import probe as probe_service
from app.services import profiles as profiles_service
from app.services import videos

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

TYPE_OPTIONS = list(TYPE_TAGS.items())

# Ordered by how widely each codec is hardware-decoded, not by efficiency:
# AV1 compresses better but VP9 direct-plays on far more Jellyfin clients.
CODEC_OPTIONS = [
    ("vp9", "VP9 — widest hardware support (recommended for 4K)"),
    ("av01", "AV1 — smaller files, patchier device support"),
    ("h264", "H.264 — plays everywhere, capped at 1080p on YouTube"),
]

FILTERS = [
    ("all", "All", {}),
    ("review", "Needs review", {"needs_review": True}),
    ("queued", "Queued", {"status": "queued"}),
    ("failed", "Failed", {"status": "failed"}),
    ("deleted", "Deleted", {"status": "deleted"}),
]


def _thumb_url(row: dict) -> str | None:
    if not row.get("thumb_path"):
        return None
    try:
        relative = Path(row["thumb_path"]).relative_to(settings.media_root)
    except ValueError:
        return None
    return "/media/" + quote(relative.as_posix())


def _resolution(row: dict) -> str | None:
    """2160p60 — frame rate appended only when it is worth knowing."""
    height = row.get("downloaded_height")
    if not height:
        return None
    fps = row.get("downloaded_fps")
    return f"{height}p{int(fps)}" if fps and fps >= 50 else f"{height}p"


def _size(row: dict) -> str | None:
    size = row.get("filesize")
    if not size:
        return None
    megabytes = size / 1_048_576
    if megabytes >= 1024:
        return f"{megabytes / 1024:.1f} GB"
    return f"{megabytes:.0f} MB"


def _duration(row: dict) -> str | None:
    seconds = row.get("duration")
    if not seconds:
        return None
    minutes, remainder = divmod(int(seconds), 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{remainder:02d}"
    return f"{minutes}:{remainder:02d}"


def _codec(row: dict) -> str | None:
    """av01.0.01M.08 -> av01"""
    return (row.get("downloaded_vcodec") or "").split(".")[0] or None


def _quality(row: dict) -> str:
    height = row.get("downloaded_height")
    if not height:
        return "—"
    codec = (row.get("downloaded_vcodec") or "").split(".")[0] or "?"
    parts = [f"{height}p", codec]
    fps = row.get("downloaded_fps")
    if fps and fps >= 50:
        parts.insert(1, f"{int(fps)}fps")
    size = row.get("filesize")
    if size:
        parts.append(f"{size / 1_048_576:.0f} MB")
    return " · ".join(parts)


def to_view(row: dict) -> dict:
    """Decorate a database row with the few derived values the templates need."""
    return row | {
        "thumb_url": _thumb_url(row),
        "quality": _quality(row),
        "resolution": _resolution(row),
        "size_label": _size(row),
        "codec": _codec(row),
        "duration_label": _duration(row),
        "type_label": TYPE_TAGS.get(row.get("type"), row.get("type") or "—"),
    }


def _counts() -> dict[str, int]:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM videos").fetchone()["c"]
        by_status = {
            r["status"]: r["c"]
            for r in conn.execute(
                "SELECT status, COUNT(*) c FROM videos GROUP BY status"
            )
        }
        review = conn.execute(
            "SELECT COUNT(*) c FROM videos WHERE needs_review = 1"
            " AND status <> 'deleted'"
        ).fetchone()["c"]
    return {
        "all": total,
        "review": review,
        "queued": by_status.get("queued", 0) + by_status.get("downloading", 0),
        "failed": by_status.get("failed", 0),
        "deleted": by_status.get("deleted", 0),
    }


def _row_response(request: Request, video_id: str) -> HTMLResponse:
    row = videos.get(video_id)
    return templates.TemplateResponse(
        request=request, name="_row.html", context={"v": to_view(row)}
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, filter: str = "all"):
    selected = next((f for f in FILTERS if f[0] == filter), FILTERS[0])
    rows = videos.list_videos(**selected[2])
    counts = _counts()
    return templates.TemplateResponse(
        request=request,
        name="list.html",
        context={
            "videos": [to_view(r) for r in rows],
            "filters": [(key, text, counts[key]) for key, text, _ in FILTERS],
            "active_filter": selected[0],
            "type_options": TYPE_OPTIONS,
        },
    )


def _settle(request: Request, video_id: str):
    """HTMX wants the swapped row back; a plain form post wants a redirect.

    The same routes serve the library (inline, HTMX) and the detail page
    (ordinary forms), so they branch on the header HTMX sets.
    """
    if request.headers.get("HX-Request"):
        return _row_response(request, video_id)
    return RedirectResponse(f"/videos/{video_id}", status_code=303)


@router.get("/videos/{video_id}", response_class=HTMLResponse)
async def video_detail(request: Request, video_id: str):
    row = videos.get(video_id)
    if row is None:
        return HTMLResponse(
            '<p style="font:15px system-ui;padding:2rem">Unknown video. '
            '<a href="/">Back to the library</a></p>',
            status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={
            "v": to_view(row),
            "profile": profiles_service.get_profile(row["profile_id"])
            if row["profile_id"]
            else None,
            "type_options": TYPE_OPTIONS,
            "profile_options": profiles_service.list_profiles(),
        },
    )


@router.get("/videos/{video_id}/row", response_class=HTMLResponse)
async def video_row(request: Request, video_id: str):
    return _row_response(request, video_id)


@router.get("/videos/{video_id}/edit", response_class=HTMLResponse)
async def edit_form(request: Request, video_id: str):
    row = videos.get(video_id)
    return templates.TemplateResponse(
        request=request,
        name="_edit_form.html",
        context={
            "v": to_view(row),
            "type_options": TYPE_OPTIONS,
            "profile_options": profiles_service.list_profiles(),
        },
    )


@router.post("/videos/{video_id}", response_class=HTMLResponse)
async def save_video(
    request: Request,
    video_id: str,
    artist: str = Form(""),
    title: str = Form(""),
    video_type: str = Form("mv"),
    year: str = Form(""),
    version: str = Form(""),
    profile_id: str = Form(""),
):
    try:
        videos.update_metadata(
            video_id,
            artist=artist,
            title=title,
            video_type=video_type,
            year=int(year) if year.strip().isdigit() else None,
            version=version,
            profile_id=int(profile_id) if profile_id.strip().isdigit() else None,
        )
    except (videos.NotFound, videos.NotEditable) as exc:
        log.warning("edit refused for %s: %s", video_id, exc)
    return _settle(request, video_id)


@router.post("/videos/{video_id}/delete", response_class=HTMLResponse)
async def delete_video(request: Request, video_id: str):
    try:
        videos.delete_video(video_id)
    except (videos.NotFound, videos.NotEditable) as exc:
        log.warning("delete refused for %s: %s", video_id, exc)
    return _settle(request, video_id)


@router.post("/videos/{video_id}/redownload", response_class=HTMLResponse)
async def redownload_video(request: Request, video_id: str):
    """Fetch again with the current profile, keeping corrected metadata."""
    try:
        videos.requeue(video_id)
    except (videos.NotFound, videos.NotEditable) as exc:
        log.warning("redownload refused for %s: %s", video_id, exc)
    return _settle(request, video_id)


def _profiles_page(request: Request, editing: dict | None = None, error: str | None = None):
    return templates.TemplateResponse(
        request=request,
        name="profiles.html",
        context={
            "profiles": profiles_service.list_profiles(),
            "editing": editing,
            "error": error,
            "codec_options": CODEC_OPTIONS,
        },
    )


@router.get("/profiles", response_class=HTMLResponse)
async def profiles_page(request: Request):
    return _profiles_page(request)


@router.get("/profiles/{profile_id}/edit", response_class=HTMLResponse)
async def profile_edit(request: Request, profile_id: int):
    return _profiles_page(request, editing=profiles_service.get_profile(profile_id))


@router.post("/profiles")
async def profile_create(
    request: Request,
    name: str = Form(...),
    max_height: str = Form(""),
    prefer_codecs: str = Form(""),
    prefer_fps: str = Form(""),
    container: str = Form("mkv"),
    format_override: str = Form(""),
):
    try:
        profiles_service.create_profile(
            name=name,
            max_height=max_height,
            prefer_codecs=prefer_codecs,
            prefer_fps=prefer_fps,
            container=container,
            format_override=format_override,
        )
    except ValueError as exc:
        return _profiles_page(request, error=str(exc))
    return RedirectResponse("/profiles", status_code=303)


@router.post("/profiles/{profile_id}")
async def profile_update(
    profile_id: int,
    name: str = Form(...),
    max_height: str = Form(""),
    prefer_codecs: str = Form(""),
    prefer_fps: str = Form(""),
    container: str = Form("mkv"),
    format_override: str = Form(""),
):
    profiles_service.update_profile(
        profile_id,
        name=name,
        max_height=max_height,
        prefer_codecs=prefer_codecs,
        prefer_fps=prefer_fps,
        container=container,
        format_override=format_override,
    )
    return RedirectResponse("/profiles", status_code=303)


@router.post("/profiles/{profile_id}/default")
async def profile_make_default(profile_id: int):
    profiles_service.make_default(profile_id)
    return RedirectResponse("/profiles", status_code=303)


@router.post("/profiles/{profile_id}/delete")
async def profile_delete(request: Request, profile_id: int):
    try:
        profiles_service.delete_profile(profile_id)
    except profiles_service.InUse as exc:
        return _profiles_page(request, error=str(exc))
    return RedirectResponse("/profiles", status_code=303)


@router.get("/add", response_class=HTMLResponse)
async def add_page(request: Request):
    return templates.TemplateResponse(request=request, name="add.html", context={})


@router.post("/add/probe", response_class=HTMLResponse)
async def add_probe(request: Request, url: str = Form(...)):
    context: dict = {
        "type_options": TYPE_OPTIONS,
        "profile_options": profiles_service.list_profiles(),
    }
    try:
        result = await probe_service.probe_async(url)
        context["p"] = result
    except ValueError as exc:
        context["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - surface lookup failures in the form
        log.exception("probe failed for %s", url)
        context["error"] = f"Lookup failed: {exc}"
    return templates.TemplateResponse(
        request=request, name="_add_confirm.html", context=context
    )


@router.post("/add", response_class=HTMLResponse)
async def add_submit(
    request: Request,
    video_id: str = Form(...),
    artist: str = Form(""),
    title: str = Form(""),
    video_type: str = Form("mv"),
    year: str = Form(""),
    channel_id: str = Form(""),
    channel_name: str = Form(""),
    duration: str = Form(""),
    profile_id: str = Form(""),
):
    videos.enqueue(
        video_id=video_id,
        artist=artist.strip() or None,
        title=title.strip() or None,
        video_type=video_type,
        year=int(year) if year.strip().isdigit() else None,
        duration=int(duration) if duration.strip().isdigit() else None,
        channel_id=channel_id.strip() or None,
        channel_name=channel_name.strip() or None,
        needs_review=not artist.strip(),
        source="web",
        profile_id=int(profile_id) if profile_id.strip().isdigit() else None,
    )
    return HTMLResponse(
        '<div class="panel">Queued. '
        '<a href="/">Back to the library</a></div>'
    )
