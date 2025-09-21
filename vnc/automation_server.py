# vnc/automation_server.py
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from typing import Dict, List, Optional


import httpx
from flask import Flask, Response, jsonify, request
from jsonschema import Draft7Validator, ValidationError
from playwright.async_api import Error as PwError, Page, async_playwright

from vnc.locator_utils import SmartLocator  # 同ディレクトリ

# -------------------------------------------------- 基本設定
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("auto")

ACTION_TIMEOUT = int(os.getenv("ACTION_TIMEOUT", "10000"))  # ms  個別アクション猶予
MAX_RETRIES = 3
LOCATOR_RETRIES = int(os.getenv("LOCATOR_RETRIES", "3"))
CDP_URL = "http://localhost:9222"
DEFAULT_URL = os.getenv("START_URL", "https://yahoo.co.jp")
SPA_STABILIZE_TIMEOUT = int(
    os.getenv("SPA_STABILIZE_TIMEOUT", "2000")
)  # ms  SPA描画安定待ち

# Event listener tracker script will be injected on every page load
_WATCHER_SCRIPT = None

# -------------------------------------------------- DSL スキーマ
_ACTIONS = [
    "navigate",
    "go_to_url",
    "search_google",
    "click",
    "click_text",
    "click_element_by_index",
    "type",
    "input_text",
    "wait",
    "scroll",
    "scroll_to_text",
    "go_back",
    "go_forward",
    "hover",
    "select_option",
    "select_dropdown_option",
    "press_key",
    "send_keys",
    "wait_for_selector",
    "extract_text",
    "extract_structured_data",
    "eval_js",
    "switch_tab",
    "close_tab",
    "upload_file_to_element",
    "get_dropdown_options",
    "done",
]
payload_schema = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": _ACTIONS},
                    "target": {"type": "string"},
                    "value": {"type": "string"},
                    "text": {"type": "string"},
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "ms": {"type": "integer", "minimum": 0},
                    "amount": {"type": "integer"},
                    "direction": {"type": "string", "enum": ["up", "down"]},
                    "key": {"type": "string"},
                    "keys": {"type": "string"},
                    "retry": {"type": "integer", "minimum": 1},
                    "attr": {"type": "string"},
                    "index": {"type": "integer", "minimum": 0},
                    "tab_id": {"type": "string"},
                    "down": {"type": "boolean"},
                    "num_pages": {"type": "number"},
                    "frame_element_index": {"type": "integer", "minimum": 0},
                    "while_holding_ctrl": {"type": "boolean"},
                    "new_tab": {"type": "boolean"},
                },
                "required": ["action"],
                "additionalProperties": True,  # ★ 不明キーは許可
            },
        },
        "complete": {"type": "boolean"},  # ★ 任意
    },
    "required": ["actions"],
    "additionalProperties": True,  # ★ ここも許可
}
validator = Draft7Validator(payload_schema)


def _validate(data: Dict) -> None:
    errs = sorted(validator.iter_errors(data), key=lambda e: e.path)
    if errs:
        raise ValidationError("; ".join(err.msg for err in errs))


# -------------------------------------------------- Playwright 管理
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

PW = None
BROWSER = None
CONTEXT = None
CURRENT_PAGE: Optional[Page] = None
CURRENT_TAB_ID: Optional[str] = None
TAB_REGISTRY: Dict[str, Page] = {}
_TAB_COUNTER = 0

SELECTOR_CACHE: Dict[int, Dict[str, object]] = {}
EXTRACTED_TEXTS: List[str] = []
EVAL_RESULTS: List[str] = []
WARNINGS: List[str] = []
LAST_SUMMARY: Dict[str, object] | None = None


def _run(coro):
    return LOOP.run_until_complete(coro)


def _assign_tab_id(page: Page) -> str:
    global _TAB_COUNTER, TAB_REGISTRY
    tab_id = getattr(page, "_tab_id", None)
    if not tab_id:
        _TAB_COUNTER += 1
        tab_id = f"{_TAB_COUNTER:04d}"
        setattr(page, "_tab_id", tab_id)
    TAB_REGISTRY[tab_id] = page
    return tab_id


