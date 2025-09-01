from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_locations_too_short():
    r = client.get("/locations?q=p")
    assert r.status_code == 200
    assert r.json() == {"data": []}
