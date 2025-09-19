"""Flask application exposing the automation service via HTTP."""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from typing import Any, Dict

from flask import Flask, Response, jsonify, request

from automation.service import AutomationService

log = logging.getLogger(__name__)


def create_app(service: AutomationService | None = None) -> Flask:
    automation = service or AutomationService()
    app = Flask(__name__)

    @app.post("/execute-dsl")
    def execute_dsl() -> Response:
        correlation_id = uuid.uuid4().hex[:8]
        try:
            payload: Dict[str, Any] = request.get_json(force=True) or {}
            if isinstance(payload, list):
                payload = {"actions": payload}
            payload.setdefault("correlation_id", correlation_id)
            result = automation.execute_plan(payload)
            return jsonify(result)
        except Exception as exc:  # pragma: no cover - defensive safeguard
            log.exception("execute-dsl failed: %s", exc)
            error_payload = {
                "success": False,
                "error": {"code": "SERVER_ERROR", "message": str(exc)},
                "warnings": [f"ERROR:auto:{str(exc)}"],
                "html": "",
                "correlation_id": correlation_id,
                "observation": {"catalog_version": None, "url": ""},
                "results": [],
                "is_done": True,
                "complete": True,
            }
            return jsonify(error_payload), 200

    @app.get("/source")
    def source() -> Response:
        try:
            html = automation.get_html()
        except Exception as exc:  # pragma: no cover - defensive safeguard
            log.error("source endpoint failed: %s", exc)
            html = ""
        return Response(html, mimetype="text/plain")

    @app.get("/url")
    def current_url() -> Response:
        try:
            url = automation.get_url()
        except Exception as exc:  # pragma: no cover
            log.error("url endpoint failed: %s", exc)
            url = ""
        return jsonify({"url": url})

    @app.get("/screenshot")
    def screenshot() -> Response:
        try:
            data = automation.get_screenshot()
            encoded = base64.b64encode(data)
            return Response(encoded, mimetype="text/plain")
        except Exception as exc:  # pragma: no cover
            log.error("screenshot endpoint failed: %s", exc)
            return Response("", mimetype="text/plain", status=500)

    @app.get("/elements")
    def elements() -> Response:
        try:
            data = automation.get_elements()
        except Exception as exc:  # pragma: no cover
            log.error("elements endpoint failed: %s", exc)
            data = []
        return jsonify(data)

    @app.get("/catalog")
    def catalog() -> Response:
        refresh = request.args.get("refresh", "false").lower() in {"1", "true", "yes"}
        data = automation.get_catalog(refresh=refresh)
        return jsonify(data)

    @app.get("/extracted")
    def extracted() -> Response:
        return jsonify(automation.get_extracted())

    @app.get("/eval_results")
    def eval_results() -> Response:
        return jsonify(automation.get_eval_results())

    @app.get("/stop-request")
    def stop_request() -> Response:
        return jsonify(automation.get_stop_request())

    @app.post("/stop-response")
    def stop_response() -> Response:
        data = request.get_json(force=True) or {}
        automation.record_stop_response(str(data.get("response", "")))
        return jsonify({"status": "success"})

    @app.get("/events/<run_id>")
    def events(run_id: str) -> Response:
        content = automation.get_events(run_id)
        if content is None:
            return jsonify({"error": "events_not_found"}), 404
        return Response(content, mimetype="application/json")

    @app.get("/healthz")
    def healthz() -> Response:
        try:
            healthy = asyncio.run(automation.adapter.is_healthy())
        except Exception:  # pragma: no cover - defensive safeguard
            healthy = False
        if healthy:
            return "ok", 200
        return "unhealthy", 503

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    logging.basicConfig(level=logging.INFO)
    app.run("0.0.0.0", 7000, threaded=False)
