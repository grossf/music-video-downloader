from app.db import get_conn
from app.services import videos


def channel_row(channel_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    return dict(row) if row else None


def test_enqueue_creates_a_queued_row(db):
    result = videos.enqueue(
        video_id="abc12345678", artist="SOMEGROUP", title="Song", source="addon"
    )
    assert result["status"] == "queued"
    assert result["already_present"] is False
    assert result["artist"] == "SOMEGROUP"
    assert result["source"] == "addon"
    assert result["profile_id"] == db["profile_id"]


def test_enqueue_is_idempotent_for_a_video_already_held(db):
    videos.enqueue(video_id="abc12345678", title="Song")
    videos.mark_done("abc12345678", file_path="/media/x.mkv")

    again = videos.enqueue(video_id="abc12345678", title="Song")
    assert again["already_present"] is True
    assert again["status"] == "done"
    # Re-downloading what we already have is the upgrade worker's job.
    assert again["file_path"] == "/media/x.mkv"


def test_enqueue_requeues_a_failed_video(db):
    videos.enqueue(video_id="abc12345678", title="Song")
    videos.mark_failed("abc12345678", "boom")
    assert videos.get("abc12345678")["status"] == "failed"

    again = videos.enqueue(video_id="abc12345678", title="Song")
    assert again["status"] == "queued"
    assert again["already_present"] is False
    assert again["error"] is None
    # The retry count survives, so repeated failures stay visible.
    assert again["retry_count"] == 1




def test_video_reads_carry_the_channel_name(db):
    """The NFO studio is taken from this joined value."""
    videos.enqueue(
        video_id="aaaaaaaaaaa", title="A", channel_id="UCx", channel_name="1theK"
    )
    assert videos.get("aaaaaaaaaaa")["channel_name"] == "1theK"
    assert videos.list_videos()[0]["channel_name"] == "1theK"


def test_new_databases_have_no_label_columns(db):
    from app.db import get_conn

    with get_conn() as conn:
        video_cols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
        channel_cols = {r["name"] for r in conn.execute("PRAGMA table_info(channels)")}
    assert "label" not in video_cols
    assert "default_label" not in channel_cols



def test_enqueue_without_channel_id_does_not_crash(db):
    result = videos.enqueue(video_id="abc12345678", title="Song", channel_id=None)
    assert result["status"] == "queued"


def test_get_many_returns_only_known_ids(db):
    videos.enqueue(video_id="aaaaaaaaaaa", title="A")
    found = videos.get_many(["aaaaaaaaaaa", "zzzzzzzzzzz"])
    assert set(found) == {"aaaaaaaaaaa"}


def test_get_many_with_no_ids(db):
    assert videos.get_many([]) == {}


def test_list_videos_filters_by_status_and_review(db):
    videos.enqueue(video_id="aaaaaaaaaaa", title="A", needs_review=True)
    videos.enqueue(video_id="bbbbbbbbbbb", title="B", needs_review=False)
    videos.mark_done("bbbbbbbbbbb", file_path="/x.mkv")

    assert {v["video_id"] for v in videos.list_videos(status="queued")} == {
        "aaaaaaaaaaa"
    }
    assert {v["video_id"] for v in videos.list_videos(needs_review=True)} == {
        "aaaaaaaaaaa"
    }
    assert len(videos.list_videos()) == 2


def test_mark_done_clears_a_previous_error(db):
    videos.enqueue(video_id="aaaaaaaaaaa", title="A")
    videos.mark_failed("aaaaaaaaaaa", "network died")
    videos.mark_done("aaaaaaaaaaa", file_path="/media/a.mkv", downloaded_height=2160)

    row = videos.get("aaaaaaaaaaa")
    assert row["status"] == "done"
    assert row["error"] is None
    assert row["downloaded_height"] == 2160


def test_worker_claims_rows_with_the_channel_name_attached(db):
    """The worker writes the NFO studio from the claimed row. A bare SELECT *
    on videos has no channel name, so the claim must go through the join."""
    from app.worker import claim_next

    videos.enqueue(
        video_id="aaaaaaaaaaa", title="A", channel_id="UCx", channel_name="1theK"
    )
    claimed = claim_next()
    assert claimed["status"] == "downloading"
    assert claimed["channel_name"] == "1theK"


def test_queued_filter_includes_downloads_in_progress(db):
    """The Queued count includes downloading videos; the list has to match."""
    videos.enqueue(video_id="aaaaaaaaaaa", title="A")
    videos.enqueue(video_id="bbbbbbbbbbb", title="B")
    with get_conn() as conn:
        conn.execute("UPDATE videos SET status='downloading' WHERE video_id='bbbbbbbbbbb'")
    listed = videos.list_videos(status=("queued", "downloading"))
    assert {v["video_id"] for v in listed} == {"aaaaaaaaaaa", "bbbbbbbbbbb"}


def test_review_filter_skips_deleted_videos(db):
    """Matches the Needs review count, which excludes tombstones."""
    videos.enqueue(video_id="aaaaaaaaaaa", title="A", needs_review=True)
    videos.enqueue(video_id="bbbbbbbbbbb", title="B", needs_review=True)
    with get_conn() as conn:
        conn.execute("UPDATE videos SET status='deleted' WHERE video_id='bbbbbbbbbbb'")
    assert [v["video_id"] for v in videos.list_videos(needs_review=True)] == ["aaaaaaaaaaa"]


def test_deleted_videos_only_show_under_the_deleted_filter(db):
    videos.enqueue(video_id="aaaaaaaaaaa", title="A")
    videos.enqueue(video_id="bbbbbbbbbbb", title="B")
    with get_conn() as conn:
        conn.execute("UPDATE videos SET status='deleted' WHERE video_id='bbbbbbbbbbb'")
    assert [v["video_id"] for v in videos.list_videos()] == ["aaaaaaaaaaa"]
    assert [v["video_id"] for v in videos.list_videos(status="deleted")] == ["bbbbbbbbbbb"]


def test_all_count_excludes_deleted_videos(db):
    from app.routes_web import _counts

    videos.enqueue(video_id="aaaaaaaaaaa", title="A")
    videos.enqueue(video_id="bbbbbbbbbbb", title="B")
    with get_conn() as conn:
        conn.execute("UPDATE videos SET status='deleted' WHERE video_id='bbbbbbbbbbb'")
    counts = _counts()
    assert counts["all"] == 1 and counts["deleted"] == 1


# --- search -----------------------------------------------------------------


def _seed_search(db_unused=None):
    videos.enqueue(video_id="aaaaaaaaaaa", artist="IVE", title="LOVE DIVE")
    videos.enqueue(video_id="bbbbbbbbbbb", artist="ILLIT", title="Magnetic")
    videos.enqueue(video_id="ccccccccccc", artist="LIVE BAND", title="Encore")


def _ids(rows):
    return {r["video_id"] for r in rows}


def test_search_matches_title_or_artist_case_insensitively(db):
    _seed_search()
    assert _ids(videos.list_videos(q="magnetic")) == {"bbbbbbbbbbb"}
    assert _ids(videos.list_videos(q="illit")) == {"bbbbbbbbbbb"}
    # Substring, across both columns: IVE, LOVE DIVE and LIVE BAND.
    assert _ids(videos.list_videos(q="ive")) == {"aaaaaaaaaaa", "ccccccccccc"}


def test_artist_filter_is_exact_not_a_substring(db):
    """Clicking IVE must not also list LIVE BAND."""
    _seed_search()
    assert _ids(videos.list_videos(artist="ive")) == {"aaaaaaaaaaa"}


def test_search_wildcards_are_literal(db):
    _seed_search()
    assert videos.list_videos(q="%") == []
    assert videos.list_videos(q="_") == []


def test_search_combines_with_the_status_filter(db):
    _seed_search()
    with get_conn() as conn:
        conn.execute("UPDATE videos SET status='failed' WHERE video_id='bbbbbbbbbbb'")
    assert _ids(videos.list_videos(status="failed", q="ill")) == {"bbbbbbbbbbb"}
    assert videos.list_videos(status="failed", q="ive") == []


def test_filter_counts_follow_the_search(db):
    from app.routes_web import _counts

    _seed_search()
    assert _counts(q="ive")["all"] == 2
    assert _counts(artist="ILLIT")["all"] == 1
    assert _counts()["all"] == 3
