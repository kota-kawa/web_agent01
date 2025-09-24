from __future__ import annotations

import asyncio
import atexit
import base64
import inspect
import logging
import os
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from flask import Flask, Response, jsonify, request
from playwright.async_api import Error as PwError, async_playwright

from agent.browser_use_runner import BrowserUseManager
from agent.utils.history import format_history_for_prompt, load_hist
from agent.utils.shared_browser import format_shared_browser_error, normalise_cdp_websocket
from vnc.dependency_check import ensure_component_dependencies

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("auto")

ensure_component_dependencies("vnc", logger=log)

_browser_use_manager: BrowserUseManager | None = None

_DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemini")
_DEFAULT_MAX_STEPS = max(1, int(os.getenv("MAX_STEPS", "15")))
_DEFAULT_URL = os.getenv("DEFAULT_URL", "https://www.yahoo.co.jp/")
_NAVIGATION_TIMEOUT = int(os.getenv("NAVIGATION_TIMEOUT", "30000"))

_CDP_ENV_VARS = ("VNC_CDP_URL", "BROWSER_USE_CDP_URL", "CDP_URL")
_CDP_DEFAULT_ENDPOINTS = (
    "http://127.0.0.1:9222",
    "http://localhost:9222",
    "http://vnc:9222",
)
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _get_browser_use_manager() -> BrowserUseManager:
    global _browser_use_manager
    if _browser_use_manager is None:
        _browser_use_manager = BrowserUseManager()
    return _browser_use_manager


@atexit.register
def _shutdown_browser_use_manager() -> None:  # pragma: no cover - shutdown path
    manager = _browser_use_manager
    if manager is None:
        return
    try:
        manager.shutdown()
    except Exception as exc:
        log.debug("Browser-use manager shutdown failed: %s", exc)


@app.errorhandler(500)
def internal_server_error(error):  # pragma: no cover - defensive handler
    correlation_id = str(uuid.uuid4())[:8]
    error_msg = f"Internal server error - {error}"
    log.exception("[%s] Unhandled exception: %s", correlation_id, error_msg)
    return jsonify(
        {
            "html": "",
            "warnings": [
                f"ERROR:auto:[{correlation_id}] Internal failure - An unexpected error occurred"
            ],
            "correlation_id": correlation_id,
        }
    ), 200


@app.errorhandler(Exception)
def handle_exception(error):  # pragma: no cover - defensive handler
    correlation_id = str(uuid.uuid4())[:8]
    log.exception("[%s] Uncaught exception: %s", correlation_id, error)
    return jsonify(
        {
            "html": "",
            "warnings": [
                f"ERROR:auto:[{correlation_id}] Internal failure - {error}"
            ],
            "correlation_id": correlation_id,
        }
    ), 200


# ---------------------------------------------------------------------------
# CDP helpers


def _normalise_cdp_candidate(value: Optional[str]) -> str:
    if not value:
        return ""
    trimmed = value.strip()
    if not trimmed:
        return ""
    lowered = trimmed.lower()
    if lowered.startswith(("http://", "https://", "ws://", "wss://")):
        return trimmed.rstrip("/")
    if trimmed.startswith("//"):
        return f"http:{trimmed}".rstrip("/")
    if ":" in trimmed:
        return f"http://{trimmed}".rstrip("/")
    return trimmed


def _candidate_cdp_endpoints() -> List[str]:
    candidates: List[str] = []
    seen: set[str] = set()

    def _add(candidate: Optional[str]) -> None:
        normalised = _normalise_cdp_candidate(candidate)
        if normalised and normalised not in seen:
            seen.add(normalised)
            candidates.append(normalised)

    for env_name in _CDP_ENV_VARS:
        _add(os.getenv(env_name))
    for default in _CDP_DEFAULT_ENDPOINTS:
        _add(default)

    if not candidates:
        candidates.append("http://127.0.0.1:9222")
    return candidates


