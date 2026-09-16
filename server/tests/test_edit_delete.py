import pytest

from app.config import settings
from app.services import videos
from app.services.naming import build_paths
from app.services.nfo import read_nfo


def make_done_video(
    video_id="aaaaaaaaaaa",
    artist="OLDNAME",
    title="Song",
    video_type="mv",
    label=None,
    channel_id="UCx",
):
    """Create a row in 'done' state with real files on disk."""
    videos.enqueue(
        video_id=video_id,
        artist=artist,
        title=title,
        video_type=video_type,
        label=label,
        channel_id=channel_id,
        duration=215,
    )
    paths = build_paths(
        video_id=video_id, artist=artist, title=title, video_type=video_type
    )
    paths.directory.mkdir(parents=True, exist_ok=True)
    paths.media.write_bytes(b"fake video data")
    paths.nfo.write_text("<musicvideo />", encoding="utf-8")
    paths.thumb.write_bytes(b"fake jpg")
    videos.mark_done(
        video_id,
        file_path=str(paths.media),
        nfo_path=str(paths.nfo),
        thumb_path=str(paths.thumb),
        filesize=paths.media.stat().st_size,
    )
    return paths


def test_edit_renames_files_and_moves_artist_folder(db):
    old = make_done_video(artist="OLDNAME", title="Song")
    assert old.media.exists()

    updated = videos.update_metadata(
        "aaaaaaaaaaa", artist="NEWNAME", title="Better Title"
    )

    new_media = settings.media_root / "NEWNAME" / "NEWNAME - Better Title [aaaaaaaaaaa].mkv"
    assert new_media.exists()
    assert new_media.read_bytes() == b"fake video data"
    assert updated["file_path"] == str(new_media)
    # The old artist folder is gone, not left behind empty.
    assert not old.media.exists()
    assert not old.directory.exists()


def test_edit_moves_the_thumbnail_too(db):
    make_done_video(artist="OLDNAME")
    updated = videos.update_metadata("aaaaaaaaaaa", artist="NEWNAME", title="Song")
    from pathlib import Path

    assert Path(updated["thumb_path"]).exists()
    assert "NEWNAME" in updated["thumb_path"]


def test_edit_rewrites_the_nfo_and_removes_the_old_one(db):
    old = make_done_video(artist="OLDNAME", title="Song")
    old_nfo = old.nfo

    updated = videos.update_metadata(
        "aaaaaaaaaaa", artist="NEWNAME", title="Song", label="HYBE"
    )

    assert not old_nfo.exists()
    from pathlib import Path

    data = read_nfo(Path(updated["nfo_path"]))
    assert data["artist"] == "NEWNAME"
    assert data["label"] == "HYBE"
    assert data["video_id"] == "aaaaaaaaaaa"


def test_edit_supplying_an_artist_clears_needs_review(db):
    videos.enqueue(video_id="aaaaaaaaaaa", title="Mystery", needs_review=True)
    paths = build_paths(video_id="aaaaaaaaaaa", artist=None, title="Mystery")
    paths.directory.mkdir(parents=True, exist_ok=True)
    paths.media.write_bytes(b"x")
    videos.mark_done(
        "aaaaaaaaaaa", file_path=str(paths.media), nfo_path=str(paths.nfo)
    )
    assert videos.get("aaaaaaaaaaa")["needs_review"] == 1

    updated = videos.update_metadata(
        "aaaaaaaaaaa", artist="SOMEGROUP", title="Mystery"
    )
    assert updated["needs_review"] == 0
    # It also leaves the _Unsorted holding pen.
    assert "_Unsorted" not in updated["file_path"]


def test_edit_changing_type_adds_the_version_suffix(db):
    make_done_video(artist="A", title="Song", video_type="mv")
    updated = videos.update_metadata(
        "aaaaaaaaaaa", artist="A", title="Song", video_type="performance"
    )
    assert updated["file_path"].endswith("A - Song (Performance) [aaaaaaaaaaa].mkv")


