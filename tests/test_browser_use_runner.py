import pytest

from agent import browser_use_runner
from agent.browser_use_runner import BrowserUseSession


@pytest.fixture(autouse=True)
def _clear_cdp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("BROWSER_USE_CDP_URL", "VNC_CDP_URL", "CDP_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _reset_managers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BROWSER_USE_REMOTE_API", raising=False)
    monkeypatch.setattr(browser_use_runner, "_browser_use_manager", None)
    monkeypatch.setattr(browser_use_runner, "_remote_browser_use_manager", None)


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


def test_resolve_cdp_endpoint_accepts_host_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSER_USE_CDP_URL", "localhost:9222")

    captured: dict[str, object] = {}

    class _Response:
        def __init__(self) -> None:
            self.status_code = 200

        def json(self) -> dict[str, str]:
            return {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/test"}

    def fake_get(url: str, timeout: float) -> _Response:
        captured["url"] = url
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(browser_use_runner.requests, "get", fake_get)

    result = browser_use_runner._resolve_cdp_endpoint()

    assert result == "ws://localhost:9222/devtools/browser/test"
    assert captured["url"] == "http://localhost:9222/json/version"
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


def test_history_context_creates_extension() -> None:
    session = BrowserUseSession(
        command="cmd",
        model_name="model",
        max_steps=1,
        history_context="[1] ユーザー指示: 続きを実行",
    )

    assert session._history_extension is not None
    assert "これまでの会話履歴" in session._history_extension


def test_create_browser_session_raises_when_shared_browser_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)

    monkeypatch.delenv("REQUIRE_SHARED_BROWSER", raising=False)
    monkeypatch.setattr(
        browser_use_runner, "_resolve_cdp_endpoint", lambda *_, **__: None
    )
    monkeypatch.setattr(
        browser_use_runner, "_warm_shared_browser", lambda candidates: ([], None)
    )

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

    with pytest.raises(RuntimeError) as excinfo:
        session._create_browser_session()

    assert calls == []
    message = str(excinfo.value)
    assert "ライブビューのブラウザに接続できないため実行できません" in message
    assert "共有ブラウザの CDP エンドポイントが見つからないか応答しませんでした" in message
    assert "http://vnc:9222" in message
    assert session.shared_browser_mode == "unknown"
    assert session.shared_browser_endpoint is None
    assert session.warnings == []


def test_create_browser_session_raises_when_remote_attach_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)

    monkeypatch.delenv("REQUIRE_SHARED_BROWSER", raising=False)
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
            raise RuntimeError("remote boom")

    monkeypatch.setattr(browser_use_runner, "BrowserSession", DummyBrowserSession)

    with pytest.raises(RuntimeError) as excinfo:
        session._create_browser_session()

    message = str(excinfo.value)
    assert "ライブビューのブラウザに接続できないため実行できません" in message
    assert "remote boom" in message
    assert "http://vnc:9222" in message
    assert calls == [("http://vnc:9222", False)]
    assert session.shared_browser_mode == "unknown"
    assert session.shared_browser_endpoint is None
    assert session.warnings == []


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


def test_warm_shared_browser_handles_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_base() -> str:
        return "http://vnc:7000"

    class DummyResponse:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "status": "ready",
                "cdp_ready": True,
                "public_websocket": "ws://vnc:9222/devtools/browser/abc",
                "public_endpoint": "http://vnc:9222",
                "candidates": ["http://127.0.0.1:9222"],
            }

    def fake_post(url: str, json: dict[str, object], timeout: tuple[float, float]):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(browser_use_runner, "get_vnc_api_base", fake_base)
    monkeypatch.setattr(browser_use_runner.requests, "post", fake_post)

    candidates, websocket = browser_use_runner._warm_shared_browser(
        ["http://localhost:9222"]
    )

    assert websocket == "ws://vnc:9222/devtools/browser/abc"
    assert candidates[0] == "ws://vnc:9222/devtools/browser/abc"
    assert "http://vnc:9222" in candidates
    assert captured["url"] == "http://vnc:7000/shared-browser/ensure"
    assert captured["json"] == {"candidates": ["http://localhost:9222"]}
    assert isinstance(captured["timeout"], tuple)
    assert captured["timeout"][1] == browser_use_runner._CDP_WARMUP_TIMEOUT