def _set_current_page(page: Page) -> None:
    global CURRENT_PAGE, CURRENT_TAB_ID
    CURRENT_PAGE = page
    CURRENT_TAB_ID = _assign_tab_id(page)
    _sync_open_tabs()


def _get_current_page() -> Page:
    if CURRENT_PAGE is None:
        raise RuntimeError("browser not initialized")
    return CURRENT_PAGE


def _css_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', '\\"')
    )


def _uniq(seq):
    seen = set()
    out = []
    for item in seq:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


async def _locator_for_index(index: int, page: Page):
    info = SELECTOR_CACHE.get(index)
    if not info:
        return None
    selectors = info.get("selectors") or []
    for sel in selectors:
        try:
            loc = await SmartLocator(page, sel).locate()
            if loc:
                return loc
        except Exception as exc:  # pragma: no cover - defensive logging
            log.debug("locator candidate %s failed: %s", sel, exc)
    return None


async def _page_for_navigation(new_tab: bool | None = None) -> Page:
    if new_tab and CONTEXT is not None:
        new_page = await CONTEXT.new_page()
        _set_current_page(new_page)
        await new_page.bring_to_front()
        return new_page
    page = _get_current_page()
    await page.bring_to_front()
    return page


def _sync_open_tabs() -> None:
    if CONTEXT is None:
        return
    for page in CONTEXT.pages:
        if page.is_closed():
            continue
        _assign_tab_id(page)
    to_remove = [tid for tid, pg in TAB_REGISTRY.items() if pg.is_closed()]
    for tid in to_remove:
        TAB_REGISTRY.pop(tid, None)


JS_SCROLL_TO_TEXT = """
(text) => {
    if (!text) {
        return false;
    }
    const needle = String(text).trim().toLowerCase();
    if (!needle) {
        return false;
    }
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    while (walker.nextNode()) {
        const el = walker.currentNode;
        if (!(el instanceof HTMLElement)) {
            continue;
        }
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') {
            continue;
        }
        const textContent = (el.innerText || '').trim();
        if (!textContent) {
            continue;
        }
        if (textContent.toLowerCase().includes(needle)) {
            el.scrollIntoView({behavior: 'instant', block: 'center'});
            return true;
        }
    }
    return false;
}
"""


JS_GET_OPTIONS = """
(el) => {
    if (!el) {
        return [];
    }
    const options = [];
    const candidates = el.tagName === 'SELECT'
        ? Array.from(el.options || [])
        : Array.from(el.querySelectorAll('option'));
    for (const opt of candidates) {
        options.push({
            text: (opt.textContent || '').trim(),
            value: opt.value || '',
            selected: Boolean(opt.selected),
        });
    }
    return options;
}
"""


