"""Lightweight browser automation adapter used by the automation service.

The real browser-use project exposes a fairly feature rich abstraction layer
around Playwright.  For the purposes of this kata we provide a much smaller but
compatible surface that the higher level automation service can depend on.  The
adapter transparently falls back to a deterministic in-memory implementation
when Playwright is not available which keeps the test-suite fast and hermetic.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

try:  # pragma: no cover - the real Playwright dependency is optional in tests
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright

    PLAYWRIGHT_AVAILABLE = True
except Exception:  # pragma: no cover - executed when Playwright is unavailable
    Browser = BrowserContext = Page = None  # type: ignore[assignment]
    async_playwright = None  # type: ignore[assignment]
    PLAYWRIGHT_AVAILABLE = False


_PLACEHOLDER_IMAGE = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@dataclass(slots=True)
class AdapterResult:
    """Small helper structure describing the outcome of an adapter operation."""

    success: bool
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        payload = dict(self.details)
        payload.setdefault("success", self.success)
        return payload


class BrowserUseAdapter:
    """Subset of the browser-use adapter API tailored for the automation service."""

    def __init__(self) -> None:
        self.playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._initialized = False
        self._headless = True
        # Placeholder state used when Playwright is not available
        self._placeholder_url = "about:blank"
        self._placeholder_html = "<html><body><h1>Placeholder</h1></body></html>"
        self._placeholder_storage: Dict[str, Any] = {}

    async def initialize(self, *, headless: bool = True) -> bool:
        """Initialise the underlying Playwright browser if available."""

        if self._initialized:
            return True

        self._headless = headless

        if not PLAYWRIGHT_AVAILABLE:  # pragma: no cover - exercised in CI
            self._initialized = True
            log.info("Playwright not available - using placeholder adapter")
            return True

        try:
            self.playwright = await async_playwright().start()
            chromium = self.playwright.chromium

            cdp_endpoint = os.getenv("CDP_URL")
            if cdp_endpoint:
                try:
                    self.browser = await chromium.connect_over_cdp(cdp_endpoint)
                except Exception as exc:  # pragma: no cover - requires CDP target
                    log.warning("Failed to connect over CDP (%s), launching instead", exc)
                    self.browser = await chromium.launch(headless=headless)
            else:
                self.browser = await chromium.launch(headless=headless)

            self.context = await self.browser.new_context()
            self.page = await self.context.new_page()
            self._initialized = True
            return True
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Browser initialisation failed: %s", exc)
            await self.close()
            return False

    async def close(self) -> None:
        """Close all Playwright objects and reset placeholder state."""

        try:
            if PLAYWRIGHT_AVAILABLE and self.context:
                await self.context.close()
            if PLAYWRIGHT_AVAILABLE and self.browser:
                await self.browser.close()
            if PLAYWRIGHT_AVAILABLE and self.playwright:
                await self.playwright.stop()
        finally:
            self.playwright = None
            self.browser = None
            self.context = None
            self.page = None
            self._initialized = False
            self._placeholder_url = "about:blank"
            self._placeholder_html = "<html><body><h1>Placeholder</h1></body></html>"
            self._placeholder_storage.clear()

    async def _ensure_page(self) -> Page | None:
        if not self._initialized:
            await self.initialize(headless=self._headless)
        return self.page

    async def navigate(self, url: str, *, wait_until: str = "load", timeout: int = 30000) -> AdapterResult:
        """Navigate the page to the supplied URL."""

        page = await self._ensure_page()
        if not PLAYWRIGHT_AVAILABLE or not page:
            self._placeholder_url = url
            self._placeholder_html = f"<html><body><p>Navigated to {url}</p></body></html>"
            return AdapterResult(True, {"url": url, "message": f"Navigated to {url}"})

        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout)
            self._placeholder_url = url
            return AdapterResult(True, {"url": url, "message": "Navigation successful"})
        except Exception as exc:  # pragma: no cover - requires real browser failure
            log.exception("Navigation failure: %s", exc)
            return AdapterResult(False, {"url": url, "error": str(exc)})

    async def click(self, selector: str, *, button: str = "left", click_count: int = 1, delay_ms: int | None = None) -> AdapterResult:
        page = await self._ensure_page()
        if not PLAYWRIGHT_AVAILABLE or not page:
            return AdapterResult(True, {"selector": selector, "message": f"Clicked {selector}"})

        try:
            await page.click(selector, button=button, click_count=click_count, delay=delay_ms)
            return AdapterResult(True, {"selector": selector})
        except Exception as exc:  # pragma: no cover
            log.exception("Click failed: %s", exc)
            return AdapterResult(False, {"selector": selector, "error": str(exc)})

    async def hover(self, selector: str, *, timeout: int = 10000) -> AdapterResult:
        page = await self._ensure_page()
        if not PLAYWRIGHT_AVAILABLE or not page:
            return AdapterResult(True, {"selector": selector, "message": f"Hovered {selector}"})

        try:
            await page.hover(selector, timeout=timeout)
            return AdapterResult(True, {"selector": selector})
        except Exception as exc:  # pragma: no cover
            log.exception("Hover failed: %s", exc)
            return AdapterResult(False, {"selector": selector, "error": str(exc)})

    async def fill(self, selector: str, text: str, *, clear: bool = False, delay_ms: int | None = None) -> AdapterResult:
        page = await self._ensure_page()
        if not PLAYWRIGHT_AVAILABLE or not page:
            return AdapterResult(True, {"selector": selector, "text": text})

        try:
            locator = page.locator(selector)
            if clear:
                await locator.fill("", timeout=5000)
            await locator.type(text, delay=delay_ms)
            return AdapterResult(True, {"selector": selector, "text": text})
        except Exception as exc:  # pragma: no cover
            log.exception("Fill failed: %s", exc)
            return AdapterResult(False, {"selector": selector, "error": str(exc)})

    async def select_option(self, selector: str, value_or_label: str) -> AdapterResult:
        page = await self._ensure_page()
        if not PLAYWRIGHT_AVAILABLE or not page:
            return AdapterResult(True, {"selector": selector, "value": value_or_label})

        try:
            locator = page.locator(selector)
            await locator.select_option(value_or_label)
            return AdapterResult(True, {"selector": selector, "value": value_or_label})
        except Exception as exc:  # pragma: no cover
            log.exception("Select option failed: %s", exc)
            return AdapterResult(False, {"selector": selector, "error": str(exc)})

    async def press_key(self, key: str) -> AdapterResult:
        page = await self._ensure_page()
        if not PLAYWRIGHT_AVAILABLE or not page:
            return AdapterResult(True, {"key": key})

        try:
            await page.keyboard.press(key)
            return AdapterResult(True, {"key": key})
        except Exception as exc:  # pragma: no cover
            log.exception("Press key failed: %s", exc)
            return AdapterResult(False, {"key": key, "error": str(exc)})

    async def wait_for_selector(self, selector: str, *, state: str = "visible", timeout: int = 5000) -> AdapterResult:
        page = await self._ensure_page()
        if not PLAYWRIGHT_AVAILABLE or not page:
            return AdapterResult(True, {"selector": selector, "message": "Waited"})

        try:
            await page.wait_for_selector(selector, state=state, timeout=timeout)
            return AdapterResult(True, {"selector": selector, "state": state})
        except Exception as exc:  # pragma: no cover
            log.exception("Wait for selector failed: %s", exc)
            return AdapterResult(False, {"selector": selector, "error": str(exc)})

    async def evaluate(self, script: str, *, timeout: int = 5000) -> Any:
        page = await self._ensure_page()
        if not PLAYWRIGHT_AVAILABLE or not page:
            # Store last evaluation to make debugging easier in tests
            self._placeholder_storage["last_eval"] = script
            return None

        try:
            return await asyncio.wait_for(page.evaluate(script), timeout=timeout / 1000)
        except Exception as exc:  # pragma: no cover
            log.exception("Evaluation failed: %s", exc)
            return None

    async def scroll_by(self, x: int = 0, y: int = 0) -> AdapterResult:
        page = await self._ensure_page()
        if not PLAYWRIGHT_AVAILABLE or not page:
            return AdapterResult(True, {"x": x, "y": y})

        try:
            await page.mouse.wheel(x, y)
            return AdapterResult(True, {"x": x, "y": y})
        except Exception as exc:  # pragma: no cover
            log.exception("Scroll failed: %s", exc)
            return AdapterResult(False, {"x": x, "y": y, "error": str(exc)})

    async def screenshot(self, *, full_page: bool = False) -> bytes:
        page = await self._ensure_page()
        if not PLAYWRIGHT_AVAILABLE or not page:
            return _PLACEHOLDER_IMAGE

        try:
            return await page.screenshot(type="png", full_page=full_page)
        except Exception as exc:  # pragma: no cover
            log.exception("Screenshot failed: %s", exc)
            return _PLACEHOLDER_IMAGE

    async def get_page_content(self) -> str:
        page = await self._ensure_page()
        if not PLAYWRIGHT_AVAILABLE or not page:
            return self._placeholder_html

        try:
            html = await page.content()
            self._placeholder_html = html
            return html
        except Exception as exc:  # pragma: no cover
            log.exception("Fetching page content failed: %s", exc)
            return self._placeholder_html

    async def get_url(self) -> str:
        page = await self._ensure_page()
        if not PLAYWRIGHT_AVAILABLE or not page:
            return self._placeholder_url

        try:
            self._placeholder_url = page.url
            return page.url
        except Exception as exc:  # pragma: no cover
            log.exception("Failed to retrieve URL: %s", exc)
            return self._placeholder_url

    async def extract(self, selector: str, *, attr: str = "text") -> AdapterResult:
        page = await self._ensure_page()
        if not PLAYWRIGHT_AVAILABLE or not page:
            value = f"placeholder:{attr}:{selector}"
            return AdapterResult(True, {"selector": selector, "attr": attr, "value": value})

        try:
            locator = page.locator(selector)
            if attr == "text":
                value = await locator.inner_text()
            elif attr == "value":
                value = await locator.input_value()
            elif attr == "href":
                value = await locator.get_attribute("href")
            elif attr == "html":
                value = await locator.inner_html()
            else:
                value = await locator.get_attribute(attr)
            return AdapterResult(True, {"selector": selector, "attr": attr, "value": value})
        except Exception as exc:  # pragma: no cover
            log.exception("Extract failed: %s", exc)
            return AdapterResult(False, {"selector": selector, "attr": attr, "error": str(exc)})

    async def is_healthy(self) -> bool:
        if not self._initialized:
            return False

        if not PLAYWRIGHT_AVAILABLE:
            return True

        page = await self._ensure_page()
        if not page:  # pragma: no cover - requires browser teardown
            return False

        try:
            state = await asyncio.wait_for(page.evaluate("() => document.readyState"), timeout=2.0)
            return state in {"complete", "interactive", "loading"}
        except Exception:  # pragma: no cover
            return False


async def get_browser_adapter() -> BrowserUseAdapter:
    """Compatibility helper returning a global adapter instance."""

    if not hasattr(get_browser_adapter, "_instance"):
        get_browser_adapter._instance = BrowserUseAdapter()  # type: ignore[attr-defined]
        await get_browser_adapter._instance.initialize()
    return get_browser_adapter._instance  # type: ignore[attr-defined]


async def close_browser_adapter() -> None:
    if hasattr(get_browser_adapter, "_instance"):
        adapter: BrowserUseAdapter = get_browser_adapter._instance  # type: ignore[attr-defined]
        await adapter.close()
        delattr(get_browser_adapter, "_instance")
