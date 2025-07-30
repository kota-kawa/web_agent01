# vnc/automation_server.py
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Dict, List, Optional


import httpx
from flask import Flask, Response, jsonify, request
from jsonschema import Draft7Validator, ValidationError
from playwright.async_api import Error as PwError, async_playwright

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
SPA_STABILIZE_TIMEOUT = int(os.getenv("SPA_STABILIZE_TIMEOUT", "5000"))  # ms  SPA描画安定待ち

# Event listener tracker script will be injected on every page load
_WATCHER_SCRIPT = None

# -------------------------------------------------- DSL スキーマ
_ACTIONS = [
    "navigate",
    "click",
    "click_text",
    "type",
    "wait",
    "scroll",
    "go_back",
    "go_forward",
    "hover",
    "select_option",
    "press_key",
    "wait_for_selector",
    "extract_text",
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
                    "ms": {"type": "integer", "minimum": 0},
                    "amount": {"type": "integer"},
                    "direction": {"type": "string", "enum": ["up", "down"]},
                    "key": {"type": "string"},
                    "retry": {"type": "integer", "minimum": 1},
                    "attr": {"type": "string"},
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

PW = BROWSER = PAGE = None
EXTRACTED_TEXTS: List[str] = []


def _run(coro):
    return LOOP.run_until_complete(coro)


async def _wait_cdp(t: int = 15) -> bool:
    deadline = time.time() + t
    async with httpx.AsyncClient(timeout=2) as c:
        while time.time() < deadline:
            try:
                await c.get(f"{CDP_URL}/json/version")
                return True
            except httpx.HTTPError:
                await asyncio.sleep(1)
    return False


async def _init_browser():
    global PW, BROWSER, PAGE
    if PAGE:
        return
    PW = await async_playwright().start()

    if await _wait_cdp():
        try:
            BROWSER = await PW.chromium.connect_over_cdp(CDP_URL)
            ctx = (
                BROWSER.contexts[0] if BROWSER.contexts else await BROWSER.new_context()
            )
            PAGE = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await PAGE.bring_to_front()
        except PwError:
            pass

    if PAGE is None:
        BROWSER = await PW.chromium.launch(headless=True)
        PAGE = await BROWSER.new_page()

    # Inject event listener tracking script on every navigation
    global _WATCHER_SCRIPT
    if _WATCHER_SCRIPT is None:
        path = os.path.join(os.path.dirname(__file__), "eventWatcher.js")
        with open(path, encoding="utf-8") as f:
            _WATCHER_SCRIPT = f.read()
    try:
        await PAGE.add_init_script(_WATCHER_SCRIPT)
    except Exception as e:
        log.error("add_init_script failed: %s", e)

    await PAGE.goto(DEFAULT_URL, wait_until="load")
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
    loc = PAGE.locator("a,button,input,textarea,select")
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
        await PAGE.evaluate(script, timeout_ms)
    except Exception:
        await PAGE.wait_for_timeout(300)


# SPA 安定化関数 ----------------------------------------
async def _stabilize_page():
    """SPA で DOM が書き換わるまで待機する共通ヘルパ."""
    try:
        # ネットワーク要求が終わるまで待機
        await PAGE.wait_for_load_state(
            "networkidle", timeout=SPA_STABILIZE_TIMEOUT
        )
    except Exception:
        pass
    await _wait_dom_idle(SPA_STABILIZE_TIMEOUT)


async def _apply(act: Dict):
    global PAGE
    a = act["action"]
    tgt = act.get("target", "")
    if isinstance(tgt, list):
        tgt = " || ".join(str(s).strip() for s in tgt if s)
    val = act.get("value", "")
    ms = int(act.get("ms", 0))
    amt = int(act.get("amount", 400))
    dir_ = act.get("direction", "down")

    # -- navigate / wait / scroll はロケータ不要
    if a == "navigate":
        await PAGE.goto(tgt, wait_until="load", timeout=ACTION_TIMEOUT)
        return
    if a == "go_back":
        await PAGE.go_back(wait_until="load")
        return
    if a == "go_forward":
        await PAGE.go_forward(wait_until="load")
        return
    if a == "wait":
        await PAGE.wait_for_timeout(ms)
        return
    if a == "wait_for_selector":
        await PAGE.wait_for_selector(tgt, state="visible", timeout=ms)
        return
    if a == "scroll":
        offset = amt if dir_ == "down" else -amt
        if tgt:
            await PAGE.locator(tgt).evaluate("(el,y)=>el.scrollBy(0,y)", offset)
        else:
            await PAGE.evaluate("(y)=>window.scrollBy(0,y)", offset)
        return

    # -- ロケータ系
    loc: Optional = None
    for _ in range(LOCATOR_RETRIES):
        if a == "click_text":
            loc = await SmartLocator(PAGE, f"text={tgt}").locate()
        else:
            loc = await SmartLocator(PAGE, tgt).locate()
        if loc is not None:
            break
        await _stabilize_page()
        await PAGE.wait_for_timeout(500)

    if loc is None:
        log.warning("locator not found: %s", tgt)
        return

    if a in ("click", "click_text"):
        await _safe_click(loc)
    elif a == "type":
        await _safe_fill(loc, val)
    elif a == "hover":
        await _safe_hover(loc)
    elif a == "select_option":
        await _safe_select(loc, val)
    elif a == "press_key":
        key = act.get("key", "")
        if key:
            await _safe_press(loc, key)
    elif a == "extract_text":
        attr = act.get("attr")
        if attr:
            text = await loc.get_attribute(attr)
        else:
            text = await loc.inner_text()
        EXTRACTED_TEXTS.append(text)


async def _run_actions(actions: List[Dict]) -> str:
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
        # 小休止（連打防止）
        await asyncio.sleep(0.5)
    return await PAGE.content()


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
        html = _run(_run_actions(data["actions"]))
        return Response(html, mimetype="text/plain")
    except Exception as e:
        log.exception("execution failed")
        return jsonify(error="ExecutionError", message=str(e)), 500


@app.get("/source")
def source():
    try:
        _run(_init_browser())
        return Response(_run(PAGE.content()), mimetype="text/plain")
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.get("/screenshot")
def screenshot():
    try:
        _run(_init_browser())
        img = _run(PAGE.screenshot(type="png"))
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


@app.get("/healthz")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run("0.0.0.0", 7000, threaded=False)
