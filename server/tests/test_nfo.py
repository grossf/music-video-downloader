import xml.etree.ElementTree as ET

from app.services.nfo import build_nfo, read_nfo, write_nfo


def parse(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def test_build_nfo_is_wellformed_musicvideo():
    root = parse(build_nfo(video_id="abc123", title="Song", artist="TWICE"))
    assert root.tag == "musicvideo"
    assert root.findtext("title") == "Song"
    assert root.findtext("artist") == "TWICE"


def test_type_becomes_a_filterable_tag():
    root = parse(
        build_nfo(video_id="a", title="S", video_type="dance_practice")
    )
    assert "Dance Practice" in [el.text for el in root.findall("tag")]


def test_studio_is_written_once_not_duplicated_as_a_tag():
    root = parse(
        build_nfo(video_id="a", title="S", studio="STUDIO CHOOM", video_type="mv")
    )
    assert root.findtext("studio") == "STUDIO CHOOM"
    # Only the type is a tag now; the studio used to be duplicated as one.
    assert [el.text for el in root.findall("tag")] == ["MV"]


def test_video_id_is_recorded_as_uniqueid():
    root = parse(build_nfo(video_id="dQw4w9WgXcQ", title="S"))
    uid = root.find("uniqueid")
    assert uid.text == "dQw4w9WgXcQ"
    assert uid.get("type") == "youtube"


def test_empty_fields_are_omitted_not_blank():
    root = parse(build_nfo(video_id="a", title="S", artist=None, year=None))
    assert root.find("artist") is None
    assert root.find("year") is None


def test_missing_title_falls_back_to_video_id():
    root = parse(build_nfo(video_id="abc123", title=None))
    assert root.findtext("title") == "abc123"


def test_runtime_converted_to_minutes():
    root = parse(build_nfo(video_id="a", title="S", runtime=215))
    assert root.findtext("runtime") == "4"


def test_special_characters_are_escaped():
    root = parse(build_nfo(video_id="a", title='A & B <tag> "q"', artist="AC/DC"))
    assert root.findtext("title") == 'A & B <tag> "q"'
    assert root.findtext("artist") == "AC/DC"


def test_roundtrip_write_and_read(tmp_path):
    path = tmp_path / "TWICE - Song [abc].nfo"
    write_nfo(
        path,
        video_id="abc",
        title="Song",
        artist="TWICE",
        year=2024,
        studio="JYP",
        video_type="performance",
    )
    data = read_nfo(path)
    assert data["title"] == "Song"
    assert data["artist"] == "TWICE"
    assert data["year"] == 2024
    assert data["studio"] == "JYP"
    assert data["video_id"] == "abc"
    assert "Performance" in data["tags"]


def test_premiered_carries_the_full_release_date():
    """<year> alone loses the month and day; Jellyfin reads <premiered>."""
    root = parse(build_nfo(video_id="a", title="S", year=2024, premiered="2024-07-15"))
    assert root.findtext("premiered") == "2024-07-15"
    assert root.findtext("year") == "2024"


def test_premiered_is_omitted_when_unknown():
    root = parse(build_nfo(video_id="a", title="S", year=2024))
    assert root.find("premiered") is None


def test_premiered_survives_a_roundtrip(tmp_path):
    path = tmp_path / "x.nfo"
    write_nfo(path, video_id="a", title="S", premiered="2024-07-15")
    assert read_nfo(path)["premiered"] == "2024-07-15"
