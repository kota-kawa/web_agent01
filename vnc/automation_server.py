# vnc/automation_server.py
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Dict, List, Optional, Union
from vnc.locator_utils import SmartLocator

import httpx
from flask import Flask, Response, jsonify, request
from jsonschema import Draft7Validator, ValidationError
from playwright.async_api import Error as PwError
from playwright.async_api import async_playwright

#from locator_utils import SmartLocator  # 同ディレクトリ

# -----------------------------------------------------------------------------
# 基本設定
# -----------------------------------------------------------------------------
app = Flask(__name__)
log = logging.getLogger("auto")
logging.basicConfig(level=logging.INFO)

ACTION_TIMEOUT = 5_000    # 個別操作の最大待機 (ms)
EARLY_TIMEOUT = 300       # 存在確認の早期タイムアウト (ms)
MAX_RETRIES = 3           # 各アクションの試行回数

CDP_URL = "http://localhost:9222"
DEFAULT_URL = os.getenv("START_URL", "https://example.com")

# -----------------------------------------------------------------------------
# JSON Schema: LLM から受け取る DSL 定義
# -----------------------------------------------------------------------------
_ACTION_ENUM = [
    "navigate", "click", "click_text", "type",
    "wait", "scroll"
]
payload_schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": _ACTION_ENUM},
                    "target": {"type": "string"},
                    "value":  {"type": "string"},
                    "ms":     {"type": "integer", "minimum": 0},
                    "amount": {"type": "integer"},
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"]
                    }
                },
                "required": ["action"]
            }
        },
        "complete": {"type": "boolean"}
    },
    "required": ["actions", "complete"],
    "additionalProperties": False
}
validator = Draft7Validator(payload_schema)


def validate_payload(data: Dict) -> None:
    """JSON Schema に従って DSL を検証"""
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    if errors:
        msgs = [e.message for e in errors]
        raise ValidationError("; ".join(msgs))


# -----------------------------------------------------------------------------
# Playwright ブラウザ管理
# -----------------------------------------------------------------------------
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

PLAYWRIGHT = None
BROWSER = None
PAGE = None


def run_sync(coro):
    """同期関数から非同期関数を呼び出すユーティリティ"""
    return LOOP.run_until_complete(coro)


async def _wait_cdp_available(timeout_s: int = 15) -> bool:
    """CDP 起動待ち"""
    deadline = time.time() + timeout_s
    async with httpx.AsyncClient(timeout=2) as client:
        while time.time() < deadline:
            try:
                await client.get(f"{CDP_URL}/json/version")
                return True
            except httpx.HTTPError:
                await asyncio.sleep(1)
    return False


async def init_browser() -> None:
    global PLAYWRIGHT, BROWSER, PAGE
    if PAGE:
        return

    PLAYWRIGHT = await async_playwright().start()
    if await _wait_cdp_available():
        try:
            BROWSER = await PLAYWRIGHT.chromium.connect_over_cdp(CDP_URL)
            context = BROWSER.contexts[0] if BROWSER.contexts else await BROWSER.new_context()
            PAGE = context.pages[0] if context.pages else await context.new_page()
            await PAGE.bring_to_front()
        except PwError:
            pass

    if PAGE is None:  # Fallback: headless launch
        BROWSER = await PLAYWRIGHT.chromium.launch(headless=True)
        PAGE = await BROWSER.new_page()

    await PAGE.goto(DEFAULT_URL, wait_until="load")
    log.info("Browser initialized")


# -----------------------------------------------------------------------------
# アクション実装
# -----------------------------------------------------------------------------
async def _safe_click(loc, force: bool = False) -> None:
    await loc.first.wait_for(state="visible", timeout=ACTION_TIMEOUT)
    await loc.first.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT)
    await loc.first.click(timeout=ACTION_TIMEOUT, force=force)


async def _safe_fill(loc, value: str) -> None:
    await loc.first.wait_for(state="visible", timeout=ACTION_TIMEOUT)
    await loc.first.fill(value, timeout=ACTION_TIMEOUT)


async def _apply_action(act: Dict) -> None:
    global PAGE
    action = act["action"]
    target = act.get("target", "")
    value = act.get("value", "")
    ms = int(act.get("ms", 0))
    amount = int(act.get("amount", 400))
    direction = act.get("direction", "down")

    # ------------------------------------------------------------------
    # ナビゲーション
    # ------------------------------------------------------------------
    if action == "navigate":
        await PAGE.goto(target, wait_until="load", timeout=ACTION_TIMEOUT)
        return

    # ------------------------------------------------------------------
    # 明示 wait
    # ------------------------------------------------------------------
    if action == "wait":
        await PAGE.wait_for_timeout(ms)
        return

    # ------------------------------------------------------------------
    # スクロール
    # ------------------------------------------------------------------
    if action == "scroll":
        offset = amount if direction == "down" else -amount
        if target:
            loc = PAGE.locator(target)
            await loc.evaluate("(el, y) => el.scrollBy(0, y)", offset)
        else:
            await PAGE.evaluate("(y) => window.scrollBy(0, y)", offset)
        return

    # ------------------------------------------------------------------
    # Locator 必要系 (click / click_text / type)
    # ------------------------------------------------------------------
    locator: Optional = None
    if action == "click_text":
        locator = await SmartLocator(PAGE, f"text={target}").locate()
    else:
        locator = await SmartLocator(PAGE, target).locate()

    if locator is None:
        log.warning("Locator not found for target=%s", target)
        return  # Locator が存在しなければスキップ

    if action == "click" or action == "click_text":
        await _safe_click(locator)
        return

    if action == "type":
        await _safe_fill(locator, value)
        return

    log.warning("Unknown action=%s", action)


async def run_actions(actions: List[Dict]) -> str:
    """DSL 内の actions を順次実行し、最後にページ HTML を返す"""
    for act in actions:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await _apply_action(act)
                break
            except Exception as e:
                log.error("Playwright action error (attempt %d/%d): %s", attempt, MAX_RETRIES, e)
                if attempt == MAX_RETRIES:
                    raise
                await asyncio.sleep(0.5)  # back-off

    return await PAGE.content()

# -----------------------------------------------------------------------------
# HTTP エンドポイント
# -----------------------------------------------------------------------------
@app.post("/execute-dsl")
def execute_dsl():
    try:
        payload = request.get_json(force=True)
        validate_payload(payload)
    except ValidationError as ve:
        return jsonify(error="InvalidDSL", message=str(ve)), 400
    except Exception as e:
        return jsonify(error="ParseError", message=str(e)), 400

    try:
        run_sync(init_browser())
        html = run_sync(run_actions(payload["actions"]))
        return Response(html, mimetype="text/plain")
    except Exception as e:
        log.exception("DSL execution failed")
        return jsonify(error="ExecutionError", message=str(e)), 500


@app.get("/source")
def source():
    try:
        run_sync(init_browser())
        return Response(run_sync(PAGE.content()), mimetype="text/plain")
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.get("/screenshot")
def screenshot():
    try:
        run_sync(init_browser())
        img = run_sync(PAGE.screenshot(type="png"))
        return Response(base64.b64encode(img), mimetype="text/plain")
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.get("/healthz")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7000, threaded=False)
