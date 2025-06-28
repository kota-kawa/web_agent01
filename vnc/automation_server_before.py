# automation_server.py (全文)

import os
import asyncio
import time
import logging
import traceback
import sys
from typing import List, Dict, Union

import base64

from flask import Flask, jsonify, Response, request

import httpx

# ---- Playwright -------------------------------------------------
try:
    from playwright.async_api import async_playwright, Error as PwError
except Exception:
    traceback.print_exc()
    sys.exit(1)

app = Flask(__name__)
log = logging.getLogger("auto")
log.setLevel(logging.INFO)

# 各 Playwright アクションのデフォルトタイムアウト(ms)
ACTION_TIMEOUT = 90000

# 一貫したイベントループを確保する
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

CDP = "http://localhost:9222"
# 起動時に開く既定ページ
WEB = os.getenv("START_URL", "https://www.yahoo.co.jp")

PLAYWRIGHT = None        # async_playwright() の戻り値を保持
GLOBAL_BROWSER = None    # Browser オブジェクト
GLOBAL_PAGE = None       # Page オブジェクト

def run_sync(coro):
    """Run async coroutine on the global event loop."""
    return LOOP.run_until_complete(coro)

async def reset_browser():
    """ブラウザを再起動してページを開き直す（自己修復用）"""
    global PLAYWRIGHT, GLOBAL_BROWSER, GLOBAL_PAGE
    try:
        if GLOBAL_BROWSER:
            await GLOBAL_BROWSER.close()
    except Exception:
        pass
    GLOBAL_PAGE = None
    if PLAYWRIGHT:
        try:
            await PLAYWRIGHT.stop()
        except Exception:
            pass
    PLAYWRIGHT = None
    await init_browser_and_page()

async def init_browser_and_page():
    """
    初回アクセス時に、ブラウザ／ページを起動してグローバル変数に保持する。
    """
    global PLAYWRIGHT, GLOBAL_BROWSER, GLOBAL_PAGE

    if GLOBAL_PAGE is not None:
        return

    PLAYWRIGHT = await async_playwright().start()

    async def wait_cdp(t: int = 25) -> bool:
        dead = time.time() + t
        async with httpx.AsyncClient(timeout=2) as c:
            while time.time() < dead:
                try:
                    await c.get(f"{CDP}/json/version")
                    return True
                except httpx.HTTPError:
                    await asyncio.sleep(1)
        return False

    connected = False
    if await wait_cdp():
        try:
            GLOBAL_BROWSER = await PLAYWRIGHT.chromium.connect_over_cdp(CDP)
            context = GLOBAL_BROWSER.contexts[0] if GLOBAL_BROWSER.contexts else None
            if context:
                GLOBAL_PAGE = context.pages[0]
                await GLOBAL_PAGE.bring_to_front()
            else:
                GLOBAL_PAGE = await GLOBAL_BROWSER.new_page()
                await GLOBAL_PAGE.goto(WEB)
            connected = True
        except PwError as e:
            log.error("CDP 接続に失敗しました: %s", e)
            connected = False

    if not connected:
        GLOBAL_BROWSER = await PLAYWRIGHT.chromium.launch(headless=True)
        GLOBAL_PAGE = await GLOBAL_BROWSER.new_page()
        await GLOBAL_PAGE.goto(WEB)

    log.info("Playwright ブラウザ・ページを初期化しました。")

async def normalize(a: Dict) -> Dict:
    a = {k.lower(): v for k, v in a.items()}
    a["action"] = a.get("action", "").lower()
    if "selector" in a and "target" not in a:
        a["target"] = a.pop("selector")
    if a["action"] in ("click_text",) and "text" in a and "target" not in a:
        a["target"] = a["text"]
    return a

async def safe_click(loc, timeout: int = ACTION_TIMEOUT, force: bool = False):
    # 早期バイパス: 要素が存在しなければ即時に抜ける
    count = await loc.count()
    if count == 0:
        log.warning("要素が存在しないためスキップ: %s", loc)
        return
    try:
        await loc.wait_for(state="visible", timeout=timeout)
        await loc.wait_for(state="attached", timeout=timeout)
        await loc.scroll_into_view_if_needed(timeout=timeout)
        await loc.click(timeout=timeout, force=force)
    except Exception as e:
        log.warning("click failed: %s; trying recovery", e)
        try:
            await GLOBAL_PAGE.keyboard.press("Escape")
            await asyncio.sleep(500) # 0.5秒待機
            await loc.click(timeout=timeout, force=True)
            log.info("リカバリー成功: Escapeキー + 強制クリック")
            return
        except Exception:
            pass
        try:
            await loc.evaluate("el => el.click()")
            log.info("リカバリー成功: JavaScriptによるクリック")
        except Exception as e2:
            log.error(f"最終的なリカバリー（JSクリック）にも失敗しました: {e2}")
            raise

async def safe_fill(loc, value: str, timeout: int = ACTION_TIMEOUT):
    try:
        await loc.wait_for(state="visible", timeout=timeout)
        await loc.scroll_into_view_if_needed(timeout=timeout)
        await loc.fill(value, timeout=timeout)
    except Exception as e:
        log.warning("fill failed: %s; trying JS value set", e)
        try:
            await loc.evaluate(
                "(el, val) => {el.focus(); el.value = val; el.dispatchEvent(new Event('input', {bubbles: true}));}",
                value,
            )
        except Exception as e2:
            log.error("fallback fill failed: %s", e2)
            raise

