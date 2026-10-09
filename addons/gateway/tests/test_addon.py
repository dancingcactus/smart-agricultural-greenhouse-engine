import json

from fastapi.testclient import TestClient

from gateway import addon
from gateway.main import app
from gateway.metric_catalog import store
from gateway.metric_catalog.models import EntityRecord


def test_apply_options(tmp_path, monkeypatch):
    p = tmp_path / "options.json"
    p.write_text(json.dumps({"vm_url": "http://vm:8428", "metric_catalog_cron": "0 1 * * *",
                             "metric_catalog_lookback_days": 7}))
    for k in ("VM_URL", "METRIC_CATALOG_CRON", "CATALOG_LOOKBACK_DAYS", "CATALOG_DB", "CATALOG_OUT_DIR"):
        monkeypatch.delenv(k, raising=False)
    assert addon.apply_options(p) is True
    import os
    assert os.environ["VM_URL"] == "http://vm:8428" and os.environ["CATALOG_LOOKBACK_DAYS"] == "7"
    assert addon.apply_options(tmp_path / "missing.json") is False


def test_routes(tmp_path, monkeypatch):
    db = tmp_path / "c.sqlite3"
    monkeypatch.setenv("CATALOG_DB", str(db))
    monkeypatch.setenv("CATALOG_OUT_DIR", str(tmp_path / "out"))
    conn = store.connect(db)
    store.save_run(conn, "prometheus", {"sensor.a": EntityRecord("sensor.a", name="Humidity A")}, [])
    store.export(conn, tmp_path / "out")
    client = TestClient(app)
    assert client.get("/catalog/status").json()["latest_run"]["n_entities"] == 1
    assert client.get("/catalog/metrics", params={"q": "humidity"}).json()[0]["entity_id"] == "sensor.a"
    assert client.get("/catalog/metrics/sensor.a").status_code == 200
    assert client.get("/catalog/metrics/nope").status_code == 404
    assert client.get("/catalog/export.csv").text.startswith("entity_id,")
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    assert client.post("/catalog/run").status_code == 502  # no token configured