JS_COLLECT_ELEMENTS = """
() => {
    const interactiveTags = new Set(['a', 'button', 'summary', 'textarea', 'select', 'label', 'details', 'svg', 'div', 'span', 'input']);
    const interactiveRoles = new Set([
        'button',
        'link',
        'checkbox',
        'radio',
        'menuitem',
        'menuitemcheckbox',
        'menuitemradio',
        'tab',
        'switch',
        'textbox',
        'combobox',
        'listbox',
        'option',
        'spinbutton',
        'slider',
        'scrollbar',
        'treeitem',
    ]);
    const importantAttrs = [
        'id',
        'name',
        'type',
        'value',
        'placeholder',
        'aria-label',
        'aria-labelledby',
        'aria-describedby',
        'aria-controls',
        'aria-expanded',
        'aria-selected',
        'aria-checked',
        'aria-haspopup',
        'role',
        'href',
        'title',
        'tabindex',
        'data-testid',
        'data-test',
        'data-cy',
        'data-qa',
        'data-automation-id',
        'autocomplete',
        'accept',
        'pattern',
        'min',
        'max',
        'step',
    ];

    function isVisible(el) {
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
            return false;
        }
        if (el.closest('[aria-hidden="true"]')) {
            return false;
        }
        const rect = el.getBoundingClientRect();
        if (rect.width < 1 && rect.height < 1) {
            return false;
        }
        return true;
    }

    function isInteractive(el) {
        const tag = el.tagName.toLowerCase();
        if (tag === 'input') {
            const inputType = (el.getAttribute('type') || 'text').toLowerCase();
            if (inputType === 'hidden') {
                return false;
            }
            return !el.disabled;
        }
        if (interactiveTags.has(tag)) {
            if (tag === 'a') {
                return Boolean(el.getAttribute('href')) || interactiveRoles.has((el.getAttribute('role') || '').toLowerCase());
            }
            return true;
        }
        const role = (el.getAttribute('role') || '').toLowerCase();
        if (interactiveRoles.has(role)) {
            return true;
        }
        if (el.hasAttribute('onclick') || el.tabIndex >= 0 || el.isContentEditable) {
            return true;
        }
        return false;
    }

    function describe(el) {
        if (!el) return '';
        const tag = el.tagName.toLowerCase();
        let desc = tag;
        const id = el.getAttribute('id');
        if (id) {
            desc += `#${id}`;
        }
        const className = (el.getAttribute('class') || '').trim();
        if (className) {
            desc += '.' + className.split(/\s+/).slice(0, 2).join('.');
        }
        return desc;
    }

    function computeXPath(el) {
        if (!el) return '';
        const parts = [];
        let current = el;
        while (current && current.nodeType === Node.ELEMENT_NODE && current !== document) {
            const tagName = current.tagName.toLowerCase();
            let index = 1;
            let sibling = current.previousElementSibling;
            while (sibling) {
                if (sibling.tagName.toLowerCase() === tagName) {
                    index += 1;
                }
                sibling = sibling.previousElementSibling;
            }
            parts.unshift(`${tagName}[${index}]`);
            current = current.parentElement;
        }
        return '/' + parts.join('/');
    }

    const results = [];
    const root = document.body || document.documentElement;
    if (!root) {
        return {title: document.title || '', url: location.href, elements: []};
    }

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let index = 1;
    while (walker.nextNode()) {
        const el = walker.currentNode;
        if (!(el instanceof HTMLElement)) {
            continue;
        }
        if (!isVisible(el)) {
            continue;
        }
        const interactive = isInteractive(el);
        const scrollable = el.scrollHeight > el.clientHeight + 4;
        if (!interactive && !scrollable) {
            continue;
        }

        const rect = el.getBoundingClientRect();
        const attrs = {};
        for (const name of importantAttrs) {
            const value = el.getAttribute(name);
            if (value) {
                attrs[name] = value;
            }
        }
        if (el.dataset && Object.keys(el.dataset).length) {
            attrs['data'] = {...el.dataset};
        }
        if (el.type && !attrs['type']) {
            attrs['type'] = el.type;
        }

        const rawText = (() => {
            if (el.tagName.toLowerCase() === 'input') {
                return el.value || '';
            }
            if (el.tagName.toLowerCase() === 'textarea') {
                return el.value || el.innerText || '';
            }
            return el.innerText || el.textContent || '';
        })();
        const text = rawText.replace(/\s+/g, ' ').trim();

        const ancestors = [];
        let parent = el.parentElement;
        while (parent && ancestors.length < 4) {
            ancestors.push(describe(parent));
            parent = parent.parentElement;
        }

        results.push({
            index: index,
            tag: el.tagName.toLowerCase(),
            role: (el.getAttribute('role') || '').toLowerCase(),
            text,
            attributes: attrs,
            rect: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
            },
            ancestors,
            isInteractive: interactive,
            isScrollable: scrollable,
            xpath: computeXPath(el),
        });
        index += 1;
        if (results.length >= 180) {
            break;
        }
    }

    return {
        title: document.title || '',
        url: location.href,
        elements: results,
    };
}
"""