def test_create_browser_session_uses_warmup_websocket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)

    attempts = {"count": 0}

    def fake_resolve(*, candidates=None, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return None
        return "ws://vnc:9222/devtools/browser/fallback"

    monkeypatch.setattr(browser_use_runner, "_resolve_cdp_endpoint", fake_resolve)
    monkeypatch.setattr(
        browser_use_runner,
        "_warm_shared_browser",
        lambda candidates: (["http://vnc:9222"], "ws://vnc:9222/devtools/browser/abc"),
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
    assert calls == [("ws://vnc:9222/devtools/browser/abc", False)]
    assert attempts["count"] >= 1
    assert session.shared_browser_mode == "remote"
    assert session.shared_browser_endpoint == "ws://vnc:9222/devtools/browser/abc"


def test_create_browser_session_requires_shared_browser_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)

    monkeypatch.setenv("REQUIRE_SHARED_BROWSER", "1")
    monkeypatch.setattr(
        browser_use_runner, "_resolve_cdp_endpoint", lambda *_, **__: None
    )
    monkeypatch.setattr(
        browser_use_runner, "_warm_shared_browser", lambda candidates: ([], None)
    )

    with pytest.raises(RuntimeError) as excinfo:
        session._create_browser_session()

    message = str(excinfo.value)
    assert "ライブビューのブラウザに接続できないため実行できません" in message
    assert "http://vnc:9222" in message
    assert session.shared_browser_mode == "unknown"
    assert session.shared_browser_endpoint is None
    assert session.warnings == []


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
    assert "ライブビューのブラウザに接続できないため実行できません" in message
    assert "remote boom" in message
    assert calls == [("http://vnc:9222", False)]
    assert session.shared_browser_mode == "unknown"
    assert session.shared_browser_endpoint is None
    assert session.warnings == []


def test_get_browser_use_manager_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_instance = object()

    monkeypatch.setenv("BROWSER_USE_REMOTE_API", "1")
    monkeypatch.setattr(
        browser_use_runner,
        "RemoteBrowserUseManager",
        lambda: dummy_instance,
    )

    manager = browser_use_runner.get_browser_use_manager()
    assert manager is dummy_instance
    assert browser_use_runner.get_browser_use_manager() is dummy_instance


def test_remote_manager_start_session_success(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = browser_use_runner.RemoteBrowserUseManager()

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"session_id": "abc123"}

    monkeypatch.setattr(manager, "_request", lambda *_, **__: _Response())

    session_id = manager.start_session("command", model="m", max_steps=5)

    assert session_id == "abc123"


def test_remote_manager_start_session_includes_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = browser_use_runner.RemoteBrowserUseManager()

    captured: dict[str, dict[str, object] | None] = {"payload": None}

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"session_id": "ctx123"}

    def fake_request(method, path, json_payload=None, timeout=None):  # type: ignore[override]
        captured["payload"] = json_payload
        return _Response()

    monkeypatch.setattr(manager, "_request", fake_request)

    session_id = manager.start_session(
        "command",
        model="m",
        max_steps=5,
        conversation_context="履歴要約",
    )

    assert session_id == "ctx123"
    assert captured["payload"]["conversation_context"] == "履歴要約"


def test_remote_manager_start_session_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = browser_use_runner.RemoteBrowserUseManager()

    class _Response:
        status_code = 400

        @staticmethod
        def json() -> dict[str, str]:
            return {"error": "bad request"}

    monkeypatch.setattr(manager, "_request", lambda *_, **__: _Response())

    with pytest.raises(ValueError) as excinfo:
        manager.start_session("command", model="m", max_steps=5)

    assert "bad request" in str(excinfo.value)


def test_remote_manager_start_session_shared_browser_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = browser_use_runner.RemoteBrowserUseManager()

    class _Response:
        status_code = 503

        @staticmethod
        def json() -> dict[str, str]:
            return {"error": "shared", "code": "shared_browser_unavailable"}

    monkeypatch.setattr(manager, "_request", lambda *_, **__: _Response())

    with pytest.raises(RuntimeError) as excinfo:
        manager.start_session("command", model="m", max_steps=5)

    assert "shared" in str(excinfo.value)


def test_remote_manager_get_status(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = browser_use_runner.RemoteBrowserUseManager()

    class _Ok:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"status": "running"}

    class _Missing:
        status_code = 404

        @staticmethod
        def json() -> dict[str, str]:
            return {"error": "session not found"}

    monkeypatch.setattr(manager, "_request", lambda *_, **__: _Ok())
    assert manager.get_status("abc") == {"status": "running"}

    monkeypatch.setattr(manager, "_request", lambda *_, **__: _Missing())
    assert manager.get_status("abc") is None


def test_remote_manager_cancel_session(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = browser_use_runner.RemoteBrowserUseManager()

    class _Ok:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {}

    class _Missing:
        status_code = 404

        @staticmethod
        def json() -> dict[str, str]:
            return {}

    monkeypatch.setattr(manager, "_request", lambda *_, **__: _Ok())
    assert manager.cancel_session("abc") is True

    monkeypatch.setattr(manager, "_request", lambda *_, **__: _Missing())
    assert manager.cancel_session("abc") is False


def test_remote_manager_request_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = browser_use_runner.RemoteBrowserUseManager()

    monkeypatch.setattr(
        browser_use_runner,
        "get_vnc_api_base",
        lambda refresh=False: "http://vnc:7000",
    )

    def fake_request(method, url, json=None, timeout=None):  # type: ignore[override]
        raise browser_use_runner.requests.RequestException("boom")

    monkeypatch.setattr(browser_use_runner.requests, "request", fake_request)

    with pytest.raises(RuntimeError) as excinfo:
        manager.start_session("command", model="m", max_steps=1)

    assert "boom" in str(excinfo.value)
