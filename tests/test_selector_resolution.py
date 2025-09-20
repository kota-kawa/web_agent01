import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

dependency_stub = types.ModuleType("dependency_check")
dependency_stub.ensure_component_dependencies = lambda *args, **kwargs: None
sys.modules.setdefault("vnc.dependency_check", dependency_stub)

from automation.dsl import Selector

from vnc import automation_server as server


def test_resolver_retries_until_element_appears(monkeypatch):
    calls = {"count": 0}

    class FakeLocator:
        async def inner_text(self) -> str:
            return "ok"

        async def get_attribute(self, name: str):  # pragma: no cover - compatibility
            return None

    fake_locator = FakeLocator()

    class FakeResolved:
        def __init__(self, selector: Selector) -> None:
            self.locator = fake_locator
            self.selector = selector

    async def fake_stabilize_page() -> None:
        calls.setdefault("stabilized", 0)
        calls["stabilized"] += 1

    monkeypatch.setattr(server, "_stabilize_page", fake_stabilize_page)
    monkeypatch.setattr(server, "LOCATOR_POLL_INTERVAL", 0.01)

    class FakeResolver:
        def __init__(self, page, store) -> None:
            self.page = page
            self.store = store

        async def resolve(self, selector: Selector):
            calls["count"] += 1
            assert isinstance(selector, Selector)
            # Typed selectors should preserve css and stable_id hints.
            assert selector.css == "#login-btn"
            assert selector.stable_id == "stable-123"
            if calls["count"] < 3:
                raise LookupError("not yet")
            return FakeResolved(selector)

    monkeypatch.setattr(server, "SelectorResolver", FakeResolver)

    selector_input = {"css": "#login-btn", "stable_id": "stable-123"}
    candidates = server._prepare_selector_candidates(selector_input, action="click")
    assert candidates  # ensure conversion created candidates

    store = server.StableNodeStore()
    async def exercise() -> None:
        resolved, display, failures, last_error = await server._resolve_selector_candidates(
            page=None,
            selector_candidates=candidates,
            store=store,
            timeout_ms=200,
            retries=3,
        )

        assert resolved is not None
        assert resolved.locator is fake_locator
        # Display string should include both css and stable identifier information.
        assert display == json.dumps({"css": "#login-btn", "stable_id": "stable-123"}, sort_keys=True)
        assert failures == []
        assert last_error is None
        assert calls["count"] == 3  # two retries before succeeding

    asyncio.run(exercise())
