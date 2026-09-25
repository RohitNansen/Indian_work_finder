import os

os.environ.setdefault("APP_PASSWORD", "test-password-long-enough")
os.environ.setdefault("APP_SECRET", "test-secret-long-enough")

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.main import app


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    object.__setattr__(settings, "data_dir", tmp_path)
    (tmp_path / "uploads").mkdir()
    (tmp_path / "exports").mkdir()
    db.init_db()
    yield tmp_path


@pytest.fixture
def client(test_env):
    with TestClient(app) as c:
        c.post("/login", data={"password": "test-password-long-enough"})
        yield c


def csrf(client):
    from app.main import csrf_token
    request = type("Request", (), {"cookies": client.cookies})()
    return csrf_token(request)
