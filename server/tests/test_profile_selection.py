"""Choosing a download profile per video, from the addon or the web form."""

from app.db import default_profile_id, get_conn
from app.services import profiles, videos
from app.services.formats import summarize
from app.services.probe import ProbeResult, apply_channel_defaults


def channel_row(channel_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    return dict(row) if row else None


def test_enqueue_honours_an_explicit_profile(db):
    tv = profiles.create_profile(name="1080p", max_height=1080)
    row = videos.enqueue(video_id="aaaaaaaaaaa", title="A", profile_id=tv["id"])
    assert row["profile_id"] == tv["id"]
    assert row["profile_name"] == "1080p"


def test_enqueue_without_a_profile_uses_the_global_default(db):
    row = videos.enqueue(video_id="aaaaaaaaaaa", title="A")
    assert row["profile_id"] == default_profile_id()


def test_channel_learns_a_deliberately_chosen_profile(db):
    tv = profiles.create_profile(name="4K60", max_height=2160)
    videos.enqueue(
        video_id="aaaaaaaaaaa",
        title="A",
        channel_id="UCchoom",
        channel_name="STUDIO CHOOM",
        profile_id=tv["id"],
    )
    assert channel_row("UCchoom")["default_profile_id"] == tv["id"]


def test_channel_does_not_learn_the_global_default_as_a_preference(db):
    """Picking whatever was already selected is not a choice. Recording it
    would pin the channel to today's default forever, the same trap that made
    default_type stick on its fallback value."""
    videos.enqueue(
        video_id="aaaaaaaaaaa",
        title="A",
        channel_id="UCx",
        profile_id=default_profile_id(),
    )
    assert channel_row("UCx")["default_profile_id"] is None

    # A genuinely different choice still gets through afterwards.
    tv = profiles.create_profile(name="1080p", max_height=1080)
    videos.enqueue(
        video_id="bbbbbbbbbbb", title="B", channel_id="UCx", profile_id=tv["id"]
    )
    assert channel_row("UCx")["default_profile_id"] == tv["id"]


def test_channel_profile_is_not_overwritten_once_learned(db):
    first = profiles.create_profile(name="First", max_height=2160)
    second = profiles.create_profile(name="Second", max_height=720)
    videos.enqueue(video_id="aaaaaaaaaaa", title="A", channel_id="UCx", profile_id=first["id"])
    videos.enqueue(video_id="bbbbbbbbbbb", title="B", channel_id="UCx", profile_id=second["id"])
    assert channel_row("UCx")["default_profile_id"] == first["id"]


def test_probe_suggests_the_channels_remembered_profile(db):
    tv = profiles.create_profile(name="4K60", max_height=2160)
    videos.enqueue(
        video_id="aaaaaaaaaaa", title="A", channel_id="UCchoom", profile_id=tv["id"]
    )

    suggested = apply_channel_defaults(
        ProbeResult(video_id="bbbbbbbbbbb", url="u", channel_id="UCchoom")
    )
    assert suggested.profile_id == tv["id"]


def test_probe_suggests_nothing_for_an_unknown_channel(db):
    result = apply_channel_defaults(
        ProbeResult(video_id="aaaaaaaaaaa", url="u", channel_id="UCnew")
    )
    # None means "no preference recorded"; the caller falls back to the default.
    assert result.profile_id is None


def test_probe_without_a_channel_id_does_not_crash(db):
    result = apply_channel_defaults(
        ProbeResult(video_id="aaaaaaaaaaa", url="u", channel_id=None)
    )
    assert result.profile_id is None


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