def _dedupe_candidates(*groups: Iterable[str | None]) -> List[str]:
    merged: List[str] = []
    seen: set[str] = set()
    for group in groups:
        if not group:
            continue
        for candidate in group:
            normalised = _normalise_cdp_candidate(candidate)
            if normalised and normalised not in seen:
                seen.add(normalised)
                merged.append(normalised)
    return merged


def _json_version_url(base: str) -> str:
    base = (base or "").strip()
    if not base:
        return ""
    working = base
    if working.startswith("//"):
        working = f"http:{working}"
    elif "://" not in working:
        working = f"http://{working}"
    try:
        parsed = urlsplit(working)
    except ValueError:
        return ""
    return urlunsplit((parsed.scheme or "http", parsed.netloc, "/json/version", "", ""))


async def _wait_cdp(endpoint: str, *, timeout: float = 6.0, poll_interval: float = 0.25) -> bool:
    version_url = _json_version_url(endpoint)
    if not version_url:
        return False
    poll_interval = max(poll_interval, 0.25)
    deadline = time.time() + max(timeout, 1.0)
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.time() < deadline:
            try:
                response = await client.get(version_url)
                if response.status_code == 200:
                    return True
            except httpx.HTTPError as exc:
                log.debug("CDP endpoint %s not ready: %s", version_url, exc)
            await asyncio.sleep(poll_interval)
    log.warning("Timed out waiting for CDP endpoint %s", version_url)
    return False


async def _fetch_cdp_metadata(endpoint: str) -> Dict[str, Any]:
    version_url = _json_version_url(endpoint)
    if not version_url:
        return {}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(version_url)
    except httpx.HTTPError as exc:
        log.debug("Failed to fetch CDP metadata from %s: %s", version_url, exc)
        return {}
    if response.status_code != 200:
        log.debug(
            "CDP metadata request to %s returned status %s",
            version_url,
            response.status_code,
        )
        return {}
    try:
        payload = response.json()
    except ValueError:
        log.debug("CDP metadata response from %s was not valid JSON", version_url)
        return {}
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# Playwright management


LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

PW = None
BROWSER = None
PAGE = None
CDP_URL: str | None = os.getenv("CDP_URL")

_BROWSER_FIRST_INIT = True


def _run(coro):
    return LOOP.run_until_complete(coro)


async def _close_browser() -> None:
    global PW, BROWSER, PAGE
    page = PAGE
    browser = BROWSER
    PAGE = None
    BROWSER = None
    try:
        if page is not None:
            await page.close()
    except Exception:
        pass
    try:
        if browser is not None:
            await browser.close()
    except Exception:
        pass
    if PW is not None:
        try:
            await PW.stop()
        except Exception:
            pass
        PW = None


@atexit.register
def _cleanup_browser() -> None:  # pragma: no cover - shutdown hook
    try:
        _run(_close_browser())
    except Exception as exc:
        log.debug("Error during Playwright shutdown: %s", exc)


async def _check_browser_health() -> bool:
    if PAGE is None or BROWSER is None:
        return False
    try:
        await PAGE.title()
        return True
    except PwError:
        return False
    except Exception:
        return False


