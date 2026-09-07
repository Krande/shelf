from fastapi.testclient import TestClient

from shelf.main import app

client = TestClient(app)


def test_health() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_api_root() -> None:
    r = client.get("/api")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "shelf"


def test_root_404_when_no_spa_built() -> None:
    # Tests run with the default frontend_dir (/app/frontend) which is
    # not present locally, so the SPA fallback is unmounted and "/" has
    # no handler. Once the SPA build is bind-mounted in, "/" should
    # return index.html instead — covered by an integration test.
    r = client.get("/")
    assert r.status_code == 404
