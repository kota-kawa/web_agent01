from __future__ import annotations

import asyncio
import copy
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlsplit, urlunsplit

import requests
from browser_use.agent.service import Agent, AgentHistoryList
from browser_use.agent.views import AgentOutput
from browser_use.browser import BrowserSession
from browser_use.browser.views import BrowserStateSummary
from browser_use.llm.base import BaseChatModel
from browser_use.llm.google.chat import ChatGoogle
from browser_use.llm.groq.chat import ChatGroq

from agent.browser.patches import apply_browser_use_patches
from agent.utils.history import append_history_entry

log = logging.getLogger(__name__)
apply_browser_use_patches(log)

_CDP_ENV_VARS = ("BROWSER_USE_CDP_URL", "VNC_CDP_URL", "CDP_URL")
_CDP_DEFAULT_ENDPOINTS = (
    "http://vnc:9222",
    "http://127.0.0.1:9222",
    "http://localhost:9222",
)
_CDP_PROBE_TIMEOUT = 3.0
_CDP_PROBE_RETRIES = 25
_CDP_PROBE_DELAY = 2.0


def _normalise_cdp_candidate(value: str | None) -> str:
    if not value:
        return ""
    trimmed = value.strip()
    if not trimmed:
        return ""
    if trimmed.lower().startswith(("ws://", "wss://")):
        return trimmed
    return trimmed.rstrip("/")


def _candidate_cdp_endpoints() -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    for env_name in _CDP_ENV_VARS:
        normalised = _normalise_cdp_candidate(os.getenv(env_name))
        if normalised and normalised not in seen:
            candidates.append(normalised)
            seen.add(normalised)

    for default in _CDP_DEFAULT_ENDPOINTS:
        normalised = _normalise_cdp_candidate(default)
        if normalised and normalised not in seen:
            candidates.append(normalised)
            seen.add(normalised)

    return candidates


