import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.config import settings

# The constrained type enum. Kept in sync with the CHECK constraint below and
# the dropdown in the addon popup / web forms.
VIDEO_TYPES = (
    "mv",
    "performance",
    "dance_practice",
    "live_stage",
    "fancam",
    "relay_dance",
    "behind",
    "other",
)

VIDEO_STATUSES = ("queued", "downloading", "done", "failed", "deleted")

# Human-readable form written into the NFO as a <tag>. Intended to make the
# type filterable in Jellyfin — unverified for the Music Videos library, which
# does not surface <studio>, so it may not surface tags either.
TYPE_TAGS = {
    "mv": "MV",
    "performance": "Performance",
    "dance_practice": "Dance Practice",
    "live_stage": "Live Stage",
    "fancam": "Fancam",
    "relay_dance": "Relay Dance",
    "behind": "Behind the Scenes",
    "other": "Other",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT NOT NULL UNIQUE,
    -- format_override is a raw yt-dlp selector that bypasses the structured
    -- fields entirely; it is the escape hatch. The structured fields are what
    -- services/formats.py compiles, and what an upgrade worker can reason about.
    format_override    TEXT,
    container          TEXT    NOT NULL DEFAULT 'mkv',
    max_height         INTEGER,
    cutoff_height      INTEGER,
    prefer_codecs      TEXT,
    prefer_fps         INTEGER,
    allow_upgrades     INTEGER NOT NULL DEFAULT 0,
    postprocessing     TEXT    NOT NULL DEFAULT '{}',
    is_default         INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS channels (
    channel_id         TEXT PRIMARY KEY,
    -- Written to the NFO as <studio>.
    name               TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS videos (
    video_id           TEXT PRIMARY KEY,
    channel_id         TEXT    REFERENCES channels(channel_id),
    profile_id         INTEGER REFERENCES profiles(id),

    artist             TEXT,
    title              TEXT,
    version            TEXT,
    type               TEXT    NOT NULL DEFAULT 'mv'
                       CHECK (type IN ('mv','performance','dance_practice',
                                       'live_stage','fancam','relay_dance',
                                       'behind','other')),
    year               INTEGER,
    -- ISO YYYY-MM-DD. upload_date is when it went up on YouTube;
    -- release_date is the actual release when YouTube reports one,
    -- which differs for re-uploads and remasters.
    upload_date        TEXT,
    release_date       TEXT,
    duration           INTEGER,

    status             TEXT    NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued','downloading','done',
                                         'failed','deleted')),

    file_path          TEXT,
    nfo_path           TEXT,
    thumb_path         TEXT,

    -- Populated at download time so the future upgrade worker can compare
    -- what we have against what YouTube now offers, without re-probing disk.
    downloaded_height  INTEGER,
    downloaded_vcodec  TEXT,
    downloaded_acodec  TEXT,
    downloaded_fps     REAL,
    filesize           INTEGER,

    needs_review       INTEGER NOT NULL DEFAULT 0,
    source             TEXT    NOT NULL DEFAULT 'web',
    error              TEXT,
    retry_count        INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_videos_status       ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_needs_review ON videos(needs_review);
CREATE INDEX IF NOT EXISTS idx_videos_created      ON videos(created_at DESC);
"""

DEFAULT_PROFILE = {
    "name": "4K Best",
    # Left empty on purpose so the structured fields below drive selection.
    # A raw override would bypass them, and with it the VP9 preference
    # that keeps 4K direct-playing instead of transcoding in Jellyfin.
    "format_override": None,
    "container": "mkv",
    "max_height": 2160,
    "cutoff_height": 2160,
    "prefer_codecs": "vp9,av01,h264",
    "prefer_fps": 60,
    "allow_upgrades": 0,
    "is_default": 1,
    # metadata embedding is deliberately off: it would write yt-dlp's raw
    # title into the container, which is the unreliable value the review
    # workflow exists to correct. The NFO is the authoritative record.
    "postprocessing": '{"thumbnail": true}',
}


# Additive schema changes for databases created by an earlier version.
# (table, column, definition) — applied only when the column is missing.
#
# Retired, deliberately not dropped — nothing reads or writes them, but
# databases that have them keep the values rather than losing data:
#   videos.label, channels.default_label
#     A hand-typed copy of the channel name; <studio> now comes from
#     channels.name.
#   channels.default_type, channels.default_profile_id, channels.auto_confirm
#     Per-channel defaults. Most videos use the default profile anyway and
#     fixing a mis-detected type is one click, so they were not worth it.
MIGRATIONS = [
    ("profiles", "is_default", "INTEGER NOT NULL DEFAULT 0"),
    ("videos", "upload_date", "TEXT"),
    ("videos", "release_date", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, definition in MIGRATIONS:
        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Short-lived connection per operation. WAL keeps the worker and the web
    requests from blocking each other; at this scale nothing more is needed."""
    conn = sqlite3.connect(settings.db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> int:
    """Create the schema if absent, apply additive migrations, and ensure
    exactly one profile is marked default. Returns the default profile's id."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)

        row = conn.execute(
            "SELECT id FROM profiles WHERE name = ?", (DEFAULT_PROFILE["name"],)
        ).fetchone()
        if row is None:
            cols = ", ".join(DEFAULT_PROFILE)
            placeholders = ", ".join(f":{k}" for k in DEFAULT_PROFILE)
            cursor = conn.execute(
                f"INSERT INTO profiles ({cols}) VALUES ({placeholders})",
                DEFAULT_PROFILE,
            )
            return cursor.lastrowid

        # A database that predates is_default has no default flagged yet.
        current = conn.execute(
            "SELECT id FROM profiles WHERE is_default = 1 ORDER BY id LIMIT 1"
        ).fetchone()
        if current is None:
            conn.execute("UPDATE profiles SET is_default = 1 WHERE id = ?", (row["id"],))
            return row["id"]
        return current["id"]


def default_profile_id() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM profiles WHERE is_default = 1 ORDER BY id LIMIT 1"
        ).fetchone()
        if row:
            return row["id"]
    return init_db()


def set_default_profile(profile_id: int) -> None:
    """Exactly one profile is default at a time."""
    with get_conn() as conn:
        conn.execute("UPDATE profiles SET is_default = 0")
        conn.execute("UPDATE profiles SET is_default = 1 WHERE id = ?", (profile_id,))
