"""JSON routes for the Firefox addon.

Mirrors `routes_web`, which renders the same operations as HTML. Both call
`services.videos`; neither owns business logic.
"""

import logging
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.db import default_profile_id
from app.services import probe as probe_service
from app.services import profiles as profiles_service
from app.services import videos

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


async def require_token(authorization: str | None = Header(default=None)) -> None:
    """Shared-secret auth. The token is the boundary here, not the origin —
    which is why CORS can be permissive."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    supplied = authorization[len("Bearer ") :]
    # Constant-time compare so the token cannot be recovered by timing.
    if not secrets.compare_digest(supplied, settings.api_token):
        raise HTTPException(status_code=401, detail="invalid token")


class EnqueueRequest(BaseModel):
    video_id: str | None = None
    url: str | None = None
    artist: str | None = None
    title: str | None = None
    video_type: str = Field(default="mv", alias="type")
    year: int | None = None
    duration: int | None = None
    channel_id: str | None = None
    channel_name: str | None = None
    # Omitted means "use the global default".
    profile_id: int | None = None

    model_config = {"populate_by_name": True}


def _public(row: dict) -> dict:
    """Only what the addon needs — no absolute filesystem paths."""
    return {
        "video_id": row["video_id"],
        "status": row["status"],
        "artist": row["artist"],
        "title": row["title"],
        "type": row["type"],
        "channel_name": row.get("channel_name"),
        "needs_review": bool(row["needs_review"]),
        "downloaded_height": row["downloaded_height"],
        "profile_id": row["profile_id"],
        "profile_name": row.get("profile_name"),
        "error": row["error"],
    }


@router.get("/probe", dependencies=[Depends(require_token)])
async def probe_endpoint(url: str = Query(..., description="YouTube URL or video id")):
    """Prefill payload for the addon popup and the web add form.

    Metadata only — nothing is downloaded and nothing is written.
    """
    try:
        result = await probe_service.probe_async(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("probe failed for %s", url)
        raise HTTPException(status_code=502, detail=f"lookup failed: {exc}") from exc

    payload = result.as_dict()
    existing = videos.get(result.video_id)
    payload["existing"] = _public(existing) if existing else None
    return payload


@router.get("/profiles", dependencies=[Depends(require_token)])
async def list_profiles_endpoint():
    """Download profiles, for the addon's profile picker."""
    return {
        "default_profile_id": default_profile_id(),
        "profiles": [
            {
                "id": p["id"],
                "name": p["name"],
                "summary": p["summary"],
                "is_default": bool(p["is_default"]),
            }
            for p in profiles_service.list_profiles()
        ],
    }


@router.get("/videos", dependencies=[Depends(require_token)])
async def bulk_status(ids: str = Query(..., description="comma-separated video ids")):
    """Bulk lookup, so a channel or playlist page can be marked up in one call."""
    wanted = [part.strip() for part in ids.split(",") if part.strip()]
    found = videos.get_many(wanted)
    return {
        "videos": {
            video_id: _public(found[video_id])
            for video_id in wanted
            if video_id in found
        }
    }


@router.get("/videos/{video_id}", dependencies=[Depends(require_token)])
async def video_status(video_id: str):
    row = videos.get(video_id)
    if row is None:
        # The addon reads 404 as "never had this one".
        raise HTTPException(status_code=404, detail="unknown video")
    return _public(row)


@router.post("/videos", status_code=202, dependencies=[Depends(require_token)])
async def enqueue_endpoint(payload: EnqueueRequest):
    video_id = payload.video_id or probe_service.parse_video_id(payload.url or "")
    if not video_id:
        raise HTTPException(
            status_code=400, detail="a valid video_id or url is required"
        )

    row = videos.enqueue(
        video_id=video_id,
        artist=payload.artist,
        title=payload.title,
        video_type=payload.video_type,
        year=payload.year,
        duration=payload.duration,
        channel_id=payload.channel_id,
        channel_name=payload.channel_name,
        needs_review=not bool(payload.artist),
        source="addon",
        profile_id=payload.profile_id,
    )
    return _public(row) | {"already_present": row.get("already_present", False)}
