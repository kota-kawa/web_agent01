import asyncio
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from automation.service import AutomationService
from vnc.browser_use_adapter import AdapterResult


class DummyAdapter:
    def __init__(self) -> None:
        self.actions = []
        self._html = "<html><body>initial</body></html>"
        self._url = "about:blank"
        self.fail_click_selector: str | None = None

    async def initialize(self, headless: bool = True) -> bool:  # pragma: no cover - trivial
        return True

    async def navigate(self, url: str, wait_until: str = "load", timeout: int = 30000) -> AdapterResult:
        self.actions.append(("navigate", url))
        self._url = url
        self._html = f"<html><body>{url}</body></html>"
        return AdapterResult(True, {"url": url, "wait_until": wait_until})

    async def click(self, selector: str, **kwargs) -> AdapterResult:
        self.actions.append(("click", selector))
        if self.fail_click_selector and selector == self.fail_click_selector:
            return AdapterResult(False, {"selector": selector, "error": "forced"})
        return AdapterResult(True, {"selector": selector})

    async def hover(self, selector: str, **kwargs) -> AdapterResult:  # pragma: no cover - not used
        self.actions.append(("hover", selector))
        return AdapterResult(True, {"selector": selector})

    async def fill(self, selector: str, text: str, **kwargs) -> AdapterResult:
        self.actions.append(("fill", selector, text))
        return AdapterResult(True, {"selector": selector, "text": text})

    async def select_option(self, selector: str, value_or_label: str) -> AdapterResult:  # pragma: no cover - not used
        self.actions.append(("select", selector, value_or_label))
        return AdapterResult(True, {"selector": selector, "value": value_or_label})

    async def press_key(self, key: str) -> AdapterResult:
        self.actions.append(("press", key))
        return AdapterResult(True, {"key": key})

    async def wait_for_selector(self, selector: str, state: str = "visible", timeout: int = 5000) -> AdapterResult:
        self.actions.append(("wait_for_selector", selector, state))
        return AdapterResult(True, {"selector": selector, "state": state})

    async def evaluate(self, script: str, timeout: int = 5000) -> bool | None:
        self.actions.append(("evaluate", script))
        # Scroll to text helper returns True to simulate success
        if "scrollIntoView" in script:
            return True
        return None

    async def scroll_by(self, x: int = 0, y: int = 0) -> AdapterResult:  # pragma: no cover - not used
        self.actions.append(("scroll", x, y))
        return AdapterResult(True, {"x": x, "y": y})

    async def screenshot(self, full_page: bool = False) -> bytes:
        self.actions.append(("screenshot", full_page))
        return b"png"

    async def get_page_content(self) -> str:
        return self._html

    async def get_url(self) -> str:
        return self._url

    async def extract(self, selector: str, attr: str = "text") -> AdapterResult:
        self.actions.append(("extract", selector, attr))
        return AdapterResult(True, {"selector": selector, "attr": attr, "value": f"value:{attr}"})

    async def is_healthy(self) -> bool:  # pragma: no cover - used via server tests
        return True


@pytest.mark.asyncio
async def test_execute_plan_successful_run():
    adapter = DummyAdapter()
    service = AutomationService(adapter=adapter)
    payload = {
        "run_id": "run-test",
        "plan": {
            "actions": [
                {"type": "navigate", "url": "https://example.com"},
                {"type": "click", "selector": {"css": "#buy"}},
                {
                    "type": "type",
                    "selector": {"css": "input[name=search]"},
                    "text": "laptop",
                    "press_enter": True,
                },
                {"type": "wait", "for": {"timeout_ms": 100}},
                {"type": "extract", "selector": {"css": ".title"}, "attr": "text"},
            ]
        },
    }

    summary = await service.execute_plan_async(payload)

    assert summary.success is True
    assert summary.run_id == "run-test"
    assert summary.url == "https://example.com"
    assert service.get_extracted()[-1] == "value:text"
    # The click action should have triggered an Enter press from the type action
    assert any(action[0] == "press" for action in adapter.actions)
    # Catalog entries should include the selectors seen during the run
    catalog = service.get_catalog()
    selectors = {entry["selector"] for entry in catalog["full"]}
    assert "#buy" in selectors


@pytest.mark.asyncio
async def test_execute_plan_reports_failures():
    adapter = DummyAdapter()
    adapter.fail_click_selector = "#fail"
    service = AutomationService(adapter=adapter)
    payload = {
        "run_id": "run-failure",
        "plan": {
            "actions": [
                {"type": "navigate", "url": "https://example.com"},
                {"type": "click", "selector": {"css": "#fail"}},
            ]
        },
    }

    summary = await service.execute_plan_async(payload)

    assert summary.success is False
    assert summary.error is not None
    assert "forced" in summary.error["message"].lower()
    assert any("WARNING:auto" in warning for warning in summary.warnings)
