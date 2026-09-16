"""The library page is static HTML. Without a self-refreshing row, a video
queued before its download finished keeps showing an empty thumbnail and a
stale status until the page is reloaded by hand."""

import pytest

from app.routes_web import TYPE_OPTIONS, templates, to_view


def render_row(**overrides) -> str:
    row = {
        "video_id": "aaaaaaaaaaa",
        "artist": "SOMEGROUP",
        "title": "Song",
        "version": None,
        "type": "mv",
        "label": None,
        "status": "done",
        "needs_review": 0,
        "error": None,
        "file_path": None,
        "thumb_path": None,
        "downloaded_height": 2160,
        "downloaded_vcodec": "vp9",
        "downloaded_fps": 60.0,
        "filesize": 500 * 1024 * 1024,
    }
    row.update(overrides)
    template = templates.get_template("_row.html")
    return template.render(v=to_view(row), type_options=TYPE_OPTIONS)


@pytest.mark.parametrize("status", ["queued", "downloading"])
def test_in_flight_rows_refresh_themselves(status):
    html = render_row(status=status)
    assert 'hx-trigger="every 3s"' in html
    assert "/videos/aaaaaaaaaaa/row" in html


@pytest.mark.parametrize("status", ["done", "failed", "deleted"])
def test_settled_rows_do_not_poll(status):
    """Polling has to stop by itself once the row settles, or the page keeps
    hammering the server forever."""
    assert "hx-trigger" not in render_row(status=status)


def test_done_row_offers_redownload_and_edit():
    html = render_row(status="done")
    assert "Redownload" in html
    assert "/videos/aaaaaaaaaaa/edit" in html


def test_in_flight_row_offers_no_destructive_actions():
    html = render_row(status="queued")
    assert "Redownload" not in html
    assert "Delete" not in html


def test_quality_column_summarises_the_download():
    html = render_row(status="done")
    assert "2160p" in html
    assert "vp9" in html
    assert "60fps" in html


def test_missing_artist_is_shown_as_such():
    assert "no artist" in render_row(artist=None)


def test_failed_row_surfaces_its_error():
    assert "boom" in render_row(status="failed", error="boom")
