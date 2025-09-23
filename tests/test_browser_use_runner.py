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
        def __init__(self) -> None:
            self.status_code = 200

        def json(self) -> dict[str, str]:
            return {
                "webSocketDebuggerUrl": "ws://example.devtools/devtools/browser/abcdef"
            }

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
        def __init__(self) -> None:
            self.status_code = 200

        def json(self) -> dict[str, str]:
            return {
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/test"
            }

    def fake_get(url: str, timeout: float) -> _Response:
        captured["url"] = url
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(browser_use_runner.requests, "get", fake_get)

    result = browser_use_runner._resolve_cdp_endpoint()

    assert result == "ws://custom-host:9222/devtools/browser/test"
    assert captured["url"].endswith("/json/version")
    assert captured["timeout"] == browser_use_runner._CDP_PROBE_TIMEOUT


def test_resolve_cdp_endpoint_falls_back_to_next_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _Response:
        def __init__(self) -> None:
            self.status_code = 200

        def json(self) -> dict[str, str]:
            return {
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc"
            }

    def fake_get(url: str, timeout: float):  # type: ignore[override]
        calls.append(url)
        if "first" in url:
            raise browser_use_runner.requests.ConnectionError("boom")
        return _Response()

    monkeypatch.setattr(browser_use_runner.requests, "get", fake_get)

    result = browser_use_runner._resolve_cdp_endpoint(
        candidates=("http://first:9222", "http://second:9222"),
    )

    assert result == "ws://second:9222/devtools/browser/abc"
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


def test_resolve_cdp_endpoint_uses_candidate_when_websocket_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def __init__(self) -> None:
            self.status_code = 200

        def json(self) -> dict[str, str]:
            return {}

    monkeypatch.setattr(browser_use_runner.requests, "get", lambda url, timeout: _Response())

    result = browser_use_runner._resolve_cdp_endpoint(
        candidates=("http://vnc:9222",),
        retries=1,
        delay=0.0,
    )

    assert result == "http://vnc:9222"


def test_resolve_cdp_endpoint_rewrites_relative_websocket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def __init__(self) -> None:
            self.status_code = 200

        def json(self) -> dict[str, str]:
            return {"webSocketDebuggerUrl": "devtools/browser/xyz"}

    monkeypatch.setattr(browser_use_runner.requests, "get", lambda url, timeout: _Response())

    result = browser_use_runner._resolve_cdp_endpoint(
        candidates=("http://vnc:9222",),
        retries=1,
        delay=0.0,
    )

    assert result == "ws://vnc:9222/devtools/browser/xyz"


def test_resolve_cdp_endpoint_rewrites_loopback_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def __init__(self) -> None:
            self.status_code = 200

        def json(self) -> dict[str, str]:
            return {"webSocketDebuggerUrl": "ws://0.0.0.0:9222/devtools/browser/loop"}

    monkeypatch.setattr(browser_use_runner.requests, "get", lambda url, timeout: _Response())

    result = browser_use_runner._resolve_cdp_endpoint(
        candidates=("http://vnc:9222",),
        retries=1,
        delay=0.0,
    )

    assert result == "ws://vnc:9222/devtools/browser/loop"


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

    session.warnings.append("warn message")
    session._finalise_result(history)

    assert session.result is not None
    assert session.result["structured_output"] == {"foo": "bar"}
    assert session.result["warnings"] == ["warn message"]


def test_snapshot_includes_warnings_and_shared_browser_data() -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)
    session.warnings.append("notice")
    session.shared_browser_mode = "local"
    session.shared_browser_endpoint = None

    data = session.snapshot()

    assert data["warnings"] == ["notice"]
    assert data["shared_browser_mode"] == "local"
    assert data["shared_browser_endpoint"] is None


def test_create_browser_session_defaults_to_optional_shared_browser(
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
    assert session.shared_browser_mode == "local"
    assert session.shared_browser_endpoint is None
    assert session.warnings == [
        "ライブビューのブラウザに接続できなかったため、ローカルのヘッドレスブラウザで実行します。",
    ]


def test_create_browser_session_falls_back_to_local_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)

    monkeypatch.setenv("REQUIRE_SHARED_BROWSER", "0")

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
    assert session.shared_browser_mode == "local"
    assert session.shared_browser_endpoint is None
    assert session.warnings == [
        "ライブビューのブラウザに接続できなかったため、ローカルのヘッドレスブラウザで実行します。"
    ]


def test_create_browser_session_falls_back_after_remote_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)

    monkeypatch.setenv("REQUIRE_SHARED_BROWSER", "0")

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
    assert session.shared_browser_mode == "local"
    assert session.shared_browser_endpoint is None
    assert session.warnings == [
        "共有ブラウザ http://vnc:9222 への接続に失敗しました（RuntimeError: remote boom）。"
    ]


def test_create_browser_session_records_remote_success(
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
            self.cdp_url = cdp_url
            self.is_local = is_local

    monkeypatch.setattr(browser_use_runner, "BrowserSession", DummyBrowserSession)

    result = session._create_browser_session()

    assert isinstance(result, DummyBrowserSession)
    assert calls == [("http://vnc:9222", False)]
    assert session.shared_browser_mode == "remote"
    assert session.shared_browser_endpoint == "http://vnc:9222"
    assert session.warnings == []


def test_create_browser_session_requires_shared_browser_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)

    monkeypatch.setenv("REQUIRE_SHARED_BROWSER", "1")
    monkeypatch.setattr(browser_use_runner, "_resolve_cdp_endpoint", lambda: None)

    with pytest.raises(RuntimeError) as excinfo:
        session._create_browser_session()

    message = str(excinfo.value)
    assert "ライブビューのブラウザに接続できないため実行できません" in message
    assert "http://vnc:9222" in message


def test_create_browser_session_requires_shared_browser_when_remote_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)

    monkeypatch.setenv("REQUIRE_SHARED_BROWSER", "1")
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
            if not is_local:
                raise RuntimeError("remote boom")
            self.cdp_url = cdp_url
            self.is_local = is_local

    monkeypatch.setattr(browser_use_runner, "BrowserSession", DummyBrowserSession)

    with pytest.raises(RuntimeError) as excinfo:
        session._create_browser_session()

    message = str(excinfo.value)
    assert "remote boom" in message
    assert calls == [("http://vnc:9222", False)]
