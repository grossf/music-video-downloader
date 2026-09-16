"""HTML routes for the browser.

These render HTMX partials; `routes_api` renders JSON for the addon. Neither
holds business logic — both call `services.videos`, so the two representations
cannot drift apart.
"""

import logging
from pathlib import Path
from urllib.parse import quote, urlencode

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

# One or two screens of rows. Big enough that most searches fit on one page,
# small enough that a page never loads hundreds of thumbnails.
PAGE_SIZE = 50

FILTERS = [
    ("all", "All", {}),
    ("review", "Needs review", {"needs_review": True}),
    # In flight: a video that has started downloading is still waiting to land.
    ("queued", "Queued", {"status": ("queued", "downloading")}),
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


def _counts(q: str | None = None, artist: str | None = None) -> dict[str, int]:
    clauses, params = videos.search_clauses(q, artist)
    search = "".join(" AND " + c for c in clauses)
    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) c FROM videos v WHERE v.status <> 'deleted'{search}",
            params,
        ).fetchone()["c"]
        by_status = {
            r["status"]: r["c"]
            for r in conn.execute(
                f"SELECT v.status, COUNT(*) c FROM videos v WHERE 1=1{search}"
                " GROUP BY v.status",
                params,
            )
        }
        review = conn.execute(
            "SELECT COUNT(*) c FROM videos v WHERE v.needs_review = 1"
            f" AND v.status <> 'deleted'{search}",
            params,
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


def _library_href(
    filter: str = "all", q: str = "", artist: str = "", page: int = 1
) -> str:
    params = {
        "filter": filter if filter != "all" else "",
        "q": q,
        "artist": artist,
        "page": page if page > 1 else "",
    }
    params = {k: v for k, v in params.items() if v}
    return "/?" + urlencode(params) if params else "/"


def _page_numbers(page: int, pages: int) -> list[int | None]:
    """1 … 4 5 6 … 20 — the first, the last and the neighbours of the current
    page, with None marking a gap. A gap of exactly one page shows that page
    instead, since "…" would take the same space and say less."""
    wanted = {1, pages, page - 1, page, page + 1}
    shown = sorted(n for n in wanted if 1 <= n <= pages)
    out: list[int | None] = []
    for n in shown:
        if out and n - out[-1] == 2:
            out.append(n - 1)
        elif out and n - out[-1] > 2:
            out.append(None)
        out.append(n)
    return out


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    filter: str = "all",
    q: str = "",
    artist: str = "",
    page: str = "1",
):
    q, artist = q.strip(), artist.strip()
    selected = next((f for f in FILTERS if f[0] == filter), FILTERS[0])
    counts = _counts(q, artist)

    total = counts[selected[0]]
    pages = max(1, -(-total // PAGE_SIZE))
    # A stale link (say page 5 after deleting half the library) lands on the
    # last page rather than an empty one.
    page_no = min(max(1, int(page) if page.isdigit() else 1), pages)
    rows = videos.list_videos(
        **selected[2], q=q, artist=artist,
        limit=PAGE_SIZE, offset=(page_no - 1) * PAGE_SIZE,
    )
    return templates.TemplateResponse(
        request=request,
        name="list.html",
        context={
            "videos": [to_view(r) for r in rows],
            # A tab with nothing in it is hidden, unless it is All or the one
            # being viewed. Failures and videos needing review thus appear
            # only when there is something to look at.
            "filters": [
                (key, text, counts[key], _library_href(key, q, artist))
                for key, text, _ in FILTERS
                if key in ("all", selected[0]) or counts[key]
            ],
            "active_filter": selected[0],
            "q": q,
            "artist": artist,
            # Removing the artist chip keeps the tab and the typed search.
            "clear_artist_href": _library_href(selected[0], q),
            "clear_search_href": _library_href(selected[0]),
            "page": page_no,
            "pages": pages,
            "total": total,
            "first_shown": (page_no - 1) * PAGE_SIZE + 1 if total else 0,
            "last_shown": min(page_no * PAGE_SIZE, total),
            "page_links": [
                (n, _library_href(selected[0], q, artist, n) if n else None)
                for n in _page_numbers(page_no, pages)
            ],
            "prev_href": _library_href(selected[0], q, artist, page_no - 1)
            if page_no > 1 else None,
            "next_href": _library_href(selected[0], q, artist, page_no + 1)
            if page_no < pages else None,
            "active_filter_label": selected[1],
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
    profile_id: str = Form(""),
):
    try:
        videos.update_metadata(
            video_id,
            artist=artist,
            title=title,
            video_type=video_type,
            year=int(year) if year.strip().isdigit() else None,
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
