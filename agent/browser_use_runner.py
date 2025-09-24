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
from browser_use.agent.views import ActionModel, AgentOutput
from browser_use.browser import BrowserSession
from browser_use.browser.views import BrowserStateSummary
from browser_use.llm.base import BaseChatModel
from browser_use.llm.google.chat import ChatGoogle
from browser_use.llm.groq.chat import ChatGroq

from agent.browser.catalog import ElementCatalogSnapshot, build_element_catalog
from agent.browser.patches import apply_browser_use_patches
from agent.browser.vnc import get_vnc_api_base
from agent.utils.history import append_history_entry
from agent.utils.shared_browser import (
    env_flag,
    format_shared_browser_error,
    normalise_cdp_websocket,
)

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
_CDP_WARMUP_TIMEOUT = max(
    5.0,
    float(os.getenv("BROWSER_USE_CDP_WARMUP_TIMEOUT", "12")),
)


def _merge_candidates(*groups: Iterable[str | None]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()

    for group in groups:
        if not group:
            continue
        for candidate in group:
            normalised = _normalise_cdp_candidate(candidate)
            if not normalised or normalised in seen:
                continue
            merged.append(normalised)
            seen.add(normalised)

    return merged


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


def _warm_shared_browser(
    candidates: Iterable[str],
) -> tuple[list[str], str | None]:
    """Request the automation server to prepare the shared browser."""

    candidate_list = _merge_candidates(candidates)
    base_url = get_vnc_api_base()

    connect_timeout = min(6.0, _CDP_WARMUP_TIMEOUT)
    request_timeout = (connect_timeout, _CDP_WARMUP_TIMEOUT)

    try:
        response = requests.post(
            f"{base_url}/shared-browser/ensure",
            json={"candidates": candidate_list},
            timeout=request_timeout,
        )
    except Exception as exc:  # pragma: no cover - network failure path
        log.debug("Shared browser warmup request to %s failed: %s", base_url, exc)
        return [], None

    if response.status_code != 200:
        log.warning(
            "Shared browser warmup request to %s returned status %s",
            base_url,
            response.status_code,
        )
        return [], None

    try:
        payload = response.json()
    except ValueError:
        log.warning(
            "Shared browser warmup response from %s was not valid JSON", base_url
        )
        return [], None

    reported_candidates: list[str] = []
    websocket: str | None = None

    raw_ws = None
    for key in ("public_websocket", "websocket", "active_websocket"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            raw_ws = value.strip()
            break

    if raw_ws:
        websocket = raw_ws

    for key in ("public_endpoint", "active_endpoint"):
        value = payload.get(key)
        if isinstance(value, str):
            reported_candidates.append(value)

    extra = payload.get("candidates")
    if isinstance(extra, list):
        for item in extra:
            if isinstance(item, str):
                reported_candidates.append(item)

    merged_candidates = _merge_candidates(
        [websocket] if websocket else [],
        reported_candidates,
        candidate_list,
    )

    status = payload.get("status")
    ready = payload.get("cdp_ready")
    if status:
        log.info(
            "Shared browser warmup status from %s: %s (cdp_ready=%s)",
            base_url,
            status,
            ready,
        )
    else:
        log.debug(
            "Shared browser warmup from %s returned candidates: %s",
            base_url,
            merged_candidates,
        )

    return merged_candidates, websocket



def _json_version_url(base: str) -> str:
    base = (base or "").strip()
    if not base:
        return ""

    working = base
    if working.startswith("//"):
        working = f"http:{working}"
    elif "://" not in working:
        working = f"http://{working}"

    adjustments = 0
    while True:
        try:
            parsed = urlsplit(working)
        except ValueError:
            return ""

        scheme = parsed.scheme.lower()
        netloc = parsed.netloc
        path = parsed.path or ""

        if netloc:
            break

        if adjustments >= 2:
            return ""

        adjustments += 1

        host_candidate = path.lstrip("/")
        if host_candidate:
            replacement_scheme = scheme or "http"
            working = f"{replacement_scheme}://{host_candidate}"
            continue

        if scheme and not host_candidate:
            # ``scheme:`` with no host is not recoverable.
            return ""

        # No netloc, no usable path – give up.
        return ""

    if not scheme:
        return ""

    if scheme not in {"http", "https", "ws", "wss"}:
        return ""

    if scheme in {"ws", "wss"}:
        scheme = "http" if scheme == "ws" else "https"

    trimmed_path = parsed.path.rstrip("/")
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


def _probe_cdp_endpoint(
    endpoint: str, timeout: float = _CDP_PROBE_TIMEOUT
) -> tuple[bool, str | None]:
    if not endpoint:
        return False, None

    url = _json_version_url(endpoint)
    if not url:
        return False, None
    try:
        response = requests.get(url, timeout=timeout)
    except Exception as exc:
        log.debug("CDP endpoint probe failed for %s: %s", endpoint, exc)
        return False, None

    if response.status_code != 200:
        log.debug(
            "CDP endpoint probe for %s returned unexpected status %s",
            endpoint,
            response.status_code,
        )
        return False, None

    websocket_url: str | None = None
    try:
        payload = response.json()
    except ValueError:
        log.debug("CDP version endpoint %s returned non-JSON response", url)
    else:
        raw_ws = payload.get("webSocketDebuggerUrl")
        if isinstance(raw_ws, str):
            websocket_url = normalise_cdp_websocket(endpoint, raw_ws)

    return True, websocket_url or endpoint


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
            success, resolved_endpoint = _probe_cdp_endpoint(
                normalised, timeout=request_timeout
            )
            if success:
                if attempt > 1:
                    log.info(
                        "CDP endpoint %s became reachable on retry %d/%d",
                        resolved_endpoint or normalised,
                        attempt,
                        max_attempts,
                    )
                return resolved_endpoint or normalised

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
    history_context: str | None = None
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "pending"
    error: Optional[str] = None
    steps: list[Dict[str, Any]] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    shared_browser_mode: str = "unknown"
    shared_browser_endpoint: str | None = None
    warnings: list[str] = field(default_factory=list)

    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _agent: Agent | None = field(default=None, init=False, repr=False)
    _prepared_browser_session: BrowserSession | None = field(
        default=None, init=False, repr=False
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _history_recorded: bool = field(default=False, init=False, repr=False)
    _history_extension: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        context = (self.history_context or "").strip()
        if not context:
            return

        instructions = [
            "## これまでの会話履歴（直近）",
            context,
            "",
            "上記の履歴と現在開いているブラウザの状態を踏まえ、デフォルトの開始ページに戻らずに続きのタスクを実行してください。",
            "過去の操作で入力した内容や遷移したページを再利用し、必要に応じて現在のページからそのまま作業を再開します。",
        ]
        self._history_extension = "\n".join(part for part in instructions if part)

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
                extend_system_message=self._history_extension,
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

    def _set_shared_browser_state(self, mode: str, endpoint: str | None) -> None:
        normalised = mode if mode in {"local", "remote"} else "unknown"
        with self._lock:
            self.shared_browser_mode = normalised
            self.shared_browser_endpoint = endpoint
            self.updated_at = _now()

    def _add_warning(self, message: str | None) -> None:
        if not message:
            return
        trimmed = message.strip()
        if not trimmed:
            return
        with self._lock:
            if trimmed not in self.warnings:
                self.warnings.append(trimmed)
                self.updated_at = _now()

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
        element_catalog: ElementCatalogSnapshot | None = None
        action_warnings: list[str] = []

        try:
            dom_text = browser_state.dom_state.llm_representation()
            if len(dom_text) > 2000:
                dom_excerpt = dom_text[:2000] + "\n…"
            else:
                dom_excerpt = dom_text
        except Exception as exc:  # pragma: no cover - best effort only
            dom_excerpt = f"DOM extraction failed: {exc}"

        if self._agent is not None:
            try:
                stabilised_actions, warnings, catalog = self._stabilise_model_output(
                    browser_state,
                    model_output,
                )
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("Session %s: action stabilisation failed: %s", self.session_id, exc)
                stabilised_actions = None
                warnings = []
                catalog = None

            if stabilised_actions is not None:
                model_output.action = stabilised_actions
            for warning in warnings:
                self._add_warning(warning)
            action_warnings.extend(warnings)
            element_catalog = catalog

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
            "element_catalog": element_catalog.text if element_catalog else "",
            "element_catalog_metadata": element_catalog.metadata if element_catalog else {},
            "action_warnings": action_warnings,
            "timestamp": _now(),
        }
        with self._lock:
            self.steps.append(step_payload)
            self.updated_at = _now()

    def _stabilise_model_output(
        self,
        browser_state: BrowserStateSummary,
        model_output: AgentOutput,
    ) -> tuple[list[ActionModel] | None, list[str], ElementCatalogSnapshot | None]:
        selector_map = getattr(browser_state.dom_state, "selector_map", {}) or {}
        catalog = build_element_catalog(selector_map)

        if not model_output.action:
            return None, [], catalog

        action_model = getattr(self._agent, "ActionModel", None)
        if action_model is None:
            return None, [], catalog

        valid_indices: set[int] = set()
        for key in selector_map:
            try:
                valid_indices.add(int(key))
            except (TypeError, ValueError):  # pragma: no cover - defensive
                continue
        sanitised: list[ActionModel] = []
        warnings: list[str] = []

        index_requirements: dict[str, tuple[str, int]] = {
            "click_element_by_index": ("index", 1),
            "input_text": ("index", 0),
            "select_dropdown_option": ("index", 1),
            "get_dropdown_options": ("index", 1),
            "upload_file_to_element": ("index", 1),
        }

        for original_action in model_output.action:
            action_data = original_action.model_dump(exclude_unset=True)
            if not action_data:
                warnings.append("LLM returned an empty action; converted to wait(1s)")
                continue

            action_name, params = next(iter(action_data.items()))
            params = params or {}

            field_requirement = index_requirements.get(action_name)
            if field_requirement is not None:
                field_name, min_value = field_requirement
                index_value = params.get(field_name)
                if not isinstance(index_value, int):
                    warnings.append(
                        f"action '{action_name}' is missing integer '{field_name}'; removed"
                    )
                    continue
                if index_value < min_value:
                    warnings.append(
                        f"action '{action_name}' received invalid {field_name}={index_value}; removed"
                    )
                    continue
                if index_value not in valid_indices:
                    warnings.append(
                        f"action '{action_name}' target index {index_value} is no longer available; replaced with wait"
                    )
                    continue

            if action_name == "scroll":
                frame_index = params.get("frame_element_index")
                if frame_index is not None and frame_index not in valid_indices:
                    params = dict(params)
                    params.pop("frame_element_index", None)
                    warnings.append(
                        "scroll.frame_element_index pointed to a missing element and was ignored"
                    )

            sanitised.append(action_model(**{action_name: params}))

        if not sanitised:
            fallback = action_model(**{"wait": {"seconds": 1}})
            sanitised.append(fallback)

        return sanitised, warnings, catalog

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
            warnings_copy = copy.deepcopy(self.warnings)
        if warnings_copy:
            result["warnings"] = warnings_copy

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
        candidates = list(_candidate_cdp_endpoints())
        try:
            endpoint = _resolve_cdp_endpoint(candidates=candidates)
        except TypeError:
            endpoint = _resolve_cdp_endpoint()

        warm_candidates: list[str] = []
        warm_websocket: str | None = None

        if not endpoint:
            warm_candidates, warm_websocket = _warm_shared_browser(candidates)
            candidates = _merge_candidates(
                [warm_websocket] if warm_websocket else [],
                warm_candidates,
                candidates,
            )
            if warm_websocket:
                resolved_ws = _normalise_cdp_candidate(warm_websocket)
                endpoint = resolved_ws or warm_websocket
            if not endpoint:
                endpoint = _resolve_cdp_endpoint(candidates=candidates)
        else:
            candidates = _merge_candidates([endpoint], candidates)

        self._set_shared_browser_state("unknown", None)

        if endpoint:
            try:
                session = BrowserSession(cdp_url=endpoint, is_local=False)
            except Exception as exc:  # pragma: no cover - defensive
                detail = f"{type(exc).__name__}: {exc}"
                message = format_shared_browser_error(
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
            else:
                log.info(
                    "Session %s: attaching to shared browser at %s",
                    self.session_id,
                    endpoint,
                )
                self._set_shared_browser_state("remote", endpoint)
                return session

        approx_wait = max(
            0.0, (_CDP_PROBE_RETRIES - 1) * max(_CDP_PROBE_DELAY, 0.0)
        )
        reason = "共有ブラウザの CDP エンドポイントが見つからないか応答しませんでした"
        if approx_wait:
            reason += f"（待機時間: 約{approx_wait:.1f}秒）"
        message = format_shared_browser_error(
            reason,
            candidates=candidates,
        )
        log.error("Session %s: %s", self.session_id, message)
        raise RuntimeError(message)

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
                "warnings": copy.deepcopy(self.warnings),
                "shared_browser_mode": self.shared_browser_mode,
                "shared_browser_endpoint": self.shared_browser_endpoint,
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

    def start_session(
        self,
        command: str,
        *,
        model: str,
        max_steps: int,
        conversation_context: str | None = None,
    ) -> str:
        session = BrowserUseSession(
            command=command,
            model_name=model,
            max_steps=max_steps,
            history_context=conversation_context,
        )
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


class RemoteBrowserUseManager:
    """Proxy ``BrowserUseManager`` requests to the automation server."""

    _BASE_TIMEOUT = (5.0, 120.0)

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, object] | None = None,
        timeout: float | tuple[float, float] | None = None,
    ) -> requests.Response:
        last_exc: requests.RequestException | None = None
        for refresh in (False, True):
            base_url = get_vnc_api_base(refresh=refresh) if refresh else get_vnc_api_base()
            url = f"{base_url}/browser-use{path}"
            try:
                response = requests.request(
                    method,
                    url,
                    json=json_payload,
                    timeout=timeout,
                )
            except requests.RequestException as exc:  # pragma: no cover - network failure path
                last_exc = exc
                log.debug("Remote browser-use request to %s failed: %s", url, exc)
                continue
            return response

        assert last_exc is not None
        raise RuntimeError(f"automation server request failed: {last_exc}") from last_exc

    @staticmethod
    def _error_details(response: requests.Response) -> tuple[str, str | None]:
        message = ""
        code: str | None = None
        try:
            payload = response.json()
        except ValueError:
            text = response.text.strip()
            if text:
                message = text
        else:
            raw_message = payload.get("error") or payload.get("message")
            if isinstance(raw_message, str):
                message = raw_message
            raw_code = payload.get("code")
            if isinstance(raw_code, str) and raw_code:
                code = raw_code

        if not message:
            message = f"automation server returned unexpected status {response.status_code}"
        return message, code

    def start_session(
        self,
        command: str,
        *,
        model: str,
        max_steps: int,
        conversation_context: str | None = None,
    ) -> str:
        payload = {
            "command": command,
            "model": model,
            "max_steps": max_steps,
        }
        if conversation_context:
            payload["conversation_context"] = conversation_context
        response = self._request(
            "post",
            "/session",
            json_payload=payload,
            timeout=self._BASE_TIMEOUT,
        )

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as exc:
                raise RuntimeError("automation server returned malformed response") from exc
            session_id = data.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise RuntimeError("automation server response missing session identifier")
            log.info("Started remote browser-use session %s for command: %s", session_id, command)
            return session_id

        message, code = self._error_details(response)
        if response.status_code == 400:
            raise ValueError(message)
        if response.status_code == 503 and code == "shared_browser_unavailable":
            raise RuntimeError(message)
        if response.status_code in {503, 504}:
            raise RuntimeError(message)
        raise RuntimeError(message)

    def get_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        response = self._request(
            "get",
            f"/session/{session_id}",
            timeout=15.0,
        )

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as exc:
                raise RuntimeError("automation server returned malformed status payload") from exc
            return data

        if response.status_code == 404:
            return None

        message, _ = self._error_details(response)
        raise RuntimeError(message)

    def cancel_session(self, session_id: str) -> bool:
        response = self._request(
            "post",
            f"/session/{session_id}/cancel",
            timeout=15.0,
        )

        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False

        message, _ = self._error_details(response)
        raise RuntimeError(message)

    def shutdown(self) -> None:  # pragma: no cover - remote manager has no local resources
        return None


_browser_use_manager: BrowserUseManager | None = None
_remote_browser_use_manager: RemoteBrowserUseManager | None = None


def _use_remote_manager() -> bool:
    return env_flag("BROWSER_USE_REMOTE_API", default=False)


def get_browser_use_manager() -> BrowserUseManager | RemoteBrowserUseManager:
    if _use_remote_manager():
        global _remote_browser_use_manager
        if _remote_browser_use_manager is None:
            _remote_browser_use_manager = RemoteBrowserUseManager()
        return _remote_browser_use_manager

    global _browser_use_manager
    if _browser_use_manager is None:
        _browser_use_manager = BrowserUseManager()
    return _browser_use_manager