def _compute_selectors(element: Dict[str, object]) -> List[str]:
    attrs = element.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}
    selectors: List[str] = []
    tag = (element.get("tag") or "*").strip() or "*"
    text = (element.get("text") or "").strip()
    role = (attrs.get("role") or element.get("role") or "").strip()

    data_block = attrs.get("data") if isinstance(attrs.get("data"), dict) else {}
    if isinstance(data_block, dict):
        for key, value in data_block.items():
            if value:
                selectors.append(f"css=[data-{key}='{_css_escape(str(value))}']")

    for data_attr in ("data-testid", "data-test", "data-cy", "data-qa", "data-automation-id"):
        value = attrs.get(data_attr)
        if value:
            selectors.append(f"css=[{data_attr}='{_css_escape(str(value))}']")

    if attrs.get("id"):
        selectors.append(f"css=#{_css_escape(str(attrs['id']))}")

    if attrs.get("name"):
        selectors.append(f"css={tag}[name='{_css_escape(str(attrs['name']))}']")

    if attrs.get("aria-label"):
        aria = str(attrs["aria-label"]).strip()
        selectors.append(f"css=[aria-label='{_css_escape(aria)}']")
        if role:
            selectors.append(f"role={role}[name='{_css_escape(aria)}']")

    if attrs.get("title"):
        selectors.append(f"css={tag}[title='{_css_escape(str(attrs['title']))}']")

    if attrs.get("placeholder"):
        selectors.append(f"css={tag}[placeholder='{_css_escape(str(attrs['placeholder']))}']")

    if attrs.get("value") and tag in {"button", "input", "option"}:
        selectors.append(f"css={tag}[value='{_css_escape(str(attrs['value']))}']")

    if attrs.get("href"):
        selectors.append(f"css={tag}[href='{_css_escape(str(attrs['href']))}']")

    if text:
        trimmed_text = text[:120]
        selectors.append(f"text={_css_escape(trimmed_text)}")
        if role:
            selectors.append(f"role={role}[name='{_css_escape(trimmed_text)}']")

    if element.get("xpath"):
        selectors.append(f"xpath={element['xpath']}")

    selectors.append(f"css={tag}")
    return _uniq(selectors)


def _format_summary_line(element: Dict[str, object], selectors: List[str]) -> str:
    idx = int(element.get("index", 0))
    tag = element.get("tag", "")
    text = (element.get("text") or "").strip()
    attrs = element.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}
    attr_keys = [
        "id",
        "name",
        "role",
        "type",
        "value",
        "placeholder",
        "aria-label",
        "href",
        "title",
    ]
    attr_parts = []
    for key in attr_keys:
        value = attrs.get(key)
        if value:
            attr_parts.append(f"{key}={value}")
    data_block = attrs.get("data") if isinstance(attrs.get("data"), dict) else {}
    if isinstance(data_block, dict):
        for key, value in data_block.items():
            if value and len(attr_parts) < 8:
                attr_parts.append(f"data-{key}={value}")

    rect = element.get("rect") or {}
    try:
        pos = f"({int(rect.get('x', 0))},{int(rect.get('y', 0))}) {int(rect.get('width', 0))}x{int(rect.get('height', 0))}"
    except Exception:
        pos = "(0,0)"

    ancestors = element.get("ancestors") or []
    if not isinstance(ancestors, list):
        ancestors = []
    parent_text = " > ".join(ancestors[:3])

    label = text[:80]
    if len(text) > 80:
        label += "…"

    line = f"[{idx:03d}] <{tag}>{' ' + label if label else ''}"
    if attr_parts:
        line += " | " + ", ".join(attr_parts[:8])
    if element.get("isScrollable"):
        line += " | scrollable"
    if parent_text:
        line += f" | parents: {parent_text}"
    line += f" | {pos}"
    if selectors:
        line += f" | selector hint: {selectors[0]}"
    return line


async def _build_dom_snapshot(limit: int = 140) -> Dict[str, object]:
    page = _get_current_page()
    raw = await page.evaluate(JS_COLLECT_ELEMENTS)
    elements = raw.get("elements") or []
    if not isinstance(elements, list):
        elements = []
    elements.sort(key=lambda e: ((e.get("rect") or {}).get("y", 0), (e.get("rect") or {}).get("x", 0)))
    trimmed = elements[:limit]

    selector_map: Dict[int, Dict[str, object]] = {}
    lines: List[str] = []
    for el in trimmed:
        try:
            idx = int(el.get("index", 0))
        except Exception:
            continue
        selectors = _compute_selectors(el)
        selector_map[idx] = {"selectors": selectors, "text": el.get("text")}
        lines.append(_format_summary_line(el, selectors))

    SELECTOR_CACHE.clear()
    SELECTOR_CACHE.update(selector_map)

    summary = {
        "title": raw.get("title") or "",
        "url": raw.get("url") or "",
        "elements": trimmed,
        "summary": lines,
    }

    global LAST_SUMMARY
    LAST_SUMMARY = summary
    return summary


