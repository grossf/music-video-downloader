from pathlib import Path

import pytest

from app.services.naming import (
    build_paths,
    prune_empty_dir,
    sanitize_component,
    display_title,
)

BS = chr(92)
ROOT = Path("/media/music-videos")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("TWICE", "TWICE"),
        ("AC/DC", "AC-DC"),
        ("Artist: The Return", "Artist- The Return"),
        ("  spaced   out  ", "spaced out"),
        ("trailing dot.", "trailing dot"),
        ("trailing space ", "trailing space"),
        ('quote"name', "quote-name"),
        (BS + "backslash", "-backslash"),
        ("pipe|star*q?", "pipe-star-q-"),
        ("CON", "_CON"),
        ("nul", "_nul"),
        ("COM1", "_COM1"),
    ],
)
def test_sanitize_component(raw, expected):
    assert sanitize_component(raw) == expected


def test_sanitize_preserves_emoji_and_hangul():
    assert sanitize_component("TWICE 🔥 나연") == "TWICE 🔥 나연"


def test_sanitize_strips_invisible_chars():
    assert sanitize_component("we\u200bird\u0007") == "weird"


def test_sanitize_empty_uses_fallback():
    assert sanitize_component("", fallback="Unknown") == "Unknown"
    assert sanitize_component(None, fallback="Unknown") == "Unknown"
    # Everything stripped away still yields the fallback, not an empty name.
    assert sanitize_component("...", fallback="fb") == "fb"


def test_sanitize_truncates_to_byte_limit():
    out = sanitize_component("나" * 200)
    assert len(out.encode("utf-8")) <= 150


def test_display_title_appends_the_type():
    assert display_title("Song", "mv") == "Song"
    assert display_title("Song", "performance") == "Song (Performance)"
    assert display_title("Song", "dance_practice") == "Song (Dance Practice)"


def test_display_title_does_not_double_a_hand_typed_suffix():
    assert display_title("Song (Performance)", "performance") == "Song (Performance)"
    assert display_title("Song (performance) ", "performance") == "Song (performance) "


def test_display_title_keeps_other_brackets_and_still_adds_the_type():
    assert display_title("Song (Band Ver.)", "performance") == "Song (Band Ver.) (Performance)"
    assert display_title("Song (Band Ver.)", "mv") == "Song (Band Ver.)"


def test_display_title_of_nothing_is_nothing():
    assert display_title(None, "performance") is None


def test_build_paths_does_not_double_a_hand_typed_suffix():
    p = build_paths(
        video_id="bbb", artist="TWICE", title="Song (Performance)",
        video_type="performance", media_root=ROOT,
    )
    assert p.media.name == "TWICE - Song (Performance) [bbb].mkv"


def test_build_paths_standard():
    p = build_paths(
        video_id="dQw4w9WgXcQ",
        artist="TWICE",
        title="Song Title",
        media_root=ROOT,
    )
    assert p.directory == ROOT / "TWICE"
    assert p.media.name == "TWICE - Song Title [dQw4w9WgXcQ].mkv"
    assert p.nfo.name == "TWICE - Song Title [dQw4w9WgXcQ].nfo"
    assert p.thumb.name == "TWICE - Song Title [dQw4w9WgXcQ]-thumb.jpg"


def test_build_paths_performance_gets_type_suffix():
    mv = build_paths(video_id="aaa", artist="TWICE", title="Song", media_root=ROOT)
    perf = build_paths(
        video_id="bbb",
        artist="TWICE",
        title="Song",
        video_type="performance",
        media_root=ROOT,
    )
    # Same artist folder, distinct filenames.
    assert mv.directory == perf.directory
    assert perf.media.name == "TWICE - Song (Performance) [bbb].mkv"
    assert mv.media.name != perf.media.name


def test_build_paths_without_artist_goes_to_unsorted():
    p = build_paths(video_id="xyz", artist=None, title="Mystery Song", media_root=ROOT)
    assert p.directory == ROOT / "_Unsorted"
    assert p.media.name == "Mystery Song [xyz].mkv"


def test_build_paths_untitled_falls_back_to_video_id():
    p = build_paths(video_id="xyz", artist="A", title=None, media_root=ROOT)
    assert p.media.name == "A - xyz [xyz].mkv"


def test_build_paths_long_title_stays_under_filesystem_limit():
    p = build_paths(
        video_id="dQw4w9WgXcQ",
        artist="가" * 100,
        title="나" * 100,
        media_root=ROOT,
    )
    # Every emitted name, including the longest suffix "-thumb.jpg", must fit.
    for name in (p.media.name, p.nfo.name, p.thumb.name):
        assert len(name.encode("utf-8")) < 255, name
    assert len(p.directory.name.encode("utf-8")) < 255
    # Truncation must not eat the identity key.
    assert "[dQw4w9WgXcQ]" in p.media.name


def test_build_paths_truncation_keeps_video_id_for_absurd_titles():
    p = build_paths(
        video_id="dQw4w9WgXcQ",
        artist="A" * 400,
        title="B" * 400,
        video_type="dance_practice",
        media_root=ROOT,
    )
    assert p.media.name.endswith("[dQw4w9WgXcQ].mkv")
    assert len(p.thumb.name.encode("utf-8")) < 255


def test_prune_empty_dir_removes_empty(tmp_path):
    artist_dir = tmp_path / "TWICE"
    artist_dir.mkdir()
    prune_empty_dir(artist_dir, media_root=tmp_path)
    assert not artist_dir.exists()


def test_prune_empty_dir_keeps_non_empty(tmp_path):
    artist_dir = tmp_path / "TWICE"
    artist_dir.mkdir()
    (artist_dir / "keep.mkv").write_text("x")
    prune_empty_dir(artist_dir, media_root=tmp_path)
    assert artist_dir.exists()


def test_prune_empty_dir_never_removes_the_media_root(tmp_path):
    prune_empty_dir(tmp_path, media_root=tmp_path)
    assert tmp_path.exists()
