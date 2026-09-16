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
        "type": "mv",
        "label": "SOME LABEL",
        "status": "done",
        "needs_review": 0,
        "error": None,
        "file_path": None,
        "thumb_path": None,
        "duration": 215,
        "downloaded_height": 2160,
        "downloaded_vcodec": "vp9",
        "downloaded_fps": 60.0,
        "filesize": 500 * 1024 * 1024,
        "profile_id": 1,
        "profile_name": "4K Best",
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


# --- the trimmed main view -------------------------------------------------


def test_done_row_shows_resolution_and_size():
    html = render_row(status="done")
    assert "2160p60" in html
    assert "500 MB" in html


def test_resolution_omits_frame_rate_when_unremarkable():
    assert "1080p" in render_row(downloaded_height=1080, downloaded_fps=24.0)
    assert "1080p24" not in render_row(downloaded_height=1080, downloaded_fps=24.0)


@pytest.mark.parametrize("status", ["queued", "downloading", "failed"])
def test_unfinished_rows_show_status_in_place_of_resolution(status):
    """Status and resolution are mutually exclusive — a video still downloading
    has no resolution. Sharing the column keeps finished rows quiet without
    letting a failure become invisible."""
    html = render_row(status=status, downloaded_height=None, filesize=None)
    assert status in html


def test_failed_row_still_surfaces_its_error():
    assert "boom" in render_row(status="failed", error="boom")


def test_done_row_does_not_shout_its_status():
    """A column of identical DONE badges is noise; absence means success."""
    assert "s-done" not in render_row(status="done")


def test_artist_has_its_own_column():
    assert "SOMEGROUP" in render_row()


def test_missing_artist_is_shown_as_such():
    assert "no artist" in render_row(artist=None)


def test_label_and_profile_moved_to_the_detail_view():
    html = render_row()
    assert "SOME LABEL" not in html
    assert "4K Best" not in html


def test_title_and_thumbnail_link_to_the_detail_view():
    html = render_row()
    assert html.count('href="/videos/aaaaaaaaaaa"') >= 2


# --- icon buttons ----------------------------------------------------------


def test_done_row_offers_edit_redownload_and_delete():
    html = render_row(status="done")
    assert "/videos/aaaaaaaaaaa/edit" in html
    assert "/videos/aaaaaaaaaaa/redownload" in html
    assert "/videos/aaaaaaaaaaa/delete" in html


def test_every_icon_button_has_an_accessible_name():
    """The svg is aria-hidden, so without an aria-label an icon-only button is
    nameless to a screen reader and unlabelled when tooltips do not fire."""
    html = render_row(status="done")
    import re

    buttons = re.findall(r"<button[^>]*>", html)
    assert buttons
    assert all("aria-label=" in b and "title=" in b for b in buttons)


def test_buttons_use_icons_not_text_labels():
    html = render_row(status="done")
    assert "<svg" in html
    # The words survive only inside title/aria-label attributes, not as content.
    assert ">Redownload<" not in html
    assert ">Edit<" not in html


def test_in_flight_row_offers_no_destructive_actions():
    html = render_row(status="queued")
    assert "redownload" not in html
    assert "/delete" not in html


# --- detail page: one date row ---------------------------------------------


def render_source_dates(**overrides) -> str:
    import re

    from app.routes_web import TYPE_OPTIONS

    row = {
        "video_id": "aaaaaaaaaaa", "title": "Song", "artist": "A", "type": "mv",
        "status": "done", "needs_review": 0, "error": None, "source": "addon", "retry_count": 0, "created_at": "", "updated_at": "",
        "channel_id": "UCx", "channel_name": "1theK", "profile_id": None,
        "profile_name": None, "year": 2018, "upload_date": None, "release_date": None,
    }
    row.update(overrides)
    page = templates.get_template("detail.html").render(
        v=to_view(row), profile=None, type_options=TYPE_OPTIONS,
        profile_options=[], request=None,
    )
    source = page[page.index("<h2>Source</h2>"):page.index("<h2>Files</h2>")]
    return re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", source))


def test_detail_prefers_the_release_date():
    text = render_source_dates(upload_date="2024-07-15", release_date="2019-06-12")
    assert "Released 2019-06-12" in text
    assert "2024-07-15" not in text and "Year" not in text


def test_detail_falls_back_to_the_upload_date():
    text = render_source_dates(upload_date="2018-07-16")
    assert "Uploaded 2018-07-16" in text
    assert "Year" not in text


def test_detail_labels_a_bare_year_as_a_year():
    """Rows downloaded before full dates were stored only have a year; showing
    it under "Uploaded" would pass it off as a date."""
    text = render_source_dates()
    assert "Year 2018" in text
    assert "Uploaded" not in text