async def _wait_cdp(t: int = 15) -> bool:
    deadline = time.time() + t
    async with httpx.AsyncClient(timeout=2) as c:
        while time.time() < deadline:
            try:
                await c.get(f"{CDP_URL}/json/version")
                return True
            except httpx.HTTPError:
                await asyncio.sleep(0.5)
    return False


async def _init_browser():
    global PW, BROWSER, CONTEXT
    if CURRENT_PAGE:
        return
    PW = await async_playwright().start()

    page: Page | None = None
    if await _wait_cdp():
        try:
            BROWSER = await PW.chromium.connect_over_cdp(CDP_URL)
            CONTEXT = (
                BROWSER.contexts[0] if BROWSER.contexts else await BROWSER.new_context()
            )
            page = CONTEXT.pages[0] if CONTEXT.pages else await CONTEXT.new_page()
        except PwError:
            log.warning("connect_over_cdp failed, falling back to launch")

    if page is None:
        BROWSER = await PW.chromium.launch(headless=True)
        CONTEXT = await BROWSER.new_context()
        page = await CONTEXT.new_page()

    _set_current_page(page)
    await page.bring_to_front()

    # Inject event listener tracking script on every navigation
    global _WATCHER_SCRIPT
    if _WATCHER_SCRIPT is None:
        path = os.path.join(os.path.dirname(__file__), "eventWatcher.js")
        with open(path, encoding="utf-8") as f:
            _WATCHER_SCRIPT = f.read()
    try:
        await page.add_init_script(_WATCHER_SCRIPT)
    except Exception as e:
        log.error("add_init_script failed: %s", e)

    if page.url in ("about:blank", "") or page.url == "chrome://newtab/":
        await page.goto(DEFAULT_URL, wait_until="load")
    log.info("browser ready")


# -------------------------------------------------- アクション実装
async def _prepare_element(loc):
    """Ensure the element is visible, enabled and ready for interaction."""
    await loc.first.wait_for(state="visible", timeout=ACTION_TIMEOUT)
    await loc.first.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT)
    await loc.first.wait_for(state="visible", timeout=ACTION_TIMEOUT)
    if not await loc.first.is_enabled():
        raise Exception("element not enabled")


async def _safe_click(l, force=False):
    try:
        await _prepare_element(l)
        await l.first.click(timeout=ACTION_TIMEOUT, force=force)
    except Exception as e:
        if not force:
            log.error("click retry with force due to: %s", e)
            await l.first.click(timeout=ACTION_TIMEOUT, force=True)
        else:
            raise


async def _safe_fill(l, val: str):
    try:
        await _prepare_element(l)
        await l.first.fill(val, timeout=ACTION_TIMEOUT)
    except Exception as e:
        log.error("fill retry due to: %s", e)
        await _safe_click(l)
        await l.first.fill(val, timeout=ACTION_TIMEOUT)


async def _safe_hover(l):
    await _prepare_element(l)
    await l.first.hover(timeout=ACTION_TIMEOUT)


async def _safe_select(l, val: str):
    await _prepare_element(l)
    await l.first.select_option(val, timeout=ACTION_TIMEOUT)


async def _safe_press(l, key: str):
    await _prepare_element(l)
    await l.first.press(key, timeout=ACTION_TIMEOUT)


