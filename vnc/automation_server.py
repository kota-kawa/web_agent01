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

from vnc.locator_utils import SmartLocator   # 同ディレクトリ

# -------------------------------------------------- 基本設定
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("auto")

ACTION_TIMEOUT = 5_000          # ms  個別アクション猶予
MAX_RETRIES = 3
CDP_URL = "http://localhost:9222"
DEFAULT_URL = os.getenv("START_URL", "https://example.com")

# -------------------------------------------------- DSL スキーマ
_ACTIONS = ["navigate", "click", "click_text", "type", "wait", "scroll"]
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
                    "value":  {"type": "string"},
                    "ms":     {"type": "integer", "minimum": 0},
                    "amount": {"type": "integer"},
                    "direction": {"type": "string", "enum": ["up", "down"]}
                },
                "required": ["action"],
                "additionalProperties": True     # ★ 不明キーは許可
            }
        },
        "complete": {"type": "boolean"}          # ★ 任意
    },
    "required": ["actions"],
    "additionalProperties": True                 # ★ ここも許可
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
            ctx = BROWSER.contexts[0] if BROWSER.contexts else await BROWSER.new_context()
            PAGE = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await PAGE.bring_to_front()
        except PwError:
            pass

    if PAGE is None:
        BROWSER = await PW.chromium.launch(headless=True)
        PAGE = await BROWSER.new_page()

    await PAGE.goto(DEFAULT_URL, wait_until="load")
    log.info("browser ready")

# -------------------------------------------------- アクション実装
async def _safe_click(l, force=False):
    await l.first.wait_for(state="visible", timeout=ACTION_TIMEOUT)
    await l.first.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT)
    await l.first.click(timeout=ACTION_TIMEOUT, force=force)


async def _safe_fill(l, val: str):
    await l.first.wait_for(state="visible", timeout=ACTION_TIMEOUT)
    await l.first.fill(val, timeout=ACTION_TIMEOUT)


async def _apply(act: Dict):
    global PAGE
    a = act["action"]
    tgt = act.get("target", "")
    val = act.get("value", "")
    ms = int(act.get("ms", 0))
    amt = int(act.get("amount", 400))
    dir_ = act.get("direction", "down")

    # -- navigate / wait / scroll はロケータ不要
    if a == "navigate":
        await PAGE.goto(tgt, wait_until="load", timeout=ACTION_TIMEOUT)
        return
    if a == "wait":
        await PAGE.wait_for_timeout(ms)
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
    if a == "click_text":
        loc = await SmartLocator(PAGE, f"text={tgt}").locate()
    else:
        loc = await SmartLocator(PAGE, tgt).locate()

    if loc is None:                # 要素が無ければスキップ
        log.warning("locator not found: %s", tgt)
        return

    if a in ("click", "click_text"):
        await _safe_click(loc)
    elif a == "type":
        await _safe_fill(loc, val)


async def _run_actions(actions: List[Dict]) -> str:
    for act in actions:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await _apply(act)
                break
            except Exception as e:
                log.error("action error (%d/%d): %s", attempt, MAX_RETRIES, e)
                if attempt == MAX_RETRIES:
                    raise
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


@app.get("/healthz")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run("0.0.0.0", 7000, threaded=False)
