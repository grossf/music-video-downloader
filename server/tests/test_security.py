"""Cross-site form posts are refused; the app's own pages and the API are not."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import is_same_origin, refuses


@pytest.mark.parametrize(
    "headers, expected",
    [
        ({"host": "server:7345", "origin": "http://server:7345"}, True),
        ({"host": "server:7345", "origin": "http://evil.example"}, False),
        # Same host name, different port: a different origin.
        ({"host": "server:7345", "origin": "http://server:8096"}, False),
        ({"host": "server:7345", "referer": "http://server:7345/videos/x"}, True),
        ({"host": "server:7345", "referer": "https://evil.example/page"}, False),
        ({"host": "server:7345", "origin": "null"}, False),
        # Not a browser: nothing to compare, nothing to refuse.
        ({"host": "server:7345"}, True),
        # Behind a reverse proxy that rewrites Host.
        ({"host": "mvd:8080", "x-forwarded-host": "music.lan",
          "origin": "https://music.lan"}, True),
    ],
)
def test_same_origin(headers, expected):
    assert is_same_origin(headers) is expected


def test_reads_and_the_api_are_never_refused():
    evil = {"host": "server", "origin": "http://evil.example"}
    assert not refuses("GET", "/videos/x", evil)
    assert not refuses("POST", "/api/videos", evil)
    assert refuses("POST", "/videos/x/delete", evil)


def test_cross_site_delete_is_refused_before_it_reaches_the_route(db):
    client = TestClient(app)
    response = client.post(
        "/videos/aaaaaaaaaaa/delete",
        headers={"origin": "http://evil.example"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_same_site_post_goes_through(db):
    client = TestClient(app)
    response = client.post(
        "/videos/aaaaaaaaaaa/redownload",
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    # Unknown video: the route logs and redirects, which proves it ran.
    assert response.status_code == 303


def test_htmx_is_served_locally():
    client = TestClient(app)
    response = client.get("/static/htmx.min.js")
    assert response.status_code == 200
    assert "htmx" in response.text[:200]
