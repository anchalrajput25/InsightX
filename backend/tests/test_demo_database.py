# backend/tests/test_demo_database.py
#
# The "Try the demo database" endpoints: off without the variable, and
# when on, they pass the server's URL to the normal connect code without
# ever returning the password.

from fastapi.testclient import TestClient

from backend import main


def test_demo_database_off_by_default(monkeypatch):
    monkeypatch.delenv("INSIGHTX_DEMO_DATABASE_URL", raising=False)
    client = TestClient(main.app)

    assert client.get("/demo-database").json() == {"available": False, "label": "Demo warehouse"}
    response = client.post("/connect-demo")
    assert response.status_code == 404


def test_connect_demo_uses_server_url(monkeypatch):
    monkeypatch.setenv("INSIGHTX_DEMO_DATABASE_URL",
                       "postgresql://reader:s3cret@ep-demo.neon.tech:5433/insightx_demo?sslmode=require")
    monkeypatch.setenv("INSIGHTX_DEMO_DATABASE_LABEL", "Warehouse demo")
    seen = {}

    def fake_connect(**kwargs):
        seen.update(kwargs)
        return {"status": "success", "source_type": "postgresql", "connection_id": "c1", "schema": {"tables": []}}

    monkeypatch.setattr(main, "connect_postgres", fake_connect)
    client = TestClient(main.app)

    assert client.get("/demo-database").json() == {"available": True, "label": "Warehouse demo"}
    response = client.post("/connect-demo")
    assert response.status_code == 200
    body = response.json()
    assert body["connection_id"] == "c1" and body["label"] == "Warehouse demo"
    assert "s3cret" not in response.text
    assert seen["host"] == "ep-demo.neon.tech" and seen["port"] == 5433
    assert seen["database"] == "insightx_demo" and seen["username"] == "reader"
    assert seen["password"] == "s3cret" and seen["db_type"] == "postgresql"
