"""Drive the download path without any UI.

    python scripts/dev_download.py <url-or-id> [--format 'bv*[height<=360]+ba/b']

Probes the video, enqueues it, then runs one worker pass synchronously and
prints where everything landed. This is the harness for build step 2.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.services import probe as probe_service  # noqa: E402
from app.services import videos  # noqa: E402
from app.worker import claim_next, process  # noqa: E402

DEV_PROFILE_NAME = "dev-throwaway"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument(
        "--format",
        dest="fmt",
        help="override the profile's format selector (useful to keep tests small)",
    )
    args = parser.parse_args()

    settings.ensure_dirs()
    profile_id = init_db()

    if args.fmt:
        # A throwaway profile, NOT an edit of the real one. Writing the test
        # override into the shared profile silently caps every later download
        # made from the addon and the web UI.
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO profiles (id, name, format_override, container)"
                " VALUES ((SELECT id FROM profiles WHERE name = ?), ?, ?, 'mkv')",
                (DEV_PROFILE_NAME, DEV_PROFILE_NAME, args.fmt),
            )
            profile_id = conn.execute(
                "SELECT id FROM profiles WHERE name = ?", (DEV_PROFILE_NAME,)
            ).fetchone()["id"]
        print(f"[dev] using throwaway profile {DEV_PROFILE_NAME!r} -> {args.fmt}")

    print(f"[dev] probing {args.url}")
    result = probe_service.probe(args.url)
    print(f"      raw_title  : {result.raw_title}")
    print(f"      artist     : {result.artist!r}")
    print(f"      title      : {result.title!r}")
    print(f"      type       : {result.type}")
    print(f"      channel    : {result.channel_name} ({result.channel_id})")
    print(f"      confidence : {result.confidence}  needs_review={result.needs_review}")
    print(f"      heights    : {result.available_heights}")

    videos.enqueue(
        video_id=result.video_id,
        artist=result.artist,
        title=result.title,
        video_type=result.type,
        year=result.year,
        duration=result.duration,
        channel_id=result.channel_id,
        channel_name=result.channel_name,
        needs_review=result.needs_review,
        source="web",
        profile_id=profile_id,
    )

    claimed = claim_next()
    if claimed is None:
        print("[dev] nothing queued (already downloaded?)")
        row = videos.get(result.video_id)
        print(f"      status={row['status']} path={row['file_path']}")
        return 0

    process(claimed)

    row = videos.get(result.video_id)
    print(f"[dev] status     : {row['status']}")
    if row["status"] == "failed":
        print(f"      error      : {row['error']}")
        return 1
    print(f"      file       : {row['file_path']}")
    print(f"      nfo        : {row['nfo_path']}")
    print(f"      thumb      : {row['thumb_path']}")
    print(
        f"      format     : {row['downloaded_height']}p "
        f"{row['downloaded_vcodec']} / {row['downloaded_acodec']} "
        f"@ {row['downloaded_fps']}fps  {row['filesize']} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