async def _list_elements(limit: int = 50) -> List[Dict]:
    """Return list of clickable/input elements with basic info."""
    els = []
    page = _get_current_page()
    loc = page.locator("a,button,input,textarea,select")
    count = await loc.count()
    for i in range(min(count, limit)):
        el = loc.nth(i)
        try:
            if not await el.is_visible():
                continue
            tag = await el.evaluate("el => el.tagName.toLowerCase()")
            text = (await el.inner_text()).strip()[:50]
            id_attr = await el.get_attribute("id")
            cls = await el.get_attribute("class")
            xpath = await el.evaluate(
                """
                el => {
                    function xp(e){
                        if(e===document.body) return '/html/body';
                        let ix=0,s=e.previousSibling;
                        while(s){ if(s.nodeType===1 && s.tagName===e.tagName) ix++; s=s.previousSibling; }
                        return xp(e.parentNode)+'/'+e.tagName.toLowerCase()+'['+(ix+1)+']';
                    }
                    return xp(el);
                }
                """
            )
            els.append(
                {
                    "index": len(els),
                    "tag": tag,
                    "text": text,
                    "id": id_attr,
                    "class": cls,
                    "xpath": xpath,
                }
            )
        except Exception:
            continue
    return els


async def _wait_dom_idle(timeout_ms: int = SPA_STABILIZE_TIMEOUT):
    """Wait until DOM mutations stop for a short period."""
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
        page = _get_current_page()
        await page.evaluate(script, timeout_ms)
    except Exception:
        await _get_current_page().wait_for_timeout(100)


# SPA 安定化関数 ----------------------------------------
async def _stabilize_page():
    """SPA で DOM が書き換わるまで待機する共通ヘルパ."""
    try:
        # ネットワーク要求が終わるまで待機
        await _get_current_page().wait_for_load_state("networkidle", timeout=SPA_STABILIZE_TIMEOUT)
    except Exception:
        pass
    await _wait_dom_idle(SPA_STABILIZE_TIMEOUT)