async def safe_click_by_text(page, txt: str, timeout: int = ACTION_TIMEOUT, force: bool = False):
        # User-first locator を活用
    locator = page.get_by_role("link", name=txt, exact=True)
    if await locator.count():
        await safe_click(locator.first, timeout=timeout, force=force)
        return
    # フォールバック: テキストセレクタ
    text_loc = page.get_by_text(txt, exact=True)
    if await text_loc.count():
        await safe_click(text_loc.first, timeout=timeout, force=force)
        return
    log.warning("テキスト要素 '%s' が見つかりません", txt)

async def run_actions(raw: List[Dict]) -> str:
    global GLOBAL_PAGE
    if GLOBAL_PAGE is None:
        await init_browser_and_page()

    acts = [await normalize(x) for x in raw]

    async def exec_one(act, force=False):
      
        await GLOBAL_PAGE.wait_for_load_state("load", timeout=ACTION_TIMEOUT)
        match act["action"]:
            case "navigate":
                await GLOBAL_PAGE.goto(
                    act["target"],
                    timeout=ACTION_TIMEOUT,
                    #wait_until="load",
                    wait_until="load",
                )
            case "click":
                if sel := act.get("target"):
                    loc = GLOBAL_PAGE.locator(sel).first
                    await safe_click(loc, timeout=ACTION_TIMEOUT, force=force)
                elif txt := act.get("text"):
                    await safe_click_by_text(
                        GLOBAL_PAGE,
                        txt,
                        timeout=ACTION_TIMEOUT,
                        force=force,
                    )
            case "click_text":
                await safe_click_by_text(
                    GLOBAL_PAGE,
                    act["target"],
                    timeout=ACTION_TIMEOUT,
                    force=force,
                )
            case "type":
                loc = GLOBAL_PAGE.locator(act["target"]).first
                await safe_fill(loc, act.get("value", ""), timeout=ACTION_TIMEOUT)
            case "wait":
                await GLOBAL_PAGE.wait_for_timeout(int(act.get("ms", 500)))
            case "scroll":
                amt = int(act.get("amount", 400))
                if str(act.get("direction", "down")).lower().startswith("up"):
                    amt = -amt
                if tgt := act.get("target"):
                    await GLOBAL_PAGE.locator(tgt).evaluate("(el,y)=>el.scrollBy(0,y)", amt)
                else:
                    await GLOBAL_PAGE.evaluate("(y)=>window.scrollBy(0,y)", amt)
            case _:
                log.warning("Unknown action skipped: %s", act)

    for a in acts:
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                await exec_one(a, force=attempt > 1)
                break
            except Exception as e:
                log.error(f"Playwright action error (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                else:
                    log.error("Max retries reached. Attempting browser reset.")
                    await reset_browser()
                    try:
                        # 最後の試行として、強制フラグを立てて実行
                        await exec_one(a, force=True)
                    except Exception as e2:
                        log.error("Recovery attempt failed: %s", e2)
                        # ここで例外を再送出するか、エラーを報告して処理を続行するかを選択
                        # 今回はループを抜ける
                    break # リセット後、次のアクションに進む

    html = await GLOBAL_PAGE.content()
    return html


async def take_screenshot_async():
    """非同期でスクリーンショットを取得するヘルパー関数"""
    if GLOBAL_PAGE is None:
        await init_browser_and_page()
    # ページのロードが完了し、ネットワークがアイドル状態になるのを待つ
    await GLOBAL_PAGE.wait_for_load_state("load", timeout=40000)
    await asyncio.sleep(1) # 念のためのレンダリング待機

   
    return await GLOBAL_PAGE.screenshot(type='png')

@app.get("/screenshot")
def screenshot():
    """現在のページのスクリーンショットを Base64 でエンコードして返す"""
    try:
        screenshot_bytes = run_sync(take_screenshot_async())
        encoded_string = base64.b64encode(screenshot_bytes).decode('utf-8')
        # データURIスキーム形式で返す
        return Response(f"data:image/png;base64,{encoded_string}", mimetype="text/plain")
    except Exception as e:
        log.exception("screenshot fatal")
        return jsonify(error=str(e)), 500
    
    
    
# ---------------- Flask ルート -----------------------------------
@app.get("/source")
def source():
    try:
        if GLOBAL_PAGE is None:
            run_sync(init_browser_and_page())
        html = run_sync(GLOBAL_PAGE.content())
        return Response(html, mimetype="text/plain")
    except Exception as e:
        log.exception("fatal")
        return jsonify(error=str(e)), 500

@app.post("/execute-dsl")
def exec_dsl():
    try:
        data = request.get_json(force=True)
        acts: Union[List, Dict] = data.get("actions", data)
        if isinstance(acts, dict):
            acts = [acts]
        html = run_sync(run_actions(acts))
        return Response(html, mimetype="text/plain")
    except Exception as e:
        log.exception("fatal")
        return jsonify(error=str(e)), 500

@app.get("/healthz")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7000, threaded=False)
