import pytest

import pytest

from web.app import app as flask_app
from web.app import _compute_novnc_url, _normalise_novnc_url, _normalise_novnc_ws_url


def test_normalise_novnc_url_adds_defaults() -> None:
    url = _normalise_novnc_url("http://example.com:6901")
    assert (
        url
        == "http://example.com:6901/vnc.html?autoconnect=1&resize=scale&reconnect=true&path=websockify"
    )


def test_normalise_novnc_url_preserves_existing_query() -> None:
    url = _normalise_novnc_url("https://example.com/view?v=1&autoconnect=0")
    assert url.startswith("https://example.com/view")
    assert "autoconnect=0" in url
    assert "resize=scale" in url
    assert "path=websockify" in url


def test_normalise_novnc_url_supports_relative_path() -> None:
    url = _normalise_novnc_url("/no-vnc/")
    assert url.startswith("/no-vnc/")
    assert url.endswith("vnc.html?autoconnect=1&resize=scale&reconnect=true&path=websockify")


def test_normalise_novnc_ws_url_from_iframe() -> None:
    url = _normalise_novnc_ws_url(
        "http://example.com:6901/vnc.html?autoconnect=1&path=/custom"
    )
    assert url == "ws://example.com:6901/custom"


def test_normalise_novnc_ws_url_preserves_ws_scheme() -> None:
    url = _normalise_novnc_ws_url("wss://proxy.example.com/bridge?token=abc")
    assert url == "wss://proxy.example.com/bridge?token=abc"


def test_normalise_novnc_ws_url_relative_path() -> None:
    url = _normalise_novnc_ws_url("/no-vnc/")
    assert url == "/no-vnc/websockify"


def test_compute_novnc_url_uses_forwarded_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOVNC_URL", raising=False)
    monkeypatch.setenv("NOVNC_PORT", "7001")

    with flask_app.test_request_context(
        "/",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "demo.example.com",
        },
    ):
        url = _compute_novnc_url()

    assert url == "wss://demo.example.com:7001/websockify"


def test_compute_novnc_url_prefers_configured_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVNC_URL", "https://proxy.example.com/no-vnc/")

    with flask_app.test_request_context("/"):
        url = _compute_novnc_url()

    assert url == "wss://proxy.example.com/no-vnc/websockify"


def test_add_instruction_endpoint_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = flask_app.test_client()

    captured: dict[str, str] = {}

    class DummyManager:
        def add_instruction(self, session_id: str, instruction: str) -> str:
            captured["session_id"] = session_id
            captured["instruction"] = instruction
            return "accepted"

    monkeypatch.setattr("web.app.get_browser_use_manager", lambda: DummyManager())

    response = client.post(
        "/session/demo/instruction",
        json={"instruction": "次の作業を続けて"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {"status": "accepted"}
    assert captured == {"session_id": "demo", "instruction": "次の作業を続けて"}


def test_add_instruction_endpoint_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = flask_app.test_client()

    class DummyManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []
            self.responses = ["not_found", "not_running", "invalid"]

        def add_instruction(self, session_id: str, instruction: str) -> str:
            self.calls.append((session_id, instruction))
            return self.responses.pop(0)

    manager = DummyManager()
    monkeypatch.setattr("web.app.get_browser_use_manager", lambda: manager)

    not_found = client.post(
        "/session/missing/instruction", json={"instruction": "A"}
    )
    assert not_found.status_code == 404

    conflict = client.post(
        "/session/missing/instruction", json={"instruction": "B"}
    )
    assert conflict.status_code == 409
    assert conflict.get_json()["status"] == "not_running"

    invalid = client.post(
        "/session/missing/instruction", json={"instruction": "C"}
    )
    assert invalid.status_code == 400
    assert manager.calls == [
        ("missing", "A"),
        ("missing", "B"),
        ("missing", "C"),
    ]


def test_add_instruction_endpoint_requires_text() -> None:
    client = flask_app.test_client()

    response = client.post("/session/demo/instruction", json={})
    assert response.status_code == 400
