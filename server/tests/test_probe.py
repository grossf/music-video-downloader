import pytest

from app.services.probe import (
    detect_type,
    extract_metadata,
    parse_video_id,
    split_artist_title,
    strip_noise,
)

VID = "Xy1_placeho"


@pytest.mark.parametrize(
    "value",
    [
        VID,
        "https://www.youtube.com/watch?v=" + VID,
        "https://youtube.com/watch?v=" + VID + "&list=PL123&index=2",
        "https://youtu.be/" + VID,
        "https://youtu.be/" + VID + "?t=42",
        "https://www.youtube.com/shorts/" + VID,
        "https://www.youtube.com/embed/" + VID,
        "https://music.youtube.com/watch?v=" + VID,
        "www.youtube.com/watch?v=" + VID,
    ],
)
def test_parse_video_id_accepts_all_youtube_url_shapes(value):
    assert parse_video_id(value) == VID


@pytest.mark.parametrize(
    "value",
    ["", None, "not a url", "https://vimeo.com/12345", "https://www.youtube.com/"],
)
def test_parse_video_id_rejects_non_videos(value):
    assert parse_video_id(value) is None


@pytest.mark.parametrize(
    "title, expected",
    [
        ("SOMEGROUP - Song Name (Dance Practice)", "dance_practice"),
        ("[Choreography Video] SOMEGROUP 'Song'", "dance_practice"),
        ("SOMEGROUP - Song Name (Performance Video)", "performance"),
        ("[Relay Dance] SOMEGROUP - Song", "relay_dance"),
        ("SOMEGROUP Song Name Fancam", "fancam"),
        ("SOMEGROUP - Song @ Comeback Stage", "live_stage"),
        ("SOMEGROUP - Song (Behind The Scenes)", "behind"),
        ("SOMEGROUP - Song Name (Official Video)", "mv"),
        ("SOMEGROUP 'Song Name' M/V", "mv"),
    ],
)
def test_detect_type(title, expected):
    assert detect_type(title) == expected


def test_detect_type_runs_before_noise_stripping():
    # "(Dance Practice)" is both the type signal and noise; the type must be
    # read off the raw title, not the cleaned one.
    raw = "SOMEGROUP - Song (Dance Practice)"
    assert detect_type(raw) == "dance_practice"
    assert "Dance Practice" not in strip_noise(raw)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("[MV] SOMEGROUP - Song Name", "SOMEGROUP - Song Name"),
        ("SOMEGROUP - Song Name (Official Video)", "SOMEGROUP - Song Name"),
        ("SOMEGROUP - Song Name (Official Music Video)", "SOMEGROUP - Song Name"),
        ("[4K] SOMEGROUP - Song Name M/V", "SOMEGROUP - Song Name"),
        ("SOMEGROUP - Song Name (Lyric Video)", "SOMEGROUP - Song Name"),
    ],
)
def test_strip_noise(raw, expected):
    assert strip_noise(raw) == expected


def test_split_on_dash():
    assert split_artist_title("[MV] SOMEGROUP - Song Name") == (
        "SOMEGROUP",
        "Song Name",
    )


def test_split_on_underscore_used_by_label_aggregators():
    # 1theK / M2 style: "[MV] ARTIST _ TITLE"
    assert split_artist_title("[MV] SOMEGROUP _ Song Name") == (
        "SOMEGROUP",
        "Song Name",
    )


def test_split_quoted_title_pattern():
    assert split_artist_title('SOMEGROUP "Song Name" M/V') == (
        "SOMEGROUP",
        "Song Name",
    )


def test_split_curly_quoted_title_pattern():
    assert split_artist_title("SOMEGROUP “Song Name” M/V") == (
        "SOMEGROUP",
        "Song Name",
    )


def test_split_returns_no_artist_when_unparseable():
    artist, title = split_artist_title("Just Some Video Title")
    assert artist is None
    assert title == "Just Some Video Title"


