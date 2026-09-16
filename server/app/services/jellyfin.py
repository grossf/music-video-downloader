"""Jellyfin library refresh.

A no-op when JELLYFIN_URL / JELLYFIN_API_KEY are unset, so the app runs fine
without Jellyfin configured. Failures here are logged and swallowed: Jellyfin
being unreachable must never fail a download that already succeeded.
"""

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0


def is_configured() -> bool:
    return bool(settings.jellyfin_url and settings.jellyfin_api_key)


def refresh() -> bool:
    """Ask Jellyfin to rescan its libraries. Returns True if the call was made
    and accepted."""
    if not is_configured():
        log.debug("jellyfin not configured; skipping refresh")
        return False

    url = settings.jellyfin_url.rstrip("/") + "/Library/Refresh"
    try:
        response = httpx.post(
            url,
            headers={"X-Emby-Token": settings.jellyfin_api_key},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        log.info("jellyfin library refresh requested")
        return True
    except httpx.HTTPError as exc:
        log.warning("jellyfin refresh failed (%s); continuing", exc)
        return False
