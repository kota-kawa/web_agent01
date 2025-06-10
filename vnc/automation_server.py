# automation_server.py
import asyncio
import time
import logging
import traceback
import sys
from typing import List, Dict, Union

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

# 一貫したイベントループを確保する
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

CDP = "http://localhost:9222"
# Web UI の起点となるページ（最初にブラウザを開いたときの URL）
WEB = "http://web:5000"  # VNC が初期表示するページ（チャット UI 無し）

# ================================================================
# ここからグローバルに保持する「ブラウザ」と「ページ」オブジェクト
# （変更後: 起動時に一度だけ生成して、以降は使い回す）
# ================================================================
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
    これを呼ぶときに、CDP に接続できるか試し、だめならヘッドレス起動する。
    """
    global PLAYWRIGHT, GLOBAL_BROWSER, GLOBAL_PAGE

    if GLOBAL_PAGE is not None:
        # すでに生成済みなら何もせず返す
        return

    PLAYWRIGHT = await async_playwright().start()

    # 1) まず CDP 経由で接続を試みる
    # 何秒待っても DevTools Protocol が立ち上がらなかったら False を返す
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
        # CDP に接続可能なら接続して既存のブラウザを流用
        try:
            GLOBAL_BROWSER = await PLAYWRIGHT.chromium.connect_over_cdp(CDP)
            # 既存のコンテキスト・ページがあればそれを使う
            # （なければ新規作成されるので問題ない）
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
        # CDP が使えない／接続に失敗した場合はヘッドレスで立ち上げて１ページ作る
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

async def safe_click_by_text(page, txt: str):
    """
    Playwright strict-mode で複数マッチした場合の 500 を防ぐ。
    1) リンク (<a>) を role='link', exact=True で取得しクリック
    2) 見つからなければ page.get_by_text(..., exact=True).first.click()
    3) それでもダメなら page.get_by_text(...).first.click()
    """
    # 1) リンク (exact)
    link = page.get_by_role("link", name=txt, exact=True)
    if await link.count():
        await link.first.click()
        return

    # 2) exact=True テキスト
    exact_loc = page.get_by_text(txt, exact=True)
    if await exact_loc.count():
        await exact_loc.first.click()
        return

    # 3) 非 strict (first match)
    await page.get_by_text(txt).first.click()

async def run_actions(raw: List[Dict]) -> str:
    """
    Action のリストを受け取り、全て順次実行した後、現在のページ HTML を返す。
    **変更後: ブラウザは閉じずに保持し続ける。**
    """
    global GLOBAL_PAGE

    # もしまだブラウザが初期化されていなければ、ここで init_ を呼ぶ
    if GLOBAL_PAGE is None:
        await init_browser_and_page()

    acts = [await normalize(x) for x in raw]

    async def exec_one(act):
        match act["action"]:
            case "navigate":
                await GLOBAL_PAGE.goto(act["target"])
            case "click":
                if sel := act.get("target"):
                    await GLOBAL_PAGE.locator(sel).first.click()
                elif txt := act.get("text"):
                    await safe_click_by_text(GLOBAL_PAGE, txt)
            case "click_text":
                await safe_click_by_text(GLOBAL_PAGE, act["target"])
            case "type":
                await GLOBAL_PAGE.fill(act["target"], act.get("value", ""))
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
                await exec_one(a)
                break
            except Exception as e:
                log.error(f"Playwright action error (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                else:
                    log.error("Max retries reached. Attempting browser reset.")
                    await reset_browser()
                    try:
                        await exec_one(a)
                    except Exception as e2:
                        log.error("Recovery attempt failed: %s", e2)
                    break
    # 全アクション実行後にページコンテンツを返す
    html = await GLOBAL_PAGE.content()
    return html

# ---------------- Flask ルート -----------------------------------
@app.get("/source")
def source():
    """
    変更前: run_actions([]) を毎回やってブラウザを立ち上げ・閉じしていた。
    変更後: すでに初期化済みのページオブジェクトがあればそれを使い、ページソースだけ返す。
    """
    try:
        # もしまだ初期化されていなければ行う（初回のみ重い処理）
        if GLOBAL_PAGE is None:
            # 「Run in event loop」として init_browser_and_page() を同期的に呼び出し
            run_sync(init_browser_and_page())
        # ページの現在HTMLをキャッシュから取ってくる
        html = run_sync(GLOBAL_PAGE.content())
        return Response(html, mimetype="text/plain")
    except Exception as e:
        log.exception("fatal")
        return jsonify(error=str(e)), 500

@app.post("/execute-dsl")
def exec_dsl():
    """
    変更前: run_actions(acts) で毎回ブラウザを立ち上げ・閉じしていた。
    変更後: ブラウザは保持しつつ、アクションだけ実行して HTML を返す。
    """
    try:
        data = request.get_json(force=True)
        acts: Union[List, Dict] = data.get("actions", data)
        if isinstance(acts, dict):
            acts = [acts]
        # run_actions では「もしまだ初期化されていなければ内部で init を呼ぶ」仕組み
        html = run_sync(run_actions(acts))
        return Response(html, mimetype="text/plain")
    except Exception as e:
        log.exception("fatal")
        return jsonify(error=str(e)), 500

@app.get("/healthz")
def health():
    return "ok", 200

if __name__ == "__main__":
    # 必要に応じて起動時にブラウザを初期化
    # run_sync(init_browser_and_page())
    app.run(host="0.0.0.0", port=7000, threaded=False)
