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
