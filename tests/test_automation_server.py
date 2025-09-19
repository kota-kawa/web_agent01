import base64
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from vnc.automation_server import create_app


class DummyAdapter:
    async def is_healthy(self) -> bool:
        return True


class DummyService:
    def __init__(self) -> None:
        self.executed = False
        self.adapter = DummyAdapter()
        self._events: str | None = "{}"

    def execute_plan(self, payload):
        self.executed = True
        return {
            "success": True,
            "html": "<html></html>",
            "url": "https://example.com",
            "warnings": [],
            "results": [],
            "correlation_id": payload.get("correlation_id", "cid"),
            "observation": {},
            "is_done": True,
            "complete": True,
        }

    def get_html(self) -> str:
        return "<html></html>"

    def get_url(self) -> str:
        return "https://example.com"

    def get_screenshot(self, full_page: bool = False) -> bytes:
        return b"png"

    def get_elements(self):
        return [{"selector": "#item"}]

    def get_catalog(self, refresh: bool = False):
        return {
            "abbreviated": [],
            "full": [{"index": 0, "selector": "#item"}],
            "catalog_version": "v1",
            "index_mode_enabled": True,
            "metadata": {"url": "https://example.com"},
        }

    def get_extracted(self):
        return ["foo"]

    def get_eval_results(self):
        return [123]

    def get_stop_request(self):
        return {"reason": "pause", "message": "hold"}

    def record_stop_response(self, response: str):
        self.last_response = response

    def get_events(self, run_id: str) -> str | None:
        return self._events if run_id == "ok" else None


def test_flask_app_endpoints():
    service = DummyService()
    app = create_app(service)
    app.testing = True
    client = app.test_client()

    resp = client.post("/execute-dsl", json={"actions": []})
    assert resp.status_code == 200
    assert service.executed is True

    resp = client.get("/source")
    assert resp.data.decode("utf-8") == "<html></html>"

    resp = client.get("/url")
    assert resp.get_json()["url"] == "https://example.com"

    resp = client.get("/screenshot")
    assert base64.b64decode(resp.data) == b"png"

    resp = client.get("/elements")
    assert resp.get_json()[0]["selector"] == "#item"

    resp = client.get("/catalog")
    assert resp.get_json()["catalog_version"] == "v1"

    resp = client.get("/extracted")
    assert resp.get_json() == ["foo"]

    resp = client.get("/eval_results")
    assert resp.get_json() == [123]

    resp = client.get("/stop-request")
    assert resp.get_json()["reason"] == "pause"

    resp = client.post("/stop-response", json={"response": "continue"})
    assert resp.status_code == 200
    assert service.last_response == "continue"

    resp = client.get("/events/ok")
    assert resp.status_code == 200
    resp = client.get("/events/missing")
    assert resp.status_code == 404

    resp = client.get("/healthz")
    assert resp.status_code == 200
