import os

os.environ.setdefault("DAY1_SALT", "test-salt")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def fresh(client):
    r = client.post("/audit/v1/reset", json={})
    assert r.status_code == 200, r.text
    return client