async def _init_browser() -> None:
    global PW, BROWSER, PAGE, CDP_URL, _BROWSER_FIRST_INIT
    if PAGE is not None and await _check_browser_health():
        return

    if PW is None:
        PW = await async_playwright().start()

    candidates = _candidate_cdp_endpoints()
    connection_errors: List[str] = []
    connected_endpoint: str | None = None

    for candidate in candidates:
        if not candidate:
            continue
        if not await _wait_cdp(candidate):
            connection_errors.append(f"共有ブラウザ {candidate} が応答しませんでした")
            continue
        browser = None
        try:
            browser = await PW.chromium.connect_over_cdp(candidate)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
            await page.bring_to_front()
        except PwError as exc:
            connection_errors.append(
                f"共有ブラウザ {candidate} への接続に失敗しました（{type(exc).__name__}: {exc}）"
            )
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            continue
        except Exception as exc:
            connection_errors.append(
                f"共有ブラウザ {candidate} への接続に失敗しました（{type(exc).__name__}: {exc}）"
            )
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            continue

        BROWSER = browser
        PAGE = page
        connected_endpoint = candidate
        break

    if PAGE is None:
        reason = (
            connection_errors[-1]
            if connection_errors
            else "共有ブラウザの CDP エンドポイントが見つからないか応答しませんでした"
        )
        message = format_shared_browser_error(reason, candidates=candidates)
        log.error("Automation server could not connect to a shared browser: %s", message)
        await _close_browser()
        raise RuntimeError(message)

    CDP_URL = connected_endpoint or CDP_URL
    if connected_endpoint:
        log.info("Connected to shared browser via %s", connected_endpoint)

    if _BROWSER_FIRST_INIT:
        try:
            await PAGE.goto(_DEFAULT_URL, wait_until="load", timeout=_NAVIGATION_TIMEOUT)
            log.info("Initial navigation to default URL: %s", _DEFAULT_URL)
        except Exception as exc:
            log.debug("Failed to navigate to default URL: %s", exc)
        _BROWSER_FIRST_INIT = False


async def _recreate_browser() -> None:
    await _close_browser()
    await _init_browser()


async def _safe_get_page_content() -> str:
    if PAGE is None:
        return ""
    try:
        return await PAGE.content()
    except Exception as exc:
        log.debug("Failed to retrieve page content: %s", exc)
        return ""


async def _get_page_url_value() -> str:
    if PAGE is None:
        return ""
    try:
        attr = getattr(PAGE, "url", "")
    except Exception as exc:
        log.debug("Unable to access page.url: %s", exc)
        return ""
    try:
        if callable(attr):
            value = attr()
            if inspect.isawaitable(value):
                value = await value
        else:
            value = attr
    except TypeError:
        value = attr
    except Exception as exc:
        log.debug("Failed to evaluate page.url: %s", exc)
        return ""
    return str(value or "")


# ---------------------------------------------------------------------------
# Browser-use API