def _env_flag(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    trimmed = value.strip().lower()
    if not trimmed:
        return default

    if trimmed in {"1", "true", "yes", "on"}:
        return True
    if trimmed in {"0", "false", "no", "off"}:
        return False
    return default


def _format_shared_browser_error(reason: str, *, candidates: Iterable[str]) -> str:
    candidate_list = [candidate for candidate in candidates if candidate]
    candidate_hint = (
        "、".join(candidate_list) if candidate_list else "http://vnc:9222 (デフォルト)"
    )
    guidance = (
        "VNC サービス (例: http://vnc:9222) が起動し `/json/version` にアクセスできるか確認してください。"
        "Docker Compose を利用している場合は `docker compose ps vnc` で稼働状況を確認し、必要に応じて `docker compose up -d vnc` で再起動してください。"
        "接続先を変更する場合は BROWSER_USE_CDP_URL / VNC_CDP_URL / CDP_URL を設定してください。"
    )
    return (
        "ライブビューのブラウザに接続できないため実行できません。"
        f"{reason}。試行した CDP エンドポイント: {candidate_hint}。{guidance}"
    )


def _json_version_url(base: str) -> str:
    base = (base or "").strip()
    if not base:
        return ""

    try:
        parsed = urlsplit(base)
    except ValueError:
        return ""

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc
    path = parsed.path or ""

    if scheme and not netloc and not path:
        # ``urlsplit('foo')`` treats ``foo`` as a scheme.  Fall back to HTTP.
        return _json_version_url(f"http://{base}")

    if not scheme:
        if base.startswith("//"):
            return _json_version_url(f"http:{base}")
        return _json_version_url(f"http://{base}")

    if not netloc and path:
        # Handles values like ``localhost:9222``.
        return _json_version_url(f"{scheme}://{path}")

    if scheme not in {"http", "https", "ws", "wss"}:
        return ""

    if scheme in {"ws", "wss"}:
        scheme = "http" if scheme == "ws" else "https"

    trimmed_path = path.rstrip("/")
    lowered = trimmed_path.lower()

    if "/devtools/browser" in lowered:
        index = lowered.rfind("/devtools/browser")
        trimmed_path = trimmed_path[:index]
        lowered = trimmed_path.lower()

    if lowered.endswith("/json/version"):
        final_path = trimmed_path
    elif trimmed_path:
        final_path = f"{trimmed_path}/json/version"
    else:
        final_path = "/json/version"

    return urlunsplit((scheme, netloc, final_path, "", ""))


def _probe_cdp_endpoint(endpoint: str, timeout: float = _CDP_PROBE_TIMEOUT) -> bool:
    if not endpoint:
        return False

    url = _json_version_url(endpoint)
    if not url:
        return False
    try:
        response = requests.get(url, timeout=timeout)
    except Exception as exc:
        log.debug("CDP endpoint probe failed for %s: %s", endpoint, exc)
        return False

    if response.status_code == 200:
        return True

    log.debug(
        "CDP endpoint probe for %s returned unexpected status %s",
        endpoint,
        response.status_code,
    )
    return False


def _resolve_cdp_endpoint(
    *,
    candidates: Iterable[str] | None = None,
    retries: int = _CDP_PROBE_RETRIES,
    delay: float = _CDP_PROBE_DELAY,
    request_timeout: float = _CDP_PROBE_TIMEOUT,
) -> str | None:
    candidate_list = (
        list(candidates)
        if candidates is not None
        else _candidate_cdp_endpoints()
    )

    if not candidate_list:
        return None

    first_viable: str | None = None
    max_attempts = max(retries, 1)

    for attempt in range(1, max_attempts + 1):
        for candidate in candidate_list:
            normalised = _normalise_cdp_candidate(candidate)
            if not normalised:
                continue
            if first_viable is None:
                first_viable = normalised
            if _probe_cdp_endpoint(normalised, timeout=request_timeout):
                if attempt > 1:
                    log.info(
                        "CDP endpoint %s became reachable on retry %d/%d",
                        normalised,
                        attempt,
                        max_attempts,
                    )
                return normalised

        if attempt < max_attempts:
            wait_time = delay if delay > 0 else 0.0
            if wait_time > 0:
                log.debug(
                    "CDP endpoint probe attempt %d/%d failed; retrying in %.1fs",
                    attempt,
                    max_attempts,
                    wait_time,
                )
                time.sleep(wait_time)
            else:
                log.debug(
                    "CDP endpoint probe attempt %d/%d failed; retrying immediately",
                    attempt,
                    max_attempts,
                )

    total_wait = max(0.0, (max_attempts - 1) * max(delay, 0.0))

    if first_viable is not None:
        log.warning(
            "Could not verify CDP endpoint connectivity after %d attempts and %.1fs total wait; last candidate was %s",
            max_attempts,
            total_wait,
            first_viable,
        )
    else:
        log.warning(
            "Could not verify CDP endpoint connectivity after %d attempts and %.1fs total wait; no candidates available",
            max_attempts,
            total_wait,
        )

    return None


def _now() -> float:
    return time.time()


def _normalise_screenshot(data: Optional[str]) -> Optional[str]:
    if not data:
        return None
    if data.startswith("data:image"):
        return data
    return f"data:image/png;base64,{data}"


@dataclass
class BrowserUseSession:
    command: str
    model_name: str
    max_steps: int
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "pending"
    error: Optional[str] = None
    steps: list[Dict[str, Any]] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)

    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _agent: Agent | None = field(default=None, init=False, repr=False)
    _prepared_browser_session: BrowserSession | None = field(
        default=None, init=False, repr=False
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _history_recorded: bool = field(default=False, init=False, repr=False)

    async def prepare(self) -> None:
        if self._prepared_browser_session is not None:
            return

        try:
            self._prepared_browser_session = self._create_browser_session()
        except Exception:
            self._prepared_browser_session = None
            raise

    async def run(self) -> None:
        self._task = asyncio.current_task()
        try:
            llm = self._create_llm()
        except Exception as exc:  # pragma: no cover - configuration error path
            log.error("Failed to create LLM for session %s: %s", self.session_id, exc)
            self._set_status("failed", str(exc))
            self._record_history()
            return

        try:
            self._set_status("running")
            if self._prepared_browser_session is not None:
                browser_session = self._prepared_browser_session
                self._prepared_browser_session = None
            else:
                browser_session = self._create_browser_session()
            self._agent = Agent(
                task=self.command,
                llm=llm,
                browser_session=browser_session,
                register_new_step_callback=self._on_step,
                generate_gif=False,
                use_vision=True,
                max_actions_per_step=10,
            )
            history: AgentHistoryList = await self._agent.run(max_steps=self.max_steps)
            self._finalise_result(history)
            self._set_status("completed")
        except asyncio.CancelledError:
            self._set_status("cancelled")
            raise
        except Exception as exc:  # pragma: no cover - runtime failure path
            log.exception("Browser-use session %s failed", self.session_id)
            self._set_status("failed", str(exc))
        finally:
            try:
                if self._agent is not None:
                    await self._agent.close()
            except Exception as close_exc:  # pragma: no cover - defensive
                log.debug("Error closing agent for session %s: %s", self.session_id, close_exc)
            self._record_history()

    async def request_cancel(self) -> None:
        task = self._task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _on_step(
        self,
        browser_state: BrowserStateSummary,
        model_output: AgentOutput,
        step_number: int,
    ) -> None:
        dom_excerpt = ""
        try:
            dom_text = browser_state.dom_state.llm_representation()
            if len(dom_text) > 2000:
                dom_excerpt = dom_text[:2000] + "\n…"
            else:
                dom_excerpt = dom_text
        except Exception as exc:  # pragma: no cover - best effort only
            dom_excerpt = f"DOM extraction failed: {exc}"

        actions = [action.model_dump(exclude_none=True) for action in model_output.action]
        step_payload: Dict[str, Any] = {
            "index": step_number,
            "url": browser_state.url,
            "title": browser_state.title,
            "thinking": model_output.thinking,
            "evaluation": model_output.evaluation_previous_goal,
            "memory": model_output.memory,
            "next_goal": model_output.next_goal,
            "actions": actions,
            "screenshot": _normalise_screenshot(browser_state.screenshot),
            "dom_excerpt": dom_excerpt,
            "timestamp": _now(),
        }
        with self._lock:
            self.steps.append(step_payload)
            self.updated_at = _now()

    def _finalise_result(self, history: AgentHistoryList) -> None:
        structured = history.structured_output
        result: Dict[str, Any] = {
            "success": history.is_successful(),
            "errors": [err for err in history.errors() if err],
            "final_result": history.final_result(),
            "urls": [url for url in history.urls() if url],
            "total_steps": history.number_of_steps(),
            "duration_seconds": history.total_duration_seconds(),
        }
        if structured is not None:
            try:
                result["structured_output"] = structured.model_dump()
            except Exception:  # pragma: no cover - defensive
                result["structured_output"] = str(structured)

        with self._lock:
            self.result = result
            self.updated_at = _now()

    def _create_llm(self) -> BaseChatModel:
        requested = (self.model_name or "").strip()
        model_key = requested.lower()

        if not model_key or model_key == "gemini":
            model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY is not configured")
            return ChatGoogle(model=model_name, api_key=api_key)

        if model_key == "groq":
            model_name = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                raise ValueError("GROQ_API_KEY is not configured")
            return ChatGroq(model=model_name, api_key=api_key)

        if model_key.startswith("gemini"):
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY is not configured")
            return ChatGoogle(model=requested, api_key=api_key)

        if any(token in model_key for token in ("/", "llama", "mixtral", "gemma")):
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                raise ValueError("GROQ_API_KEY is not configured")
            return ChatGroq(model=requested, api_key=api_key)

        # Fallback: try Gemini first, then Groq if Gemini fails
        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key:
            return ChatGoogle(model=requested, api_key=gemini_key)
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            return ChatGroq(model=requested, api_key=groq_key)
        raise ValueError(f"Unsupported model '{requested}'")

    def _create_browser_session(self) -> BrowserSession:
        candidates = _candidate_cdp_endpoints()
        try:
            endpoint = _resolve_cdp_endpoint(candidates=candidates)
        except TypeError:
            endpoint = _resolve_cdp_endpoint()
        require_shared_browser = _env_flag("REQUIRE_SHARED_BROWSER", default=True)
        remote_error: Exception | None = None

        if endpoint:
            try:
                session = BrowserSession(cdp_url=endpoint, is_local=False)
            except Exception as exc:  # pragma: no cover - defensive
                if require_shared_browser:
                    detail = f"{type(exc).__name__}: {exc}"
                    message = _format_shared_browser_error(
                        f"共有ブラウザ {endpoint} への接続に失敗しました（{detail}）",
                        candidates=candidates or [endpoint],
                    )
                    log.error(
                        "Session %s: %s",
                        self.session_id,
                        message,
                        exc_info=True,
                    )
                    raise RuntimeError(message) from exc
                remote_error = exc
                log.warning(
                    "Session %s: failed to attach to shared browser at %s: %s; "
                    "falling back to local headless browser",
                    self.session_id,
                    endpoint,
                    exc,
                )
            else:
                log.info(
                    "Session %s: attaching to shared browser at %s",
                    self.session_id,
                    endpoint,
                )
                return session
        else:
            approx_wait = max(
                0.0, (_CDP_PROBE_RETRIES - 1) * max(_CDP_PROBE_DELAY, 0.0)
            )
            if require_shared_browser:
                reason = "共有ブラウザの CDP エンドポイントが見つからないか応答しませんでした"
                if approx_wait:
                    reason += f"（待機時間: 約{approx_wait:.1f}秒）"
                message = _format_shared_browser_error(
                    reason,
                    candidates=candidates,
                )
                log.error("Session %s: %s", self.session_id, message)
                raise RuntimeError(message)
            log.warning(
                "Session %s: remote browser is not reachable after waiting up to %.1fs; "
                "falling back to local headless browser session",
                self.session_id,
                approx_wait,
            )

        try:
            session = BrowserSession(is_local=True)
        except Exception as exc:  # pragma: no cover - defensive
            if endpoint:
                remote_details = (
                    remote_error if remote_error is not None else "unknown error"
                )
                raise RuntimeError(
                    "Failed to connect to remote browser at "
                    f"{endpoint} ({remote_details}) and local fallback session failed to start: {exc}"
                ) from exc
            raise RuntimeError(
                "Remote VNC browser is not reachable and local fallback session failed to start: "
                f"{exc}"
            ) from exc

        log.info(
            "Session %s: started local headless browser session",
            self.session_id,
        )
        return session

    def _set_status(self, status: str, error: Optional[str] = None) -> None:
        with self._lock:
            self.status = status
            if error:
                self.error = error
            self.updated_at = _now()

    def _record_history(self) -> None:
        if self._history_recorded:
            return
        with self._lock:
            payload = {
                "status": self.status,
                "model": self.model_name,
                "steps": copy.deepcopy(self.steps),
                "result": copy.deepcopy(self.result),
                "error": self.error,
                "complete": self.status == "completed",
            }
            final_url = self._final_url_locked(payload)
            self._history_recorded = True
        try:
            append_history_entry(self.command, payload, final_url)
        except Exception as exc:  # pragma: no cover - IO failure path
            log.error("Failed to persist history for session %s: %s", self.session_id, exc)

    def _final_url_locked(self, payload: Dict[str, Any]) -> Optional[str]:
        steps = payload.get("steps") or []
        if steps:
            url = steps[-1].get("url")
            if url:
                return url
        result = payload.get("result") or {}
        urls = result.get("urls") or []
        if urls:
            return urls[-1]
        return None

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "session_id": self.session_id,
                "command": self.command,
                "model": self.model_name,
                "status": self.status,
                "error": self.error,
                "steps": copy.deepcopy(self.steps),
                "result": copy.deepcopy(self.result),
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "complete": self.status == "completed",
            }


