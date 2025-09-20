from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import asyncio

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "vnc.dependency_check" not in sys.modules:
    dependency_stub = types.ModuleType("vnc.dependency_check")

    def _noop_dependency_check(*args: Any, **kwargs: Any) -> None:  # pragma: no cover - simple stub
        return None

    dependency_stub.ensure_component_dependencies = _noop_dependency_check
    sys.modules["vnc.dependency_check"] = dependency_stub

from vnc import automation_server


def test_dom_changes_trigger_catalog_refresh_before_index(monkeypatch):
    """Ensure DOM-changing actions refresh the catalog before resolving index targets."""

    call_sequence: List[Any] = []
    refresh_calls: List[bool] = []

    automation_server.PAGE = object()
    original_index_mode = automation_server.INDEX_MODE
    original_catalog = automation_server._CURRENT_CATALOG
    original_signature = automation_server._CURRENT_CATALOG_SIGNATURE
    automation_server.INDEX_MODE = True
    automation_server._CURRENT_CATALOG = {
        "index_map": {
            "5": {"robust_selectors": ["css=#button"], "stable_id": "node-5"}
        },
        "catalog_version": "before",
    }
    automation_server._CURRENT_CATALOG_SIGNATURE = {"catalog_version": "before"}

    async def fake_stabilize_page(*args: Any, **kwargs: Any) -> None:
        call_sequence.append("stabilize")

    async def fake_resolve_selector_candidates(
        page: Any,
        selector_candidates: Any,
        *,
        store: Any,
        timeout_ms: Any = None,
        retries: Any = None,
    ) -> Any:
        call_sequence.append(("resolve_selectors", [display for _, display in selector_candidates]))

        class DummyResolved:
            def __init__(self) -> None:
                self.locator = object()

        return DummyResolved(), selector_candidates[0][1], [], None

    async def fake_safe_fill(*args: Any, **kwargs: Any) -> None:
        value = args[1] if len(args) > 1 else kwargs.get("value")
        call_sequence.append(("fill", value))

    async def fake_safe_click(*args: Any, **kwargs: Any) -> None:
        call_sequence.append(("click",))

    async def fake_safe_get_page_content(*args: Any, **kwargs: Any) -> str:
        return "<html></html>"

    async def fake_generate_element_catalog(*, force: bool = False) -> Dict[str, Any]:
        refresh_calls.append(force)
        call_sequence.append(("refresh", force))
        automation_server._CURRENT_CATALOG = {
            "index_map": {
                "5": {"robust_selectors": ["css=#button"], "stable_id": "node-5"}
            },
            "catalog_version": "after",
        }
        automation_server._CURRENT_CATALOG_SIGNATURE = {"catalog_version": "after"}
        return automation_server._CURRENT_CATALOG

    def fake_resolve_index_entry(index: int) -> Any:
        call_sequence.append(("resolve_index", index))
        return ["css=#button"], {"stable_id": "node-5"}

    monkeypatch.setattr(automation_server, "_stabilize_page", fake_stabilize_page)
    monkeypatch.setattr(automation_server, "_resolve_selector_candidates", fake_resolve_selector_candidates)
    monkeypatch.setattr(automation_server, "_safe_fill", fake_safe_fill)
    monkeypatch.setattr(automation_server, "_safe_click", fake_safe_click)
    monkeypatch.setattr(automation_server, "_safe_get_page_content", fake_safe_get_page_content)
    monkeypatch.setattr(automation_server, "_generate_element_catalog", fake_generate_element_catalog)
    monkeypatch.setattr(automation_server, "_resolve_index_entry", fake_resolve_index_entry)

    plan = [
        {"action": "type", "target": {"css": "#input"}, "value": "abc"},
        {"action": "click", "target": "index=5"},
    ]

    try:
        html, warnings = asyncio.run(automation_server._run_actions(plan, correlation_id="dom-test"))
    finally:
        automation_server.PAGE = None
        automation_server.INDEX_MODE = original_index_mode
        automation_server._CURRENT_CATALOG = original_catalog
        automation_server._CURRENT_CATALOG_SIGNATURE = original_signature

    assert html == "<html></html>"
    assert isinstance(warnings, list)
    assert refresh_calls == [True], "Catalog refresh should be forced exactly once"

    refresh_index = call_sequence.index(("refresh", True))
    resolve_index = call_sequence.index(("resolve_index", 5))
    assert refresh_index < resolve_index, "Catalog refresh must occur before resolving the index target"
