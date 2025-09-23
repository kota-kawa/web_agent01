import pytest

from agent import browser_use_runner
from agent.browser_use_runner import BrowserUseSession


@pytest.fixture(autouse=True)
def _clear_cdp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("BROWSER_USE_CDP_URL", "VNC_CDP_URL", "CDP_URL"):
        monkeypatch.delenv(name, raising=False)


def test_resolve_cdp_endpoint_prefers_ws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BROWSER_USE_CDP_URL",
        "ws://example.devtools/devtools/browser/abcdef",
    )

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

    def fake_get(url: str, timeout: float) -> _Response:
        captured["url"] = url
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(browser_use_runner.requests, "get", fake_get)

    result = browser_use_runner._resolve_cdp_endpoint()

    assert result == "ws://example.devtools/devtools/browser/abcdef"
    assert captured["url"] == "http://example.devtools/json/version"
    assert captured["timeout"] == browser_use_runner._CDP_PROBE_TIMEOUT


def test_resolve_cdp_endpoint_prefers_configured_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSER_USE_CDP_URL", "http://custom-host:9222")

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

    def fake_get(url: str, timeout: float) -> _Response:
        captured["url"] = url
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(browser_use_runner.requests, "get", fake_get)

    result = browser_use_runner._resolve_cdp_endpoint()

    assert result == "http://custom-host:9222"
    assert captured["url"].endswith("/json/version")
    assert captured["timeout"] == browser_use_runner._CDP_PROBE_TIMEOUT


def test_resolve_cdp_endpoint_falls_back_to_next_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _Response:
        status_code = 200

    def fake_get(url: str, timeout: float):  # type: ignore[override]
        calls.append(url)
        if "first" in url:
            raise browser_use_runner.requests.ConnectionError("boom")
        return _Response()

    monkeypatch.setattr(browser_use_runner.requests, "get", fake_get)

    result = browser_use_runner._resolve_cdp_endpoint(
        candidates=("http://first:9222", "http://second:9222"),
    )

    assert result == "http://second:9222"
    assert len(calls) == 2


def test_resolve_cdp_endpoint_returns_none_when_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, timeout: float):  # type: ignore[override]
        raise browser_use_runner.requests.RequestException("nope")

    monkeypatch.setattr(browser_use_runner.requests, "get", fake_get)

    result = browser_use_runner._resolve_cdp_endpoint(
        candidates=("http://unreachable:9222",),
        delay=0.0,
    )

    assert result is None


def test_resolve_cdp_endpoint_rejects_unreachable_ws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_get(url: str, timeout: float):  # type: ignore[override]
        calls.append(url)
        raise browser_use_runner.requests.RequestException("boom")

    monkeypatch.setattr(browser_use_runner.requests, "get", fake_get)

    result = browser_use_runner._resolve_cdp_endpoint(
        candidates=("ws://example.devtools/devtools/browser/abcdef",),
        retries=1,
        delay=0.0,
    )

    assert result is None
    assert calls == ["http://example.devtools/json/version"]


class DummyStructured:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def model_dump(self) -> dict[str, object]:
        return self._data


class DummyHistory:
    def __init__(self, structured: DummyStructured | None) -> None:
        self._structured = structured

    @property
    def structured_output(self) -> DummyStructured | None:  # pragma: no cover - simple attribute access
        return self._structured

    def is_successful(self) -> bool:
        return True

    def errors(self) -> list[str | None]:
        return [None, "err"]

    def final_result(self) -> str:
        return "done"

    def urls(self) -> list[str | None]:
        return [None, "https://example.com"]

    def number_of_steps(self) -> int:
        return 2

    def total_duration_seconds(self) -> float:
        return 1.5


def test_finalise_result_handles_none_structured_output() -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)
    history = DummyHistory(structured=None)

    session._finalise_result(history)

    assert session.result is not None
    assert session.result["success"] is True
    assert session.result["errors"] == ["err"]
    assert "structured_output" not in session.result


def test_finalise_result_includes_structured_output_when_present() -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)
    history = DummyHistory(structured=DummyStructured({"foo": "bar"}))

    session._finalise_result(history)

    assert session.result is not None
    assert session.result["structured_output"] == {"foo": "bar"}


def test_create_browser_session_falls_back_to_local_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)

    monkeypatch.setattr(browser_use_runner, "_resolve_cdp_endpoint", lambda: None)

    calls: list[dict[str, object]] = []

    class DummyBrowserSession:
        def __init__(
            self,
            *,
            cdp_url: str | None = None,
            is_local: bool = False,
            **_: object,
        ) -> None:
            calls.append({"cdp_url": cdp_url, "is_local": is_local})
            self.cdp_url = cdp_url
            self.is_local = is_local

    monkeypatch.setattr(browser_use_runner, "BrowserSession", DummyBrowserSession)

    result = session._create_browser_session()

    assert isinstance(result, DummyBrowserSession)
    assert calls == [{"cdp_url": None, "is_local": True}]


def test_create_browser_session_falls_back_after_remote_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)

    monkeypatch.setattr(
        browser_use_runner,
        "_resolve_cdp_endpoint",
        lambda: "http://vnc:9222",
    )

    calls: list[tuple[str | None, bool]] = []

    class DummyBrowserSession:
        def __init__(
            self,
            *,
            cdp_url: str | None = None,
            is_local: bool = False,
            **_: object,
        ) -> None:
            calls.append((cdp_url, is_local))
            if cdp_url:
                raise RuntimeError("remote boom")
            self.cdp_url = cdp_url
            self.is_local = is_local

    monkeypatch.setattr(browser_use_runner, "BrowserSession", DummyBrowserSession)

    result = session._create_browser_session()

    assert isinstance(result, DummyBrowserSession)
    assert calls == [("http://vnc:9222", False), (None, True)]
