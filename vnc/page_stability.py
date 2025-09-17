"""Utilities to stabilise dynamic pages before interactions."""

from __future__ import annotations

import asyncio
import logging
from typing import List

from playwright.async_api import Page

log = logging.getLogger(__name__)

DEFAULT_STABILIZE_TIMEOUT = 2_000

_LOADING_SELECTORS = [
    ".loading, .spinner, .loader",
    "[data-testid*='loading'], [data-testid*='spinner']",
    ".fa-spinner, .fa-circle-notch, .fa-refresh",
    "[role='status'][aria-live]",
    ".MuiCircularProgress-root, .ant-spin",
]


async def wait_dom_idle(page: Page, timeout_ms: int = DEFAULT_STABILIZE_TIMEOUT) -> None:
    """Wait until DOM mutations have been idle for a short threshold."""

    script = """
        (timeoutMs) => new Promise(resolve => {
            const threshold = 300;
            let last = Date.now();
            const ob = new MutationObserver(() => (last = Date.now()));
            ob.observe(document, {subtree: true, childList: true, attributes: true});
            const start = Date.now();
            (function check() {
                if (Date.now() - last > threshold) {
                    ob.disconnect();
                    resolve(true);
                    return;
                }
                if (Date.now() - start > timeoutMs) {
                    ob.disconnect();
                    resolve(false);
                    return;
                }
                setTimeout(check, 50);
            })();
        })
    """
    try:
        await page.evaluate(script, timeout_ms)
    except Exception:
        await page.wait_for_timeout(100)


async def wait_for_loading_indicators(page: Page, timeout: int = 3_000) -> None:
    """Wait for common loading indicators to disappear."""

    for selector in _LOADING_SELECTORS:
        try:
            await page.wait_for_selector(selector, state="hidden", timeout=timeout)
        except Exception:
            continue


async def stabilize_page(page: Page, timeout: int = DEFAULT_STABILIZE_TIMEOUT) -> None:
    """Best-effort attempt to allow SPA style pages to finish rendering."""

    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
        await wait_dom_idle(page, timeout_ms=timeout)
        await wait_for_loading_indicators(page, timeout=timeout)
    except Exception:
        await page.wait_for_timeout(min(500, max(50, timeout // 2)))


async def wait_for_page_ready(page: Page, timeout: int = 3_000) -> List[str]:
    """Wait for common structural elements to be ready after navigation."""

    warnings: List[str] = []
    common_selectors = [
        "body",
        "main, [role=main], #main, .main",
        "nav, [role=navigation], #nav, .nav",
        "header, [role=banner], #header, .header",
        "footer, [role=contentinfo], #footer, .footer",
    ]

    for selector in common_selectors:
        try:
            await page.wait_for_selector(selector, state="visible", timeout=timeout)
            break
        except Exception:
            continue

    await stabilize_page(page, timeout=timeout)
    return warnings


async def safe_get_page_content(
    page: Page,
    *,
    max_retries: int = 3,
    delay_ms: int = 500,
    stabilization_timeout: int = DEFAULT_STABILIZE_TIMEOUT,
) -> str:
    """Safely retrieve page content while handling transient navigation errors."""

    for attempt in range(max_retries):
        try:
            await stabilize_page(page, timeout=stabilization_timeout)
            return await page.content()
        except Exception as exc:
            error_str = str(exc).lower()
            if "navigating and changing" in error_str or "page is navigating" in error_str:
                log.warning(
                    "Page content retrieval attempt %d failed due to navigation: %s",
                    attempt + 1,
                    exc,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay_ms / 1000)
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=5_000)
                        await page.wait_for_load_state("networkidle", timeout=3_000)
                    except Exception:
                        pass
                    continue
                log.warning("Final attempt to get page content failed due to navigation: %s", exc)
                return ""
            else:
                log.warning("Page content retrieval failed: %s", exc)
                return ""
    return ""

