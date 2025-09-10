# vnc/automation_server.py
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from typing import Dict, List, Optional
from urllib.parse import urlparse


import httpx
from flask import Flask, Response, jsonify, request
from jsonschema import Draft7Validator, ValidationError
from playwright.async_api import Error as PwError, async_playwright

from vnc.locator_utils import SmartLocator  # 同ディレクトリ

# -------------------------------------------------- 基本設定
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("auto")

# 環境変数で調整可能な設定値
ACTION_TIMEOUT = int(os.getenv("ACTION_TIMEOUT", "10000"))  # ms  個別アクション猶予
NAVIGATE_TIMEOUT = int(os.getenv("NAVIGATE_TIMEOUT", "15000"))  # ms  ナビゲーション専用タイムアウト（延長）
LOCATOR_TIMEOUT = int(os.getenv("LOCATOR_TIMEOUT", "7000"))  # ms  セレクタ待機タイムアウト（3s→7s）
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
LOCATOR_RETRIES = int(os.getenv("LOCATOR_RETRIES", "3"))
CDP_URL = "http://localhost:9222"
DEFAULT_URL = os.getenv("START_URL", "https://yahoo.co.jp")
SPA_STABILIZE_TIMEOUT = int(
    os.getenv("SPA_STABILIZE_TIMEOUT", "3000")  # ms  SPA描画安定待ち（2s→3s）
)

# 動的リトライ設定
RETRY_BASE_DELAY = 0.5  # 基本リトライ間隔（秒）
RETRY_BACKOFF_FACTOR = 1.5  # 指数バックオフ係数
MAX_CHUNK_SIZE = int(os.getenv("MAX_CHUNK_SIZE", "10"))  # DSL 分割実行のしきい値

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
    "eval_js",
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


def _validate_url(url: str) -> bool:
    """URL 形式の検証"""
    if not url or not url.strip():
        return False
    try:
        result = urlparse(url.strip())
        return all([result.scheme, result.netloc]) and result.scheme in ('http', 'https')
    except Exception:
        return False


def _validate_selector(selector: str) -> bool:
    """セレクタが空でないことを検証"""
    return bool(selector and selector.strip())


def _get_action_specific_timeout(action: str, default_timeout: int) -> int:
    """アクション種別別のタイムアウト設定"""
    action_timeouts = {
        "navigate": NAVIGATE_TIMEOUT,
        "go_back": NAVIGATE_TIMEOUT, 
        "go_forward": NAVIGATE_TIMEOUT,
        "wait_for_selector": LOCATOR_TIMEOUT,
        "click": ACTION_TIMEOUT,
        "click_text": ACTION_TIMEOUT,
        "type": ACTION_TIMEOUT * 2,  # タイプは長めに
        "hover": ACTION_TIMEOUT // 2,  # ホバーは短め
        "select_option": ACTION_TIMEOUT,
        "press_key": ACTION_TIMEOUT // 2,
        "extract_text": ACTION_TIMEOUT // 2,
        "eval_js": ACTION_TIMEOUT,
        "scroll": ACTION_TIMEOUT // 4,  # スクロールは短め
        "wait": default_timeout,  # wait は指定値をそのまま使用
    }
    return action_timeouts.get(action, default_timeout)


def _get_action_specific_retries(action: str) -> int:
    """アクション種別別のリトライ回数"""
    action_retries = {
        "navigate": 5,  # 遷移は多めにリトライ
        "go_back": 3,
        "go_forward": 3,
        "wait_for_selector": 2,  # 待機は少なめ
        "click": 4,  # クリックは多めにリトライ
        "click_text": 4,
        "type": 3,
        "hover": 2,
        "select_option": 3,
        "press_key": 2,
        "extract_text": 2,
        "eval_js": 1,  # eval_js は1回のみ
        "scroll": 2,
        "wait": 1,  # wait は基本的にリトライ不要
    }
    return action_retries.get(action, MAX_RETRIES)


