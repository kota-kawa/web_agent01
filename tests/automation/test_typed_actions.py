"""Tests for typed DSL execution via the automation server."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "vnc.dependency_check" not in sys.modules:
    dependency_stub = types.ModuleType("vnc.dependency_check")

    def _noop_dependency_check(*args, **kwargs):  # pragma: no cover - simple stub
        return None

    dependency_stub.ensure_component_dependencies = _noop_dependency_check
    sys.modules["vnc.dependency_check"] = dependency_stub

from vnc import automation_server


def test_execute_dsl_typed_actions(monkeypatch):
    """Ensure typed actions are forwarded to the RunExecutor pipeline."""

    captured_payloads: List[Dict[str, Any]] = []

    async def fake_init_browser():
        automation_server.PAGE = object()

    class FakeExecutor:
        def __init__(self, page, config=None):  # pragma: no cover - simple test double
            self.page = page
            self.config = config

        async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
            captured_payloads.append(payload)
            return {
                "success": True,
                "results": [
                    {"ok": True, "details": {"action": payload["plan"][0]["type"]}},
                    {"ok": True, "details": {"action": payload["plan"][1]["type"]}},
                ],
                "warnings": [],
                "html": "<html></html>",
                "run_id": payload.get("run_id", "typed-run"),
                "observation": {},
            }

    monkeypatch.setattr(automation_server, "_init_browser", fake_init_browser)
    monkeypatch.setattr(automation_server, "RunExecutor", FakeExecutor)
    automation_server.PAGE = None

    client = automation_server.app.test_client()

    payload = {
        "run_id": "typed-test",
        "actions": [
            {"type": "switch_tab", "target": {"strategy": "latest"}},
            {"type": "focus_iframe", "target": {"strategy": "root"}},
            {"type": "assert", "selector": {"css": "#main"}, "state": "visible"},
            {
                "type": "submit_form",
                "fields": [{"selector": {"css": "#name"}, "value": "Alice"}],
                "submit_via": "enter",
            },
        ],
    }

    response = client.post("/execute-dsl", json=payload)
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert captured_payloads, "RunExecutor should be invoked for typed actions"

    executed_payload = captured_payloads[0]
    assert executed_payload["run_id"] == "typed-test"
    assert [action["type"] for action in executed_payload["plan"]] == [
        "switch_tab",
        "focus_iframe",
        "assert",
        "submit_form",
    ]

    automation_server.PAGE = None
