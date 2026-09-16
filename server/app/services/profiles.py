"""Quality profile CRUD.

A profile is a download template: either structured fields the app can reason
about, or a raw yt-dlp selector that bypasses them. See `services.formats` for
how the two are compiled.
"""

import json
import logging

from app.db import default_profile_id, get_conn, set_default_profile
from app.services.formats import compile_selector, describe

log = logging.getLogger(__name__)

NOW = "datetime('now')"

EDITABLE = (
    "name",
    "format_override",
    "container",
    "max_height",
    "cutoff_height",
    "prefer_codecs",
    "prefer_fps",
    "allow_upgrades",
    "postprocessing",
)


class NotFound(Exception):
    """No such profile."""


class InUse(Exception):
    """The profile cannot be removed right now."""


def list_profiles() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.*, ("
            "  SELECT COUNT(*) FROM videos v WHERE v.profile_id = p.id"
            ") AS video_count FROM profiles p ORDER BY p.is_default DESC, p.name"
        ).fetchall()
    return [dict(r) | {"selector": describe(dict(r))} for r in rows]


def get_profile(profile_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    if row is None:
        return None
    return dict(row) | {"selector": describe(dict(row))}


def _clean(fields: dict) -> dict:
    cleaned = {}
    for key in EDITABLE:
        if key not in fields:
            continue
        value = fields[key]
        if key in ("max_height", "cutoff_height", "prefer_fps"):
            cleaned[key] = int(value) if str(value or "").strip().isdigit() else None
        elif key == "allow_upgrades":
            cleaned[key] = 1 if value else 0
        elif key == "postprocessing":
            cleaned[key] = value if isinstance(value, str) else json.dumps(value or {})
        else:
            cleaned[key] = (str(value).strip() or None) if value is not None else None
    return cleaned


def create_profile(**fields) -> dict:
    data = _clean(fields)
    data.setdefault("container", "mkv")
    data.setdefault("postprocessing", '{"thumbnail": true}')
    if not data.get("name"):
        raise ValueError("a profile needs a name")

    columns = ", ".join(data)
    placeholders = ", ".join(f":{k}" for k in data)
    with get_conn() as conn:
        cursor = conn.execute(
            f"INSERT INTO profiles ({columns}) VALUES ({placeholders})", data
        )
        profile_id = cursor.lastrowid
    log.info("created profile %s (%s)", profile_id, data.get("name"))
    return get_profile(profile_id)


def update_profile(profile_id: int, **fields) -> dict:
    if get_profile(profile_id) is None:
        raise NotFound(profile_id)
    data = _clean(fields)
    if not data:
        return get_profile(profile_id)
    assignments = ", ".join(f"{k} = :{k}" for k in data)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE profiles SET {assignments}, updated_at = {NOW} WHERE id = :id",
            data | {"id": profile_id},
        )
    log.info("updated profile %s", profile_id)
    return get_profile(profile_id)


def delete_profile(profile_id: int) -> None:
    profile = get_profile(profile_id)
    if profile is None:
        raise NotFound(profile_id)
    if profile["is_default"]:
        raise InUse("the default profile cannot be deleted; make another one default first")

    fallback = default_profile_id()
    with get_conn() as conn:
        # Videos keep working — they fall back to the default profile rather
        # than ending up with a dangling reference.
        conn.execute(
            "UPDATE videos SET profile_id = ? WHERE profile_id = ?",
            (fallback, profile_id),
        )
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    log.info("deleted profile %s", profile_id)


def make_default(profile_id: int) -> dict:
    if get_profile(profile_id) is None:
        raise NotFound(profile_id)
    set_default_profile(profile_id)
    return get_profile(profile_id)


def preview(fields: dict) -> dict:
    """Compile without saving, so the form can show what a profile will do."""
    return compile_selector(_clean(fields) | {"name": fields.get("name")})
