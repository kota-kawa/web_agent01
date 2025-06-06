#!/usr/local/bin/python3
from flask import Flask, jsonify, Response, request
import asyncio, time, httpx, logging, sys, traceback
from typing import List, Dict, Union

# ---- Playwright -------------------------------------------------
try:
    from playwright.async_api import async_playwright, Error as PwError
except Exception:
    traceback.print_exc(); sys.exit(1)

app = Flask(__name__)
log = logging.getLogger("auto")

CDP = "http://localhost:9222"
WEB = "http://web:5000"           # VNC が初期表示するページ（チャット UI 無し）

# ----------------------------------------------------------------
async def wait_cdp(t: int = 25) -> bool:
    """CDP (chrome-remote-debugging) が立ち上がるまで待つ"""
    dead = time.time() + t
    async with httpx.AsyncClient(timeout=2) as c:
        while time.time() < dead:
            try:
                await c.get(f"{CDP}/json/version"); return True
            except httpx.HTTPError:
                await asyncio.sleep(1)
    return False

# ---------------- DSL 正規化 ------------------------------------
def normalize(a: Dict) -> Dict:
    a = {k.lower(): v for k, v in a.items()}
    a["action"] = a.get("action", "").lower()
    if "selector" in a and "target" not in a:
        a["target"] = a.pop("selector")
    if a["action"] in ("click_text",) and "text" in a and "target" not in a:
        a["target"] = a["text"]
    return a

# ---------------- クリックヘルパー ------------------------------
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

# ---------------- DSL 実行 --------------------------------------
async def run_actions(raw: List[Dict]) -> str:
    acts = [normalize(x) for x in raw]

    async with async_playwright() as p:
        browser = page = None
        if await wait_cdp():
            try:
                browser = await p.chromium.connect_over_cdp(CDP)
                page    = browser.contexts[0].pages[0]
                await page.bring_to_front()
            except PwError:
                browser = None
        if page is None:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(WEB)

        for a in acts:
            ############## 変更: エラー時リトライを追加 ################
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                try:
                    match a["action"]:
                        case "navigate":
                            await page.goto(a["target"])
                        case "click":
                            if sel := a.get("target"):
                                await page.locator(sel).first.click()
                            elif txt := a.get("text"):
                                await safe_click_by_text(page, txt)
                        case "click_text":
                            await safe_click_by_text(page, a["target"])
                        case "type":
                            await page.fill(a["target"], a.get("value", ""))
                        case "wait":
                            await page.wait_for_timeout(int(a.get("ms", 500)))
                        case _:
                            log.warning("Unknown action skipped: %s", a)
                    # 成功したらループを抜ける
                    break
                except Exception as e:
                    log.error(f"Playwright action error (attempt {attempt}/{max_retries}): {e}")
                    if attempt < max_retries:
                        # 1秒待って再試行
                        await asyncio.sleep(1)
                    else:
                        # 3回失敗したら諦めて次のアクションへ
                        log.error("Max retries reached. Skipping this action.")
            ############## 変更ここまで ################

        html = await page.content()
        if browser: await browser.close()
        return html

# ---------------- Flask ルート -----------------------------------
@app.get("/source")
def source():
    try:
        html = asyncio.run(run_actions([]))
        return Response(html, mimetype="text/plain")
    except Exception as e:
        log.exception("fatal"); return jsonify(error=str(e)), 500

@app.post("/execute-dsl")
def exec_dsl():
    try:
        data = request.get_json(force=True)
        acts: Union[List, Dict] = data.get("actions", data)
        if isinstance(acts, dict): acts = [acts]
        html = asyncio.run(run_actions(acts))
        return Response(html, mimetype="text/plain")
    except Exception as e:
        log.exception("fatal"); return jsonify(error=str(e)), 500

@app.get("/healthz")
def health(): return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7000)
