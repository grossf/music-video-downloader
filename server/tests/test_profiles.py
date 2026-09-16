import pytest

from app.db import default_profile_id
from app.services import profiles, videos


def test_seeded_profile_is_the_default(db):
    listed = profiles.list_profiles()
    assert len(listed) == 1
    assert listed[0]["is_default"] == 1
    assert listed[0]["id"] == default_profile_id()


def test_seeded_profile_targets_4k_vp9(db):
    """The seeded default must reach 2160p and prefer VP9 — and must never be
    capped low, which is what a stray test override silently did once."""
    seeded = profiles.list_profiles()[0]
    assert seeded["max_height"] == 2160
    # No raw override, or the structured VP9 preference would be bypassed.
    assert not seeded["format_override"]
    assert "height<=2160" in seeded["selector"]
    assert "vcodec:vp9" in seeded["selector"]
    assert "360" not in seeded["selector"]


def test_create_profile(db):
    created = profiles.create_profile(
        name="1080p H264", max_height=1080, prefer_codecs="h264", container="mp4"
    )
    assert created["name"] == "1080p H264"
    assert created["max_height"] == 1080
    assert created["is_default"] == 0
    assert "height<=1080" in created["selector"]


def test_create_requires_a_name(db):
    with pytest.raises(ValueError):
        profiles.create_profile(name="", max_height=1080)


def test_numeric_fields_accept_blank_form_values(db):
    """Form posts send empty strings, not None."""
    created = profiles.create_profile(
        name="No limit", max_height="", prefer_fps="", prefer_codecs="vp9"
    )
    assert created["max_height"] is None
    assert created["prefer_fps"] is None
    assert "height<=" not in created["selector"]


def test_update_profile(db):
    created = profiles.create_profile(name="Temp", max_height=720)
    updated = profiles.update_profile(created["id"], max_height=2160, name="Temp")
    assert updated["max_height"] == 2160


def test_update_unknown_profile_raises(db):
    with pytest.raises(profiles.NotFound):
        profiles.update_profile(9999, name="nope")


def test_make_default_moves_the_flag(db):
    original = profiles.list_profiles()[0]
    created = profiles.create_profile(name="4K VP9", max_height=2160)

    profiles.make_default(created["id"])

    assert profiles.get_profile(created["id"])["is_default"] == 1
    assert profiles.get_profile(original["id"])["is_default"] == 0
    # Exactly one default, always.
    assert sum(p["is_default"] for p in profiles.list_profiles()) == 1
    assert default_profile_id() == created["id"]


def test_default_profile_cannot_be_deleted(db):
    default = profiles.list_profiles()[0]
    with pytest.raises(profiles.InUse):
        profiles.delete_profile(default["id"])


def test_deleting_a_profile_repoints_its_videos_at_the_default(db):
    created = profiles.create_profile(name="Temp", max_height=720)
    videos.enqueue(video_id="aaaaaaaaaaa", title="A", profile_id=created["id"])
    assert videos.get("aaaaaaaaaaa")["profile_id"] == created["id"]

    profiles.delete_profile(created["id"])

    # No dangling reference — the video still downloads, using the default.
    assert videos.get("aaaaaaaaaaa")["profile_id"] == default_profile_id()
    assert profiles.get_profile(created["id"]) is None


def test_video_count_is_reported(db):
    videos.enqueue(video_id="aaaaaaaaaaa", title="A")
    assert profiles.list_profiles()[0]["video_count"] == 1


def test_requeue_keeps_corrected_metadata(db):
    videos.enqueue(video_id="aaaaaaaaaaa", artist="SOMEGROUP", title="Fixed By Hand")
    videos.mark_done("aaaaaaaaaaa", file_path="/media/x.mkv", downloaded_height=360)

    requeued = videos.requeue("aaaaaaaaaaa")

    assert requeued["status"] == "queued"
    assert requeued["artist"] == "SOMEGROUP"
    assert requeued["title"] == "Fixed By Hand"
    assert requeued["error"] is None


def test_requeue_can_switch_profile(db):
    created = profiles.create_profile(name="4K", max_height=2160)
    videos.enqueue(video_id="aaaaaaaaaaa", title="A")
    videos.mark_done("aaaaaaaaaaa", file_path="/media/x.mkv")

    requeued = videos.requeue("aaaaaaaaaaa", profile_id=created["id"])
    assert requeued["profile_id"] == created["id"]


def test_requeue_is_refused_while_in_flight(db):
    videos.enqueue(video_id="aaaaaaaaaaa", title="A")
    with pytest.raises(videos.NotEditable):
        videos.requeue("aaaaaaaaaaa")


def test_requeue_unknown_video_raises(db):
    with pytest.raises(videos.NotFound):
        videos.requeue("zzzzzzzzzzz")


def test_listed_videos_carry_their_profile_name(db):
    videos.enqueue(video_id="aaaaaaaaaaa", title="A")
    listed = videos.list_videos()
    assert listed[0]["profile_name"] == "4K Best"
    assert videos.get("aaaaaaaaaaa")["profile_name"] == "4K Best"


def test_video_still_listed_after_its_profile_is_deleted(db):
    created = profiles.create_profile(name="Temp", max_height=720)
    videos.enqueue(video_id="aaaaaaaaaaa", title="A", profile_id=created["id"])
    profiles.delete_profile(created["id"])

    listed = videos.list_videos()
    assert len(listed) == 1, "the video must not vanish with its profile"
    assert listed[0]["profile_name"] == "4K Best"


def test_edit_can_change_a_videos_profile(db):
    created = profiles.create_profile(name="1080p", max_height=1080)
    videos.enqueue(video_id="aaaaaaaaaaa", artist="A", title="Song")
    videos.mark_done("aaaaaaaaaaa", file_path=None)

    updated = videos.update_metadata(
        "aaaaaaaaaaa", artist="A", title="Song", profile_id=created["id"]
    )
    assert updated["profile_id"] == created["id"]
    assert updated["profile_name"] == "1080p"


def test_edit_without_a_profile_keeps_the_existing_one(db):
    videos.enqueue(video_id="aaaaaaaaaaa", artist="A", title="Song")
    videos.mark_done("aaaaaaaaaaa", file_path=None)
    before = videos.get("aaaaaaaaaaa")["profile_id"]

    updated = videos.update_metadata("aaaaaaaaaaa", artist="A", title="Song")
    assert updated["profile_id"] == before