async def _retry_with_backoff(func, *args, max_retries: int = MAX_RETRIES, action_name: str = "unknown", **kwargs):
    """指数バックオフによるリトライ実行"""
    last_exception = None
    
    for attempt in range(1, max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            log.warning("%s attempt %d/%d failed: %s", action_name, attempt, max_retries, e)
            
            if attempt < max_retries:
                # 指数バックオフで待機
                delay = RETRY_BASE_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1))
                log.info("Retrying %s in %.2f seconds...", action_name, delay)
                await asyncio.sleep(delay)
    
    # 最終的に失敗
    raise last_exception


# -------------------------------------------------- Playwright 管理
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

PW = BROWSER = PAGE = None
EXTRACTED_TEXTS: List[str] = []
EVAL_RESULTS: List[str] = []
WARNINGS: List[str] = []

# 並行実行防止用のロック
EXECUTION_LOCK = asyncio.Lock()


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
                await asyncio.sleep(0.5)
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
    # 操作直前にも短い再確認
    count = await loc.count()
    if count == 0:
        raise Exception("element not found during preparation")
    
    await loc.first.wait_for(state="visible", timeout=ACTION_TIMEOUT)
    await loc.first.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT)
    await loc.first.wait_for(state="visible", timeout=ACTION_TIMEOUT)
    
    # focus と hover を事前に実行してより確実な操作に
    try:
        await loc.first.focus(timeout=ACTION_TIMEOUT//2)
        await loc.first.hover(timeout=ACTION_TIMEOUT//2)
    except Exception as e:
        log.warning("Element focus/hover preparation failed: %s", e)
    
    if not await loc.first.is_enabled():
        raise Exception("element not enabled")


async def _safe_click(l, force=False):
    try:
        await _prepare_element(l)
        await l.first.click(timeout=ACTION_TIMEOUT, force=force)
    except Exception as e:
        if not force:
            log.warning("click retry with force due to: %s", e)
            try:
                await l.first.click(timeout=ACTION_TIMEOUT, force=True)
            except Exception as e2:
                # セーフ操作を強化してもダメな場合の詳細ログ
                raise Exception(f"Click failed even with force=True: {str(e2)}. Original error: {str(e)}")
        else:
            raise


async def _safe_fill(l, val: str):
    # 長文は分割入力を検討
    if len(val) > 1000:
        warning_msg = f"WARNING: Large text input ({len(val)} chars) may be slow. Consider splitting or using setValue alternative."
        WARNINGS.append(warning_msg)
        log.warning(warning_msg)
    
    try:
        await _prepare_element(l)
        await l.first.fill(val, timeout=ACTION_TIMEOUT)
    except Exception as e:
        log.warning("fill retry with click focus due to: %s", e)
        try:
            await _safe_click(l)
            await l.first.fill(val, timeout=ACTION_TIMEOUT)
        except Exception as e2:
            # setValue 代替を提案
            raise Exception(f"Fill failed even after click: {str(e2)}. Consider using eval_js setValue for editors. Original error: {str(e)}")


async def _safe_hover(l):
    try:
        await _prepare_element(l)
        await l.first.hover(timeout=ACTION_TIMEOUT)
    except Exception as e:
        # hover失敗は比較的軽微なので詳細ログ
        raise Exception(f"Hover failed: {str(e)}")


async def _safe_select(l, val: str):
    try:
        await _prepare_element(l)
        await l.first.select_option(val, timeout=ACTION_TIMEOUT)
    except Exception as e:
        raise Exception(f"Select option failed for value '{val}': {str(e)}")


async def _safe_press(l, key: str):
    try:
        await _prepare_element(l)
        await l.first.press(key, timeout=ACTION_TIMEOUT)
    except Exception as e:
        raise Exception(f"Press key '{key}' failed: {str(e)}")


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
        await PAGE.wait_for_timeout(100)


# SPA 安定化関数 ----------------------------------------
async def _stabilize_page():
    """SPA で DOM が書き換わるまで待機する共通ヘルパ."""
    try:
        # ネットワーク要求が終わるまで待機（必須化）
        await PAGE.wait_for_load_state("networkidle", timeout=SPA_STABILIZE_TIMEOUT)
        log.debug("Network idle achieved")
    except Exception as e:
        log.warning("Network idle wait failed: %s", e)
        # ネットワーク待機に失敗してもDOM待機は試行
    
    try:
        # DOM idle も待機
        await _wait_dom_idle(SPA_STABILIZE_TIMEOUT)
        log.debug("DOM idle achieved")
    except Exception as e:
        log.warning("DOM idle wait failed: %s", e)
    
    # 短い追加待機（SPA の遅延描画対応）
    await PAGE.wait_for_timeout(200)


async def _enhanced_stabilize_after_navigation():
    """ナビゲーション後の強化された安定化（遷移直後は特に重要）"""
    try:
        # ローディングスピナーの消失待ち
        await PAGE.wait_for_load_state("load", timeout=NAVIGATE_TIMEOUT)
        await PAGE.wait_for_load_state("networkidle", timeout=NAVIGATE_TIMEOUT) 
        
        # DOM の変更が止まるまで待機（長め）
        await _wait_dom_idle(SPA_STABILIZE_TIMEOUT * 2)
        
        # 最終的な短い待機
        await PAGE.wait_for_timeout(500)
        log.info("Enhanced post-navigation stabilization completed")
        
    except Exception as e:
        log.warning("Enhanced stabilization failed: %s", e)
        # 最低限の待機
        await PAGE.wait_for_timeout(1000)


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
        # URL の検証
        if not _validate_url(tgt):
            warning_msg = f"WARNING: Navigate action skipped - invalid or empty URL: '{tgt}'"
            WARNINGS.append(warning_msg)
            log.warning(warning_msg)
            return
        await PAGE.goto(tgt, wait_until="load", timeout=NAVIGATE_TIMEOUT)
        # ナビゲーション後は強化された安定化を実行
        await _enhanced_stabilize_after_navigation()
        return
    if a == "go_back":
        await PAGE.go_back(wait_until="load", timeout=NAVIGATE_TIMEOUT)
        await _enhanced_stabilize_after_navigation()
        return
    if a == "go_forward":
        await PAGE.go_forward(wait_until="load", timeout=NAVIGATE_TIMEOUT)
        await _enhanced_stabilize_after_navigation()
        return
    if a == "wait":
        await PAGE.wait_for_timeout(ms)
        return
    if a == "wait_for_selector":
        # セレクタの検証
        if not _validate_selector(tgt):
            warning_msg = f"WARNING: wait_for_selector action skipped - empty selector"
            WARNINGS.append(warning_msg)
            log.warning(warning_msg)
            return
        # タイムアウトが指定されていない場合は既定値を使用
        timeout = ms if ms > 0 else LOCATOR_TIMEOUT
        try:
            await PAGE.wait_for_selector(tgt, state="visible", timeout=timeout)
        except Exception as e:
            # wait_for_selector 失敗は warnings 化し、次手に回す
            warning_msg = f"WARNING: wait_for_selector failed - selector '{tgt}' not found within {timeout}ms: {str(e)}"
            WARNINGS.append(warning_msg)
            log.warning(warning_msg)
        return
    if a == "scroll":
        offset = amt if dir_ == "down" else -amt
        if tgt:
            await PAGE.locator(tgt).evaluate("(el,y)=>el.scrollBy(0,y)", offset)
        else:
            await PAGE.evaluate("(y)=>window.scrollBy(0,y)", offset)
        return
    if a == "eval_js":
        script = act.get("script") or val
        if script:
            try:
                result = await PAGE.evaluate(script)
                EVAL_RESULTS.append(result)
            except Exception as e:
                # eval_js 失敗は例外にせず常に warnings 化
                warning_msg = f"WARNING: eval_js failed - {str(e)}. Consider using alternative methods like click or type."
                WARNINGS.append(warning_msg)
                log.warning(warning_msg)
        return

    # -- ロケータ系
    loc: Optional = None
    for _ in range(LOCATOR_RETRIES):
        if a == "click_text":
            if "||" in tgt or tgt.strip().startswith(("css=", "text=", "role=", "xpath=")):
                loc = await SmartLocator(PAGE, tgt).locate()
            else:
                loc = await SmartLocator(PAGE, f"text={tgt}").locate()
        else:
            loc = await SmartLocator(PAGE, tgt).locate()
        if loc is not None:
            break
        await _stabilize_page()

    if loc is None:
        msg = f"locator not found: {tgt}"
        log.warning(msg)
        WARNINGS.append(f"WARNING:auto:{msg}")
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
            if loc:
                await _safe_press(loc, key)
            else:
                # 対象未指定は ページ全体への keypress にフォールバック
                try:
                    await PAGE.keyboard.press(key)
                    log.info("press_key executed on page level for key: %s", key)
                except Exception as e:
                    warning_msg = f"WARNING: press_key failed on page level - {str(e)}"
                    WARNINGS.append(warning_msg)
                    log.warning(warning_msg)
    elif a == "extract_text":
        attr = act.get("attr")
        if attr:
            text = await loc.get_attribute(attr)
        else:
            text = await loc.inner_text()
        EXTRACTED_TEXTS.append(text)


async def _run_actions_with_correlation(actions: List[Dict], correlation_id: str) -> tuple[str, List[str]]:
    """相関ID付きアクション実行"""
    WARNINGS.clear()
    
    # 巨大な DSL の分割実行
    if len(actions) > MAX_CHUNK_SIZE:
        warning_msg = f"WARNING: Large DSL with {len(actions)} actions detected. Consider splitting into smaller chunks. Executing first {MAX_CHUNK_SIZE} actions."
        WARNINGS.append(warning_msg)
        log.warning("[%s] %s", correlation_id, warning_msg)
        actions = actions[:MAX_CHUNK_SIZE]
    
    for i, act in enumerate(actions):
        action_name = act.get('action', 'unknown')
        target = act.get('target', '')[:50] + ('...' if len(act.get('target', '')) > 50 else '')
        
        log.info("[%s] Executing action %d/%d: %s (target: %s)", 
                correlation_id, i+1, len(actions), action_name, target)
        
        # DOM の更新が落ち着くまで待ってから次のアクションを実行する
        await _stabilize_page()
        
        retries = int(act.get("retry", _get_action_specific_retries(action_name)))
        action_failed = False
        
        try:
            # 指数バックオフによるリトライ実行
            await _retry_with_backoff(
                _apply_single_action,
                act,
                max_retries=retries,
                action_name=f"[{correlation_id}] action_{action_name}"
            )
            # アクション実行後も DOM 安定化を待つ
            await _stabilize_page()
            action_failed = False
            
        except Exception as e:
            # 最終リトライ失敗でも例外にせず、warnings に ERROR を格納
            current_url = await PAGE.url() if PAGE and not PAGE.is_closed() else "unknown"
            error_msg = f"ERROR:auto: Action '{action_name}' (#{i+1}) failed after {retries} retries. URL: {current_url}. Error: {str(e)}"
            WARNINGS.append(error_msg)
            action_failed = True
            log.error("[%s] Final retry failed for action %d: %s", correlation_id, i+1, error_msg)
        
        # アクション失敗時も次のアクションの実行を続ける（論理エラーとして扱う）
    
    final_url = await PAGE.url() if PAGE and not PAGE.is_closed() else "unknown" 
    log.info("[%s] DSL execution completed at URL: %s", correlation_id, final_url)
    return await PAGE.content(), WARNINGS.copy()


async def _check_browser_health() -> bool:
    """ブラウザとページの健全性をチェック"""
    try:
        if not BROWSER or not PAGE:
            return False
        
        # ページが閉じられていないかチェック
        if PAGE.is_closed():
            log.warning("Page is closed, needs recreation")
            return False
            
        # 簡単な動作確認（DOM アクセス）
        await PAGE.evaluate("document.readyState", timeout=2000)
        return True
        
    except Exception as e:
        log.warning("Browser health check failed: %s", e)
        return False


async def _recreate_browser_if_needed():
    """必要に応じてブラウザを再作成"""
    global PW, BROWSER, PAGE
    
    if not await _check_browser_health():
        log.info("Recreating browser due to health check failure")
        
        # 既存リソースのクリーンアップ
        try:
            if PAGE and not PAGE.is_closed():
                await PAGE.close()
        except Exception as e:
            log.warning("Failed to close page during recreation: %s", e)
            
        try:
            if BROWSER:
                await BROWSER.close()
        except Exception as e:
            log.warning("Failed to close browser during recreation: %s", e)
            
        # 再初期化
        PW = BROWSER = PAGE = None
        await _init_browser()
        log.info("Browser recreation completed")


async def _safe_browser_operation(operation_func, *args, **kwargs):
    """ブラウザ操作をヘルスチェック付きで実行"""
    max_attempts = 2
    
    for attempt in range(max_attempts):
        try:
            await _recreate_browser_if_needed()
            return await operation_func(*args, **kwargs)
        except Exception as e:
            if attempt == max_attempts - 1:
                # 最終試行でも失敗
                error_msg = f"Browser operation failed after {max_attempts} attempts: {str(e)}"
                log.error(error_msg)
                raise Exception(error_msg)
            else:
                log.warning("Browser operation attempt %d failed, retrying: %s", attempt + 1, e)
                # 次の試行前にブラウザを強制再作成
                global PW, BROWSER, PAGE
                PW = BROWSER = PAGE = None


async def _apply_single_action(act: Dict):
    """単一のアクション実行（リトライ対象の関数）"""
    await _apply(act)


# -------------------------------------------------- HTTP エンドポイント
@app.post("/execute-dsl")
def execute_dsl():
    correlation_id = str(uuid.uuid4())[:8]  # 相関ID生成
    log.info("=== DSL Execution Start [%s] ===", correlation_id)
    
    try:
        data = request.get_json(force=True)
        # 配列だけ来た場合の後方互換
        if isinstance(data, list):
            data = {"actions": data}
        _validate(data)
        
        action_count = len(data.get("actions", []))
        log.info("[%s] Executing %d actions", correlation_id, action_count)
        
    except ValidationError as ve:
        log.warning("[%s] Validation error: %s", correlation_id, ve)
        return jsonify(error="InvalidDSL", message=str(ve)), 400
    except Exception as e:
        log.error("[%s] Parse error: %s", correlation_id, e)
        return jsonify(error="ParseError", message=str(e)), 400

    try:
        # 並行実行防止用のロック取得
        async def safe_execution_with_lock():
            async with EXECUTION_LOCK:
                log.info("[%s] Acquired execution lock", correlation_id)
                await _recreate_browser_if_needed()
                return await _run_actions_with_correlation(data["actions"], correlation_id)
        
        html, warns = _run(safe_execution_with_lock())
        
        log.info("[%s] DSL execution completed - warnings: %d", correlation_id, len(warns))
        if warns:
            log.warning("[%s] Warnings summary: %s", correlation_id, "; ".join(warns[:3]))
        
        return jsonify({"html": html, "warnings": warns})
        
    except Exception as e:
        log.exception("[%s] Execution failed", correlation_id)
        # VNC サーバの 500 を 200＋warnings に変換
        error_msg = f"ERROR:auto: Server execution failed (ID:{correlation_id}): {str(e)}"
        return jsonify({"html": "", "warnings": [error_msg]})


@app.get("/source")
def source():
    try:
        _run(_init_browser())
        return Response(_run(PAGE.content()), mimetype="text/plain")
    except Exception as e:
        log.exception("source endpoint failed")
        # 500 を 200＋エラー内容に変換
        return Response(f"ERROR: Failed to get page source: {str(e)}", mimetype="text/plain")


@app.get("/screenshot")
def screenshot():
    try:
        _run(_init_browser())
        img = _run(PAGE.screenshot(type="png"))
        return Response(base64.b64encode(img), mimetype="text/plain")
    except Exception as e:
        log.exception("screenshot endpoint failed")
        # スクリーンショット失敗時も 200 で空画像またはエラーメッセージを返す
        error_msg = f"ERROR: Failed to capture screenshot: {str(e)}"
        return Response(error_msg, mimetype="text/plain")


@app.get("/elements")
def elements():
    try:
        _run(_init_browser())
        data = _run(_list_elements())
        return jsonify(data)
    except Exception as e:
        log.exception("elements endpoint failed")
        # 要素リスト取得失敗時も 200 で空配列とエラー情報を返す
        return jsonify({"error": f"Failed to list elements: {str(e)}", "elements": []})


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
