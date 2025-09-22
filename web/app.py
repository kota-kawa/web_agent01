from __future__ import annotations

import atexit
import logging
import os
from typing import Any

from flask import Flask, jsonify, render_template, request, send_from_directory

from agent.browser_use_runner import get_browser_use_manager
from agent.utils import history as history_utils
from agent.utils.history import load_hist, save_hist

app = Flask(__name__)
log = logging.getLogger("agent")
log.setLevel(logging.INFO)

MAX_STEPS = max(1, int(os.getenv("MAX_STEPS", "15")))
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemini")
HIST_FILE = history_utils.HIST_FILE


@app.errorhandler(404)
def not_found(error: Exception):  # pragma: no cover - simple JSON handler
    return jsonify({"error": f"resource not found: {request.path}"}), 404


@app.errorhandler(Exception)
def handle_exception(error: Exception):  # pragma: no cover - defensive handler
    log.exception("Unhandled exception: %s", error)
    return jsonify({"error": "internal server error"}), 500


@app.route("/")
def index():
    return render_template(
        "layout.html",
        default_model=DEFAULT_MODEL,
        max_steps=MAX_STEPS,
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
