"""Choosing a download profile per video, from the addon or the web form."""

from app.db import default_profile_id, get_conn
from app.services import profiles, videos
from app.services.formats import summarize


def test_enqueue_honours_an_explicit_profile(db):
    tv = profiles.create_profile(name="1080p", max_height=1080)
    row = videos.enqueue(video_id="aaaaaaaaaaa", title="A", profile_id=tv["id"])
    assert row["profile_id"] == tv["id"]
    assert row["profile_name"] == "1080p"


def test_enqueue_without_a_profile_uses_the_global_default(db):
    row = videos.enqueue(video_id="aaaaaaaaaaa", title="A")
    assert row["profile_id"] == default_profile_id()


def test_a_non_default_choice_is_not_carried_to_the_next_video(db):
    """Per-channel defaults were removed: picking a profile for one video from
    a channel says nothing about the next one."""
    tv = profiles.create_profile(name="1080p", max_height=1080)
    videos.enqueue(
        video_id="aaaaaaaaaaa", title="A", channel_id="UCx", profile_id=tv["id"]
    )
    second = videos.enqueue(video_id="bbbbbbbbbbb", title="B", channel_id="UCx")
    assert second["profile_id"] == default_profile_id()


def test_channels_record_only_their_name(db):
    videos.enqueue(
        video_id="aaaaaaaaaaa",
        title="A",
        channel_id="UCchoom",
        channel_name="STUDIO CHOOM",
        video_type="performance",
    )
    with get_conn() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(channels)")}
        row = dict(
            conn.execute(
                "SELECT * FROM channels WHERE channel_id = ?", ("UCchoom",)
            ).fetchone()
        )
    assert row["name"] == "STUDIO CHOOM"
    for retired in ("default_type", "default_profile_id", "auto_confirm"):
        assert retired not in columns


def test_channel_name_is_filled_in_but_never_overwritten(db):
    videos.enqueue(video_id="aaaaaaaaaaa", title="A", channel_id="UCx", channel_name=None)
    videos.enqueue(video_id="bbbbbbbbbbb", title="B", channel_id="UCx", channel_name="First")
    videos.enqueue(video_id="ccccccccccc", title="C", channel_id="UCx", channel_name="Second")
    assert videos.get("ccccccccccc")["channel_name"] == "First"


def test_summary_is_short_enough_for_a_dropdown():
    summary = summarize(
        {"max_height": 2160, "prefer_codecs": "vp9,av01", "prefer_fps": 60,
         "container": "mkv", "format_override": None}
    )
    assert "2160p" in summary and "vp9" in summary and "mkv" in summary
    assert len(summary) < 40


def test_summary_of_an_override_says_so():
    summary = summarize({"format_override": "bv*+ba/b", "container": "mkv"})
    assert "custom" in summary


def test_summary_without_a_height_limit():
    assert "best available" in summarize({"container": "mkv"})


def test_summary_is_ascii_only():
    """Shown in the addon popup and logged; a stray arrow breaks cp1252."""
    summarize({"format_override": "b", "container": "mp4"}).encode("ascii")
    summarize({"max_height": 1080, "container": "mkv"}).encode("ascii")
