from __future__ import annotations

import atexit
import logging
import os
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Flask, jsonify, render_template, request, send_from_directory

from agent.browser_use_runner import get_browser_use_manager
from agent.utils import history as history_utils
from agent.utils.history import load_hist, save_hist
from vnc.dependency_check import ensure_component_dependencies

app = Flask(__name__)
log = logging.getLogger("agent")
log.setLevel(logging.INFO)

ensure_component_dependencies("web", logger=log)

MAX_STEPS = max(1, int(os.getenv("MAX_STEPS", "15")))
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemini")
START_URL = os.getenv("START_URL", "https://www.yahoo.co.jp/")
HIST_FILE = history_utils.HIST_FILE

_NOVNC_DEFAULTS = (
    ("autoconnect", "1"),
    ("resize", "scale"),
    ("reconnect", "true"),
    ("path", "websockify"),
)


def _normalise_novnc_url(raw_url: str) -> str:
    """Normalise an endpoint for embedding the bundled noVNC client.

    The automation UI expects the iframe to point at ``vnc.html`` with query
    parameters that trigger an immediate connection.  Operators can still
    override the path/query portion via :envvar:`NOVNC_URL`, but this helper
    ensures sensible defaults when they are omitted.
    """

    raw_url = (raw_url or "").strip()
    if not raw_url:
        return ""

    try:
        parsed = urlsplit(raw_url, allow_fragments=True)
    except ValueError:
        return raw_url

    if parsed.scheme and not parsed.netloc and not parsed.path:
        # ``urlsplit('foo')`` treats ``foo`` as a scheme.  In that case we
        # cannot reliably inject defaults, so return the value unchanged.
        return raw_url

    path = parsed.path or ""
    trimmed = path[:-1] if path.endswith("/") else path

    if not trimmed:
        normalised_path = "/vnc.html"
    else:
        if not trimmed.lower().endswith(".html"):
            trimmed = f"{trimmed}/vnc.html"
        normalised_path = trimmed
        if not normalised_path.startswith("/"):
            normalised_path = "/" + normalised_path.lstrip("/")

    query_items = list(parse_qsl(parsed.query, keep_blank_values=True))
    seen = {key for key, _ in query_items}
    for key, value in _NOVNC_DEFAULTS:
        if key not in seen:
            query_items.append((key, value))
            seen.add(key)

    new_query = urlencode(query_items, doseq=True)

    return urlunsplit(parsed._replace(path=normalised_path, query=new_query))


@app.errorhandler(404)
def not_found(error: Exception):  # pragma: no cover - simple JSON handler
    return jsonify({"error": f"resource not found: {request.path}"}), 404


@app.errorhandler(Exception)
def handle_exception(error: Exception):  # pragma: no cover - defensive handler
    log.exception("Unhandled exception: %s", error)
    return jsonify({"error": "internal server error"}), 500


def _compute_novnc_url() -> str:
    configured_url = (os.getenv("NOVNC_URL") or "").strip()
    if configured_url:
        return _normalise_novnc_url(configured_url)

    configured_port = (os.getenv("NOVNC_PORT") or "").strip()
    port_value = "6901"
    if configured_port:
        try:
            port_int = int(configured_port)
            if port_int > 0:
                port_value = str(port_int)
        except (TypeError, ValueError):
            log.debug("Ignoring invalid NOVNC_PORT value: %s", configured_port)

    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    scheme = (forwarded_proto.split(",")[0].strip() if forwarded_proto else None) or request.scheme or "http"

    forwarded_host = request.headers.get("X-Forwarded-Host", "")
    host_reference = forwarded_host.split(",")[0].strip() if forwarded_host else request.host

    try:
        parsed = urlsplit(f"{scheme}://{host_reference}")
    except ValueError:
        parsed = urlsplit(request.host_url)

    hostname = parsed.hostname or "localhost"
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    return _normalise_novnc_url(f"{scheme}://{hostname}:{port_value}")


@app.route("/")
def index():
    novnc_url = _compute_novnc_url()
    return render_template(
        "layout.html",
        default_model=DEFAULT_MODEL,
        max_steps=MAX_STEPS,
        start_url=START_URL,
        novnc_url=novnc_url,
    )


@app.post("/execute")
def execute():
    data: dict[str, Any] = request.get_json(force=True) or {}
    command = str(data.get("command", "")).strip()
    if not command:
        return jsonify({"error": "command empty"}), 400

    model = str(data.get("model") or DEFAULT_MODEL).strip()
    requested_steps = data.get("max_steps")
    max_steps = MAX_STEPS
    if requested_steps is not None:
        try:
            max_steps = int(requested_steps)
        except (TypeError, ValueError):
            return jsonify({"error": "max_steps must be an integer"}), 400
        if max_steps <= 0:
            return jsonify({"error": "max_steps must be positive"}), 400

    manager = get_browser_use_manager()
    try:
        session_id = manager.start_session(command, model=model, max_steps=max_steps)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # pragma: no cover - runtime failure path
        log.exception("Failed to start automation run")
        return jsonify({"error": "failed to start automation"}), 500

    return jsonify({"session_id": session_id})


@app.get("/status/<session_id>")
def get_status(session_id: str):
    info = get_browser_use_manager().get_status(session_id)
    if info is None:
        return jsonify({"error": "session not found"}), 404
    return jsonify(info)


@app.post("/cancel/<session_id>")
def cancel(session_id: str):
    manager = get_browser_use_manager()
    if not manager.cancel_session(session_id):
        return jsonify({"error": "session not found"}), 404
    return jsonify({"status": "cancelled"})


@app.get("/history")
def history():
    try:
        return jsonify(load_hist())
    except Exception as exc:  # pragma: no cover - defensive
        log.error("Failed to load history: %s", exc)
        return jsonify({"error": "failed to load history", "data": []}), 500


@app.get("/history.json")
def history_file():
    if os.path.exists(HIST_FILE):
        return send_from_directory(
            directory=os.path.dirname(HIST_FILE),
            path=os.path.basename(HIST_FILE),
            mimetype="application/json",
        )
    return jsonify({"error": "history file not found"}), 404


@app.post("/reset")
def reset():
    try:
        save_hist([])
        return jsonify({"status": "success", "message": "会話履歴がリセットされました"})
    except Exception as exc:  # pragma: no cover - defensive
        log.error("Failed to reset history: %s", exc)
        return jsonify({"error": str(exc)}), 500


@atexit.register
def _shutdown_manager() -> None:  # pragma: no cover - shutdown hook
    try:
        get_browser_use_manager().shutdown()
    except Exception as exc:
        log.debug("Shutdown cleanup failed: %s", exc)


if __name__ == "__main__":  # pragma: no cover - manual run helper
    app.run(host="0.0.0.0", port=5000, debug=True)