@app.post("/browser-use/session")
def start_browser_use_session():
    correlation_id = str(uuid.uuid4())[:8]
    data = request.get_json(force=True) or {}

    command = str(data.get("command", "")).strip()
    if not command:
        return jsonify({"error": "command empty"}), 400

    model_value = data.get("model")
    model = str(model_value).strip() if model_value is not None else _DEFAULT_MODEL
    if not model:
        model = _DEFAULT_MODEL

    requested_steps = data.get("max_steps")
    max_steps = _DEFAULT_MAX_STEPS
    if requested_steps is not None:
        try:
            max_steps = int(requested_steps)
        except (TypeError, ValueError):
            return jsonify({"error": "max_steps must be an integer"}), 400
        if max_steps <= 0:
            return jsonify({"error": "max_steps must be positive"}), 400

    context_value = data.get("conversation_context")
    if isinstance(context_value, str):
        conversation_context = context_value.strip()
    else:
        conversation_context = ""

    if not conversation_context:
        try:
            conversation_context = format_history_for_prompt(load_hist())
        except Exception as exc:  # pragma: no cover - best effort only
            log.debug("[%s] Failed to prepare conversation history: %s", correlation_id, exc)
            conversation_context = ""

    manager = _get_browser_use_manager()
    try:
        session_id = manager.start_session(
            command,
            model=model,
            max_steps=max_steps,
            conversation_context=conversation_context or None,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        message = str(exc)
        payload = {"error": message}
        status = 500
        if "ライブビューのブラウザに接続できないため実行できません" in message:
            payload["code"] = "shared_browser_unavailable"
            status = 503
            log.error("[%s] Failed to start browser-use session: %s", correlation_id, message)
        else:
            log.exception("[%s] Browser-use session start failed", correlation_id)
        return jsonify(payload), status
    except Exception as exc:  # pragma: no cover - defensive fallback
        log.exception("[%s] Browser-use session start failed unexpectedly", correlation_id)
        return jsonify({"error": "failed to start automation"}), 500

    return jsonify({"session_id": session_id})


@app.get("/browser-use/session/<session_id>")
def get_browser_use_session(session_id: str):
    info = _get_browser_use_manager().get_status(session_id)
    if info is None:
        return jsonify({"error": "session not found"}), 404
    return jsonify(info)


@app.post("/browser-use/session/<session_id>/cancel")
def cancel_browser_use_session(session_id: str):
    if not _get_browser_use_manager().cancel_session(session_id):
        return jsonify({"error": "session not found"}), 404
    return jsonify({"status": "cancelled"})


# ---------------------------------------------------------------------------
# Shared browser helpers


@app.post("/shared-browser/ensure")
def ensure_shared_browser():
    correlation_id = str(uuid.uuid4())[:8]
    candidates = _candidate_cdp_endpoints()
    deduped = _dedupe_candidates(candidates)
    payload: Dict[str, Any] = {
        "correlation_id": correlation_id,
        "candidates": deduped,
    }

    try:
        _run(_init_browser())
        ready = bool(_run(_check_browser_health()))
    except Exception as exc:
        log.error("[%s] Shared browser warmup failed: %s", correlation_id, exc)
        payload.update(
            {
                "status": "error",
                "ready": False,
                "cdp_ready": False,
                "error": str(exc),
            }
        )
        return jsonify(payload), 503

    active_endpoint = CDP_URL or (deduped[0] if deduped else "")

    public_endpoint = ""
    for candidate in deduped:
        host = (urlsplit(candidate if "://" in candidate else f"http://{candidate}").hostname or "").lower()
        if host and host not in _LOOPBACK_HOSTS:
            public_endpoint = candidate
            break
    if not public_endpoint:
        public_endpoint = active_endpoint

    metadata: Dict[str, Any] = {}
    if public_endpoint:
        try:
            metadata = _run(_fetch_cdp_metadata(public_endpoint))
        except Exception as exc:
            log.debug("[%s] Failed to retrieve CDP metadata from %s: %s", correlation_id, public_endpoint, exc)
            metadata = {}

    public_websocket: Optional[str] = None
    if metadata:
        raw_ws = metadata.get("webSocketDebuggerUrl") or metadata.get("websocketDebuggerUrl")
        if isinstance(raw_ws, str) and raw_ws.strip():
            public_websocket = normalise_cdp_websocket(raw_ws)
    if not public_websocket and active_endpoint:
        public_websocket = normalise_cdp_websocket(active_endpoint)

    payload.update(
        {
            "status": "ok",
            "ready": ready,
            "cdp_ready": bool(public_websocket),
            "active_endpoint": active_endpoint,
            "public_endpoint": public_endpoint,
            "public_websocket": public_websocket,
            "json_version_url": _json_version_url(public_endpoint) if public_endpoint else "",
        }
    )
    if metadata:
        payload["metadata"] = metadata

    return jsonify(payload)


# ---------------------------------------------------------------------------
# Basic page introspection endpoints


@app.get("/source")
def source():
    try:
        _run(_init_browser())
        html = _run(_safe_get_page_content())
        return Response(html, mimetype="text/plain")
    except Exception as exc:
        log.error("source error: %s", exc)
        return Response(str(exc), mimetype="text/plain", status=500)


@app.get("/url")
def current_url():
    try:
        _run(_init_browser())
        url = _run(_get_page_url_value())
        return jsonify({"url": url})
    except Exception as exc:
        log.error("url error: %s", exc)
        return jsonify({"url": "", "error": str(exc)})


@app.get("/screenshot")
def screenshot():
    try:
        _run(_init_browser())
        if PAGE is None:
            raise RuntimeError("browser not ready")
        image = _run(PAGE.screenshot(type="png"))
        return Response(base64.b64encode(image), mimetype="text/plain")
    except Exception as exc:
        log.error("screenshot error: %s", exc)
        return Response(str(exc), mimetype="text/plain", status=500)


@app.get("/healthz")
def health():  # pragma: no cover - trivial endpoint
    return "ok", 200


if __name__ == "__main__":  # pragma: no cover - manual run helper
    app.run("0.0.0.0", 7000, threaded=False)