async def _apply(act: Dict):
    page = _get_current_page()
    action = (act.get("action") or "").lower()
    tgt = act.get("target", "")
    if isinstance(tgt, list):
        tgt = " || ".join(str(s).strip() for s in tgt if s)
    val = act.get("value", "")
    ms = int(act.get("ms", 0))
    amt = int(act.get("amount", 400))
    dir_ = act.get("direction", "down")

    if action in {"navigate", "go_to_url"}:
        url = tgt or val or act.get("url")
        if not url:
            raise ValueError("navigate/go_to_url requires a target URL")
        dest = await _page_for_navigation(bool(act.get("new_tab")))
        await dest.goto(url, wait_until="load", timeout=ACTION_TIMEOUT)
        _set_current_page(dest)
        return

    if action == "search_google":
        query = act.get("query") or tgt or val
        if not query:
            raise ValueError("search_google requires query")
        url = f"https://www.google.com/search?q={query}&udm=14"
        dest = await _page_for_navigation(bool(act.get("new_tab")))
        await dest.goto(url, wait_until="load", timeout=ACTION_TIMEOUT)
        _set_current_page(dest)
        return

    if action == "go_back":
        await page.go_back(wait_until="load")
        return

    if action == "go_forward":
        await page.go_forward(wait_until="load")
        return

    if action == "wait":
        await page.wait_for_timeout(ms)
        return

    if action == "wait_for_selector":
        await page.wait_for_selector(tgt, state="visible", timeout=ms)
        return

    if action == "scroll":
        down_flag = act.get("down")
        if down_flag is None:
            down_flag = dir_.lower() != "up"
        amount = amt
        if act.get("num_pages") is not None:
            viewport = await page.evaluate("() => window.innerHeight || 800")
            amount = int(float(act.get("num_pages", 1)) * float(viewport))
        offset = amount if down_flag else -amount
        frame_idx = act.get("frame_element_index")
        if frame_idx:
            loc = await _locator_for_index(int(frame_idx), page)
            if loc:
                try:
                    await loc.first.evaluate("(el, delta) => { el.scrollBy(0, delta); return true; }", offset)
                    return
                except Exception as exc:
                    log.warning("scroll element by index failed: %s", exc)
        if tgt:
            try:
                await page.locator(tgt).evaluate("(el, delta) => { el.scrollBy(0, delta); return true; }", offset)
            except Exception:
                await page.evaluate("(delta) => window.scrollBy(0, delta)", offset)
        else:
            await page.evaluate("(delta) => window.scrollBy(0, delta)", offset)
        return

    if action == "scroll_to_text":
        text = act.get("text") or tgt or val
        if text:
            found = await page.evaluate(JS_SCROLL_TO_TEXT, text)
            if not found:
                WARNINGS.append(f"WARNING:auto:scroll_to_text not found: {text}")
        return

    if action == "switch_tab":
        tab_id = act.get("tab_id") or (tgt if tgt else None)
        if not tab_id:
            return
        _sync_open_tabs()
        tab = TAB_REGISTRY.get(tab_id)
        if tab and not tab.is_closed():
            _set_current_page(tab)
            await tab.bring_to_front()
        else:
            WARNINGS.append(f"WARNING:auto:tab not found: {tab_id}")
        return

    if action == "close_tab":
        tab_id = act.get("tab_id") or CURRENT_TAB_ID
        if not tab_id:
            return
        tab = TAB_REGISTRY.pop(tab_id, None)
        if tab and not tab.is_closed():
            await tab.close()
        if CURRENT_TAB_ID == tab_id:
            remaining = [pg for pg in TAB_REGISTRY.values() if not pg.is_closed()]
            if remaining:
                _set_current_page(remaining[-1])
                await _get_current_page().bring_to_front()
            elif CONTEXT is not None:
                new_page = await CONTEXT.new_page()
                _set_current_page(new_page)
                await new_page.bring_to_front()
        _sync_open_tabs()
        return

    if action == "send_keys":
        keys = act.get("keys") or act.get("key")
        if keys:
            if "+" in keys:
                await page.keyboard.press(keys)
            elif len(keys) == 1:
                await page.keyboard.type(keys)
            else:
                await page.keyboard.type(keys)
        return

    if action in {"click_element_by_index", "click_element"}:
        idx = int(act.get("index", 0))
        loc = await _locator_for_index(idx, page)
        if not loc:
            raise ValueError(f"no locator for index {idx}")
        if act.get("while_holding_ctrl"):
            await page.keyboard.down("Control")
            try:
                await _safe_click(loc)
            finally:
                await page.keyboard.up("Control")
        else:
            await _safe_click(loc)
        _sync_open_tabs()
        return

    if action == "input_text":
        idx = int(act.get("index", 0))
        text = act.get("text") if act.get("text") is not None else val
        clear_existing = act.get("clear_existing", True)
        if idx == 0:
            if clear_existing:
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Delete")
            await page.keyboard.type(text)
        else:
            loc = await _locator_for_index(idx, page)
            if not loc:
                raise ValueError(f"no locator for index {idx}")
            if clear_existing:
                await _safe_fill(loc, text)
            else:
                await _prepare_element(loc)
                await loc.first.type(text, timeout=ACTION_TIMEOUT)
        return

    if action == "upload_file_to_element":
        idx = int(act.get("index", 0))
        loc = await _locator_for_index(idx, page)
        if not loc:
            raise ValueError(f"no locator for index {idx}")
        await _prepare_element(loc)
        paths = act.get("path") or val
        files = paths if isinstance(paths, list) else [paths]
        await loc.first.set_input_files(files)
        return

    if action == "get_dropdown_options":
        idx = int(act.get("index", 0))
        loc = await _locator_for_index(idx, page)
        if not loc:
            raise ValueError(f"no locator for index {idx}")
        options = await loc.first.evaluate(JS_GET_OPTIONS)
        EXTRACTED_TEXTS.append(json.dumps(options, ensure_ascii=False))
        return

    if action == "select_dropdown_option":
        idx = int(act.get("index", 0))
        choice = act.get("text") or act.get("value") or val
        loc = await _locator_for_index(idx, page)
        if not loc:
            raise ValueError(f"no locator for index {idx}")
        await _prepare_element(loc)
        try:
            await loc.first.select_option(label=choice)
        except Exception:
            await loc.first.select_option(value=choice)
        return

    if action == "extract_structured_data":
        if act.get("index"):
            loc = await _locator_for_index(int(act["index"]), page)
            if loc:
                text = await loc.first.inner_text()
                EXTRACTED_TEXTS.append(text)
                return
        if tgt:
            loc = await SmartLocator(page, tgt).locate()
            if loc:
                text = await loc.first.inner_text()
                EXTRACTED_TEXTS.append(text)
                return
        body_text = await page.inner_text("body")
        EXTRACTED_TEXTS.append(body_text)
        return

    if action == "done":
        return

    if action == "eval_js":
        script = act.get("script") or val
        if script:
            try:
                result = await page.evaluate(script)
                EVAL_RESULTS.append(result)
            except Exception as e:
                log.error("eval_js error: %s", e)
        return

    # Fallback to legacy locator-based actions
    loc: Optional = None
    for _ in range(LOCATOR_RETRIES):
        if action == "click_text":
            loc = await SmartLocator(page, f"text={tgt}").locate()
        else:
            loc = await SmartLocator(page, tgt).locate()
        if loc is not None:
            break
        await _stabilize_page()

    if loc is None:
        msg = f"locator not found: {tgt}"
        log.warning(msg)
        WARNINGS.append(f"WARNING:auto:{msg}")
        return

    if action in ("click", "click_text"):
        await _safe_click(loc)
    elif action in ("type",):
        await _safe_fill(loc, val)
    elif action == "hover":
        await _safe_hover(loc)
    elif action == "select_option":
        await _safe_select(loc, val)
    elif action == "press_key":
        key = act.get("key", "")
        if key:
            await _safe_press(loc, key)
    elif action == "extract_text":
        attr = act.get("attr")
        if attr:
            text = await loc.get_attribute(attr)
        else:
            text = await loc.inner_text()
        EXTRACTED_TEXTS.append(text)