def test_detail_has_no_channel_defaults_panel():
    assert "defaults" not in render_source_dates().lower()


def render_detail(**overrides) -> str:
    row = {
        "video_id": "aaaaaaaaaaa", "title": "Song", "artist": "A", "type": "mv",
        "status": "done", "needs_review": 0, "error": None, "source": "addon", "retry_count": 0,
        "created_at": "2026-09-16 08:38:02", "updated_at": "2026-09-16 09:02:13",
        "channel_id": "UCx", "channel_name": "1theK", "profile_id": None,
        "profile_name": None, "year": 2018, "upload_date": None, "release_date": None,
        "file_path": "/media/A/A - Song [aaaaaaaaaaa].mkv",
    }
    row.update(overrides)
    return templates.get_template("detail.html").render(
        v=to_view(row), profile=None, type_options=TYPE_OPTIONS,
        profile_options=[], request=None,
    )


def test_detail_has_no_record_panel():
    assert "<h2>Record</h2>" not in render_detail()


def test_source_shows_origin_added_and_updated_as_separate_rows():
    text = render_source_dates(
        created_at="2026-09-16 08:38:02", updated_at="2026-09-16 09:02:13"
    )
    assert "Added via addon" in text
    assert "Added 2026-09-16 08:38" in text
    assert "Updated 2026-09-16 09:02" in text


def test_retries_only_shown_when_there_were_some():
    assert "Retries" not in render_source_dates(retry_count=0)
    assert "Retries 2" in render_source_dates(retry_count=2)


def test_files_panel_sits_outside_the_two_column_grid():
    """Paths need the full page width to stay readable."""
    html = render_detail()
    grid_end = html.index("</div>", html.index("<h2>Source</h2>"))
    assert html.index("<h2>Files</h2>") > grid_end


def test_detail_has_no_explanatory_hints():
    html = render_detail()
    assert "performs no external" not in html
    assert "Saving renames the file" not in html


def test_edit_form_has_no_version_field():
    assert 'name="version"' not in render_detail()


def render_empty_list(active_filter: str, label: str) -> str:
    return templates.get_template("list.html").render(
        videos=[], filters=[], active_filter=active_filter,
        active_filter_label=label, type_options=TYPE_OPTIONS, request=None,
    )


def test_empty_filter_says_nothing_matches_rather_than_nothing_exists():
    html = render_empty_list("failed", "Failed")
    assert "Nothing here yet" not in html
    assert "No videos match" in html and "Failed" in html


def test_empty_library_still_invites_adding_a_video():
    assert "Nothing here yet" in render_empty_list("all", "All")


def test_artist_name_links_to_its_filter():
    html = render_row(artist="Red Velvet")
    assert 'href="/?artist=Red%20Velvet"' in html


def test_filter_tabs_keep_the_search():
    from app.routes_web import _library_href

    assert _library_href("failed", "ive", "IVE") == "/?filter=failed&q=ive&artist=IVE"
    assert _library_href("all") == "/"
    assert _library_href("all", page=3) == "/?page=3"
    assert _library_href("all", page=1) == "/"


def test_empty_search_offers_to_clear_it():
    html = templates.get_template("list.html").render(
        videos=[], filters=[], active_filter="all", active_filter_label="All",
        q="zzz", artist="", clear_search_href="/", clear_artist_href="/",
        type_options=TYPE_OPTIONS, request=None,
    )
    assert "No videos match your search" in html and "Clear search" in html


def test_thumbnails_load_lazily_with_a_fixed_size():
    from app.config import settings

    html = render_row(thumb_path=str(settings.media_root / "A" / "t.jpg"))
    assert 'loading="lazy"' in html
    assert 'width="88"' in html and 'height="50"' in html


@pytest.mark.parametrize(
    "page, pages, expected",
    [
        (1, 1, [1]),
        (1, 3, [1, 2, 3]),
        (1, 20, [1, 2, None, 20]),
        (5, 20, [1, None, 4, 5, 6, None, 20]),
        (3, 20, [1, 2, 3, 4, None, 20]),  # a one-page gap shows the page
        (20, 20, [1, None, 19, 20]),
    ],
)
def test_page_numbers(page, pages, expected):
    from app.routes_web import _page_numbers

    assert _page_numbers(page, pages) == expected


def test_search_icon_renders_as_svg_not_escaped_text():
    html = templates.get_template("list.html").render(
        videos=[], filters=[], active_filter="all", active_filter_label="All",
        q="", artist="", clear_search_href="/", clear_artist_href="/",
        type_options=TYPE_OPTIONS, request=None,
    )
    assert "<circle" in html and "&lt;circle" not in html
