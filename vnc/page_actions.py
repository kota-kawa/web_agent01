"""Shared page-level interactions that do not rely on specific locators."""

from __future__ import annotations

from typing import Any, Dict

from playwright.async_api import Page


SCROLL_TO_TEXT_SCRIPT = """
    (needle) => {
        if (!needle) return {success: false, reason: 'empty'};
        const target = String(needle).trim();
        if (!target) return {success: false, reason: 'empty'};
        const lower = target.toLowerCase();
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
        while (walker.nextNode()) {
            const el = walker.currentNode;
            if (!(el instanceof Element)) continue;
            const style = window.getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none') continue;
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const content = (el.innerText || el.textContent || '').trim();
            if (!content) continue;
            if (content.toLowerCase().includes(lower)) {
                el.scrollIntoView({behavior: 'instant', block: 'center'});
                return {
                    success: true,
                    text: target,
                    snippet: content.slice(0, 160),
                };
            }
        }
        return {success: false, reason: 'not_found'};
    }
"""

CLICK_BLANK_AREA_SCRIPT = """
    (() => {
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;

        function isBlankPoint(x, y) {
            const element = document.elementFromPoint(x, y);
            if (!element) return true;

            if (element.tagName === 'BODY' || element.tagName === 'HTML') {
                return true;
            }

            const style = window.getComputedStyle(element);
            const interactiveTags = /^(A|BUTTON|INPUT|SELECT|TEXTAREA)$/;
            const isInteractive = interactiveTags.test(element.tagName) ||
                element.hasAttribute('onclick') ||
                element.hasAttribute('role') ||
                style.cursor === 'pointer';

            if (!isInteractive && (!element.textContent || element.textContent.trim() === '')) {
                return true;
            }

            return false;
        }

        const candidates = [
            [50, 50],
            [viewportWidth - 50, 50],
            [50, viewportHeight - 50],
            [viewportWidth - 50, viewportHeight - 50],
            [viewportWidth / 2, 50],
            [viewportWidth / 2, viewportHeight - 50],
            [50, viewportHeight / 2],
            [viewportWidth - 50, viewportHeight / 2],
            [viewportWidth / 2, viewportHeight / 2],
        ];

        for (const [x, y] of candidates) {
            if (isBlankPoint(x, y)) {
                const target = document.elementFromPoint(x, y);
                if (!target) {
                    continue;
                }
                const event = new MouseEvent('click', {
                    view: window,
                    bubbles: true,
                    cancelable: true,
                    clientX: x,
                    clientY: y,
                });
                target.dispatchEvent(event);
                return {success: true, x, y, fallback: false};
            }
        }

        const fallbackTarget = document.elementFromPoint(50, 50);
        if (fallbackTarget) {
            const fallbackEvent = new MouseEvent('click', {
                view: window,
                bubbles: true,
                cancelable: true,
                clientX: 50,
                clientY: 50,
            });
            fallbackTarget.dispatchEvent(fallbackEvent);
        }
        return {success: true, x: 50, y: 50, fallback: true};
    })()
"""

CLOSE_POPUP_SCRIPT = """
    (() => {
        const popupSelectors = [
            '[role="dialog"]',
            '[role="alertdialog"]',
            '.modal',
            '.popup',
            '.overlay',
            '.dialog',
            '.modal-backdrop',
            '.modal-overlay',
            '[data-testid*="modal"]',
            '[data-testid*="popup"]',
            '[data-testid*="dialog"]',
            '[class*="modal"]',
            '[class*="popup"]',
            '[class*="overlay"]',
            '[class*="dialog"]'
        ];

        const foundPopups = [];
        for (const selector of popupSelectors) {
            const elements = document.querySelectorAll(selector);
            for (const el of elements) {
                const style = window.getComputedStyle(el);
                if (
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    style.opacity !== '0' &&
                    el.offsetWidth > 0 &&
                    el.offsetHeight > 0
                ) {
                    foundPopups.push(el);
                }
            }
        }

        if (!foundPopups.length) {
            return {found: false, clicked: false, message: 'No popups detected'};
        }

        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;

        function isOutsidePopups(x, y) {
            for (const popup of foundPopups) {
                const rect = popup.getBoundingClientRect();
                if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
                    return false;
                }
            }
            return true;
        }

        const candidates = [
            [50, 50],
            [viewportWidth - 50, 50],
            [50, viewportHeight - 50],
            [viewportWidth - 50, viewportHeight - 50],
            [viewportWidth / 2, 50],
            [viewportWidth / 2, viewportHeight - 50]
        ];

        for (const [x, y] of candidates) {
            if (isOutsidePopups(x, y)) {
                const target = document.elementFromPoint(x, y);
                if (!target) {
                    continue;
                }
                const event = new MouseEvent('click', {
                    view: window,
                    bubbles: true,
                    cancelable: true,
                    clientX: x,
                    clientY: y,
                });
                target.dispatchEvent(event);
                return {found: true, clicked: true, x, y, popupCount: foundPopups.length};
            }
        }

        return {found: true, clicked: false, message: 'Could not find safe click area'};
    })()
"""


async def scroll_to_text(page: Page, text: str) -> Dict[str, Any]:
    result = await page.evaluate(SCROLL_TO_TEXT_SCRIPT, text)
    if not isinstance(result, dict):
        return {"success": False, "reason": "unknown"}
    return result


async def click_blank_area(page: Page) -> Dict[str, Any]:
    result = await page.evaluate(CLICK_BLANK_AREA_SCRIPT)
    if not isinstance(result, dict):
        return {"success": False}
    return result


async def close_popup(page: Page) -> Dict[str, Any]:
    result = await page.evaluate(CLOSE_POPUP_SCRIPT)
    if not isinstance(result, dict):
        return {"found": False, "clicked": False}
    return result


async def eval_js(page: Page, script: str) -> Any:
    return await page.evaluate(script)