def test_split_preserves_hangul():
    artist, title = split_artist_title("아이돌 - 노래제목")
    assert artist == "아이돌"
    assert title == "노래제목"


def info(**overrides) -> dict:
    base = {
        "id": VID,
        "title": "SOMEGROUP - Song Name (Official Video)",
        "channel": "HYBE LABELS",
        "channel_id": "UCabc123",
        "upload_date": "20240715",
        "duration": 215,
        "formats": [{"height": 1080}, {"height": 2160}, {"height": 720}],
    }
    base.update(overrides)
    return base


def test_ytdlp_music_metadata_wins_and_is_high_confidence():
    result = extract_metadata(info(artist="SOMEGROUP", track="Real Song Title"))
    assert result.artist == "SOMEGROUP"
    assert result.title == "Real Song Title"
    assert result.confidence == "high"
    assert result.needs_review is False


def test_artists_list_field_is_handled():
    result = extract_metadata(info(artists=["SOMEGROUP", "Featured"], track="Song"))
    assert result.artist == "SOMEGROUP"


def test_parsed_from_title_is_medium_confidence():
    result = extract_metadata(info())
    assert result.artist == "SOMEGROUP"
    assert result.title == "Song Name"
    assert result.confidence == "medium"
    assert result.needs_review is False


def test_unparseable_title_flags_for_review_and_leaves_artist_empty():
    result = extract_metadata(info(title="Some Random Upload"))
    assert result.artist is None
    assert result.title == "Some Random Upload"
    assert result.confidence == "low"
    assert result.needs_review is True


def test_channel_name_is_never_used_as_artist():
    # The single most important extraction rule: for K-pop the channel is the
    # label, so falling back to it would organise the library by record company.
    result = extract_metadata(info(title="Some Random Upload", channel="HYBE LABELS"))
    assert result.artist != "HYBE LABELS"
    assert result.artist is None
    assert result.needs_review is True


def test_year_from_upload_date_when_no_release_year():
    assert extract_metadata(info()).year == 2024


def test_release_year_preferred_over_upload_date():
    assert extract_metadata(info(release_year=2019)).year == 2019


def test_available_heights_are_collected_and_sorted():
    assert extract_metadata(info()).available_heights == [720, 1080, 2160]


def test_type_is_detected_into_the_result():
    result = extract_metadata(info(title="SOMEGROUP - Song (Dance Practice)"))
    assert result.type == "dance_practice"


def test_missing_optional_fields_do_not_crash():
    result = extract_metadata({"id": VID, "title": ""})
    assert result.video_id == VID
    assert result.needs_review is True
    assert result.available_heights == []


# --- dates -----------------------------------------------------------------


def test_iso_date_converts_ytdlp_format():
    from app.services.probe import iso_date

    assert iso_date("20240715") == "2024-07-15"


def test_iso_date_rejects_junk():
    from app.services.probe import iso_date

    for value in ("", None, "2024", "not-a-date", "202407155", 20240715.0):
        assert iso_date(value) is None, value


def test_full_upload_date_is_captured():
    result = extract_metadata(info())
    assert result.upload_date == "2024-07-15"
    assert result.release_date is None


def test_release_date_is_captured_when_present():
    result = extract_metadata(info(release_date="20190612"))
    assert result.release_date == "2019-06-12"
    assert result.upload_date == "2024-07-15"


def test_year_prefers_the_release_date_over_the_upload_date():
    """A re-upload's upload date can be years after the song came out."""
    assert extract_metadata(info(release_date="20190612")).year == 2019


def test_year_falls_back_to_the_upload_date():
    assert extract_metadata(info()).year == 2024


def test_explicit_release_year_still_wins():
    assert extract_metadata(info(release_year=2015, release_date="20190612")).year == 2015


def test_missing_dates_do_not_crash():
    result = extract_metadata({"id": VID, "title": "x"})
    assert result.upload_date is None
    assert result.release_date is None
    assert result.year is None