class BrowserUseManager:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._sessions: Dict[str, BrowserUseSession] = {}
        self._lock = threading.Lock()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def start_session(self, command: str, *, model: str, max_steps: int) -> str:
        session = BrowserUseSession(command=command, model_name=model, max_steps=max_steps)
        with self._lock:
            self._sessions[session.session_id] = session
        try:
            prepare_future = asyncio.run_coroutine_threadsafe(
                session.prepare(), self._loop
            )
            prepare_future.result()
        except Exception:
            with self._lock:
                self._sessions.pop(session.session_id, None)
            raise
        asyncio.run_coroutine_threadsafe(session.run(), self._loop)
        log.info("Started browser-use session %s for command: %s", session.session_id, command)
        return session.session_id

    def get_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = self._sessions.get(session_id)
        if not session:
            return None
        return session.snapshot()

    def cancel_session(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if not session:
            return False
        future = asyncio.run_coroutine_threadsafe(session.request_cancel(), self._loop)
        try:
            future.result(timeout=10)
            return True
        except Exception as exc:  # pragma: no cover - defensive
            log.error("Failed to cancel session %s: %s", session_id, exc)
            return False

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            future = asyncio.run_coroutine_threadsafe(session.request_cancel(), self._loop)
            try:
                future.result(timeout=5)
            except Exception:  # pragma: no cover - best effort
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


_browser_use_manager: BrowserUseManager | None = None


def get_browser_use_manager() -> BrowserUseManager:
    global _browser_use_manager
    if _browser_use_manager is None:
        _browser_use_manager = BrowserUseManager()
    return _browser_use_manager
