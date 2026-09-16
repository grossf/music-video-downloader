import pytest

from app.config import settings
from app.db import init_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh sqlite database and media root per test."""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    settings.ensure_dirs()
    profile_id = init_db()
    return {"profile_id": profile_id, "root": tmp_path}