def test_edit_teaches_the_channel_its_label(db):
    make_done_video(channel_id="UCchoom")
    videos.update_metadata(
        "aaaaaaaaaaa", artist="A", title="Song", label="STUDIO CHOOM"
    )
    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT default_label FROM channels WHERE channel_id = ?", ("UCchoom",)
        ).fetchone()
    assert row["default_label"] == "STUDIO CHOOM"


def test_edit_is_refused_while_downloading(db):
    videos.enqueue(video_id="aaaaaaaaaaa", title="Song")
    # enqueue leaves it 'queued', which is equally mid-flight.
    with pytest.raises(videos.NotEditable):
        videos.update_metadata("aaaaaaaaaaa", artist="A", title="B")


def test_edit_unknown_video_raises_notfound(db):
    with pytest.raises(videos.NotFound):
        videos.update_metadata("zzzzzzzzzzz", artist="A", title="B")


def test_edit_survives_a_missing_media_file(db):
    paths = make_done_video(artist="A", title="Song")
    paths.media.unlink()

    updated = videos.update_metadata("aaaaaaaaaaa", artist="B", title="Song")
    # Metadata is still corrected rather than the edit being refused.
    assert updated["artist"] == "B"


def test_delete_removes_files_and_tombstones_the_row(db):
    paths = make_done_video(artist="A", title="Song")

    result = videos.delete_video("aaaaaaaaaaa")

    assert not paths.media.exists()
    assert not paths.nfo.exists()
    assert not paths.thumb.exists()
    assert not paths.directory.exists()
    assert result["status"] == "deleted"
    assert result["file_path"] is None


def test_delete_keeps_the_row_so_it_is_not_silently_redownloaded(db):
    make_done_video()
    videos.delete_video("aaaaaaaaaaa")

    row = videos.get("aaaaaaaaaaa")
    assert row is not None, "the tombstone must survive"
    assert row["status"] == "deleted"
    # The addon can therefore distinguish "removed on purpose" from "never had".
    assert row["video_id"] == "aaaaaaaaaaa"


def test_delete_is_refused_while_queued(db):
    videos.enqueue(video_id="aaaaaaaaaaa", title="Song")
    with pytest.raises(videos.NotEditable):
        videos.delete_video("aaaaaaaaaaa")


def test_delete_a_failed_video_is_allowed(db):
    videos.enqueue(video_id="aaaaaaaaaaa", title="Song")
    videos.mark_failed("aaaaaaaaaaa", "boom")
    result = videos.delete_video("aaaaaaaaaaa")
    assert result["status"] == "deleted"


def test_enqueue_after_delete_requeues(db):
    make_done_video()
    videos.delete_video("aaaaaaaaaaa")
    again = videos.enqueue(video_id="aaaaaaaaaaa", artist="A", title="Song")
    assert again["status"] == "queued"
    assert again["already_present"] is False


def test_edit_preserves_the_premiered_date_in_the_nfo(db):
    """An edit rewrites the whole NFO, so anything not carried through
    explicitly is silently lost from the file."""
    from pathlib import Path

    make_done_video(artist="OLDNAME", title="Song")
    videos.mark_done(
        "aaaaaaaaaaa", upload_date="2024-07-15", release_date="2019-06-12"
    )

    updated = videos.update_metadata("aaaaaaaaaaa", artist="NEWNAME", title="Song")

    data = read_nfo(Path(updated["nfo_path"]))
    assert data["premiered"] == "2019-06-12", "release date must survive an edit"


def test_edit_falls_back_to_the_upload_date_when_no_release_date(db):
    from pathlib import Path

    make_done_video(artist="OLDNAME", title="Song")
    videos.mark_done("aaaaaaaaaaa", upload_date="2024-07-15")

    updated = videos.update_metadata("aaaaaaaaaaa", artist="NEWNAME", title="Song")
    assert read_nfo(Path(updated["nfo_path"]))["premiered"] == "2024-07-15"


def test_edit_without_any_date_does_not_crash(db):
    from pathlib import Path

    make_done_video(artist="OLDNAME", title="Song")
    updated = videos.update_metadata("aaaaaaaaaaa", artist="NEWNAME", title="Song")
    assert read_nfo(Path(updated["nfo_path"]))["premiered"] is None