async def _run_actions(actions: List[Dict]) -> tuple[str, List[str]]:
    WARNINGS.clear()
    for act in actions:
        # DOM の更新が落ち着くまで待ってから次のアクションを実行する
        await _stabilize_page()
        retries = int(act.get("retry", MAX_RETRIES))
        for attempt in range(1, retries + 1):
            try:
                await _apply(act)
                # アクション実行後も DOM 安定化を待つ
                await _stabilize_page()
                break
            except Exception as e:
                log.error("action error (%d/%d): %s", attempt, retries, e)
                if attempt == retries:
                    raise
    page = _get_current_page()
    return await page.content(), WARNINGS.copy()


# -------------------------------------------------- HTTP エンドポイント
@app.post("/execute-dsl")
def execute_dsl():
    try:
        data = request.get_json(force=True)
        # 配列だけ来た場合の後方互換
        if isinstance(data, list):
            data = {"actions": data}
        _validate(data)
    except ValidationError as ve:
        return jsonify(error="InvalidDSL", message=str(ve)), 400
    except Exception as e:
        return jsonify(error="ParseError", message=str(e)), 400

    try:
        _run(_init_browser())
        html, warns = _run(_run_actions(data["actions"]))
        return jsonify({"html": html, "warnings": warns})
    except Exception as e:
        log.exception("execution failed")
        return jsonify(error="ExecutionError", message=str(e)), 500


@app.get("/dom")
def dom_summary():
    try:
        _run(_init_browser())
        snapshot = _run(_build_dom_snapshot())
        return jsonify(snapshot)
    except Exception as e:
        log.error("dom_summary error: %s", e)
        if LAST_SUMMARY:
            return jsonify({**LAST_SUMMARY, "error": str(e)}), 200
        return jsonify(error=str(e)), 500


@app.get("/source")
def source():
    try:
        _run(_init_browser())
        page = _get_current_page()
        return Response(_run(page.content()), mimetype="text/plain")
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.get("/screenshot")
def screenshot():
    try:
        _run(_init_browser())
        page = _get_current_page()
        img = _run(page.screenshot(type="png"))
        return Response(base64.b64encode(img), mimetype="text/plain")
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.get("/elements")
def elements():
    try:
        _run(_init_browser())
        data = _run(_list_elements())
        return jsonify(data)
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.get("/extracted")
def extracted():
    return jsonify(EXTRACTED_TEXTS)


@app.get("/eval_results")
def eval_results():
    return jsonify(EVAL_RESULTS)


@app.get("/healthz")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run("0.0.0.0", 7000, threaded=False)
