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

# Configurable timeouts and retry settings
ACTION_TIMEOUT = int(os.getenv("ACTION_TIMEOUT", "10000"))  # ms  個別アクション猶予
NAVIGATION_TIMEOUT = int(os.getenv("NAVIGATION_TIMEOUT", "30000"))  # ms  ナビゲーション専用
WAIT_FOR_SELECTOR_TIMEOUT = int(os.getenv("WAIT_FOR_SELECTOR_TIMEOUT", "5000"))  # ms  セレクタ待機
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
LOCATOR_RETRIES = int(os.getenv("LOCATOR_RETRIES", "3"))
CDP_URL = "http://localhost:9222"
DEFAULT_URL = os.getenv("START_URL", "https://yahoo.co.jp")
SPA_STABILIZE_TIMEOUT = int(
    os.getenv("SPA_STABILIZE_TIMEOUT", "2000")
)  # ms  SPA描画安定待ち
MAX_DSL_ACTIONS = int(os.getenv("MAX_DSL_ACTIONS", "50"))  # DSL アクション数上限


# Security and navigation configuration
ALLOWED_DOMAINS = os.getenv("ALLOWED_DOMAINS", "").split(",") if os.getenv("ALLOWED_DOMAINS") else []
BLOCKED_DOMAINS = os.getenv("BLOCKED_DOMAINS", "").split(",") if os.getenv("BLOCKED_DOMAINS") else []
MAX_REDIRECTS = int(os.getenv("MAX_REDIRECTS", "10"))


def _is_domain_allowed(url: str) -> tuple[bool, str]:
    """Check if domain is allowed for navigation."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        
        # Check blocked domains first
        if BLOCKED_DOMAINS:
            for blocked in BLOCKED_DOMAINS:
                if blocked and (domain == blocked.lower() or domain.endswith(f".{blocked.lower()}")):
                    return False, f"Domain {domain} is blocked"
        
        # If allowlist is configured, check it
        if ALLOWED_DOMAINS:
            allowed = False
            for allowed_domain in ALLOWED_DOMAINS:
                if allowed_domain and (domain == allowed_domain.lower() or domain.endswith(f".{allowed_domain.lower()}")):
                    allowed = True
                    break
            if not allowed:
                return False, f"Domain {domain} is not in allowlist"
        
        return True, ""
    except Exception as e:
        return False, f"Could not parse domain from URL: {str(e)}"


def _classify_error(error_str: str) -> tuple[str, bool]:
    """Classify error as internal/external and provide user-friendly message."""
    error_lower = error_str.lower()
    
    # Network/DNS errors (external)
    if any(x in error_lower for x in ["dns", "connection", "timeout", "network", "err_name_not_resolved"]):
        return "ネットワークエラー - サイトに接続できません", False
    
    # HTTP errors (external)
    if "403" in error_str or "forbidden" in error_lower:
        return "アクセス拒否 - サイトがアクセスを拒否しました", False
    if "404" in error_str or "not found" in error_lower:
        return "ページが見つかりません", False
    if "500" in error_str or "internal server error" in error_lower:
        return "サイトの内部エラー", False
    
    # Element/interaction errors (actionable)
    if "element not found" in error_lower or "locator not found" in error_lower:
        return "要素が見つかりませんでした - セレクタを確認するか、ページの読み込みを待ってください", True
    if "timeout" in error_lower:
        return "操作がタイムアウトしました - ページの応答が遅い可能性があります", True
    if "not enabled" in error_lower or "not visible" in error_lower:
        return "要素が操作できません - 要素が無効化されているか見えない状態です", True
    
    # Default classification as internal
    return f"内部処理エラー - {error_str}", True

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


def _validate_schema(data: Dict) -> None:
    errs = sorted(validator.iter_errors(data), key=lambda e: e.path)
    if errs:
        raise ValidationError("; ".join(err.msg for err in errs))


def _validate_url(url: str) -> bool:
    """Validate that URL is non-empty and properly formatted."""
    if not url or not url.strip():
        return False
    try:
        parsed = urlparse(url.strip())
        return bool(parsed.scheme and parsed.netloc)
    except Exception:
        return False


def _validate_selector(selector: str) -> bool:
    """Validate that selector is non-empty."""
    return bool(selector and selector.strip())


def _validate_action_params(act: Dict) -> List[str]:
    """Validate action parameters and return list of validation warnings."""
    warnings = []
    action = act.get("action")
    
    if action == "navigate":
        url = act.get("target", "")
        if not _validate_url(url):
            warnings.append(f"ERROR:auto:Invalid navigate URL '{url}' - URL must be non-empty and properly formatted")
    
    elif action == "wait_for_selector":
        selector = act.get("target", "")
        if not _validate_selector(selector):
            warnings.append(f"ERROR:auto:Invalid selector '{selector}' - Selector must be non-empty")
    
    elif action in ["click", "click_text", "type", "hover", "select_option", "press_key", "extract_text"]:
        selector = act.get("target", "")
        if not _validate_selector(selector):
            warnings.append(f"ERROR:auto:Invalid selector '{selector}' for action '{action}' - Selector must be non-empty")
    
    # Validate timeout values
    ms = act.get("ms")
    if ms is not None:
        try:
            ms_val = int(ms)
            if ms_val < 0:
                warnings.append(f"ERROR:auto:Invalid timeout {ms} - Must be non-negative")
        except (ValueError, TypeError):
            warnings.append(f"ERROR:auto:Invalid timeout {ms} - Must be a valid integer")
    
    # Validate retry count
    retry = act.get("retry")
    if retry is not None:
        try:
            retry_val = int(retry)
            if retry_val < 1:
                warnings.append(f"ERROR:auto:Invalid retry count {retry} - Must be at least 1")
        except (ValueError, TypeError):
            warnings.append(f"ERROR:auto:Invalid retry count {retry} - Must be a valid integer")
    
    return warnings


# -------------------------------------------------- Playwright 管理
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

PW = BROWSER = PAGE = None
EXTRACTED_TEXTS: List[str] = []
EVAL_RESULTS: List[str] = []
WARNINGS: List[str] = []

# Execution lock to prevent concurrent DSL execution
_EXECUTION_LOCK = asyncio.Lock()

# Debug artifacts directory
DEBUG_DIR = os.getenv("DEBUG_DIR", "./debug_artifacts")
SAVE_DEBUG_ARTIFACTS = os.getenv("SAVE_DEBUG_ARTIFACTS", "true").lower() == "true"

# Browser context management
USE_INCOGNITO_CONTEXT = os.getenv("USE_INCOGNITO_CONTEXT", "false").lower() == "true"


async def _create_clean_context():
    """Create a new browser context (optionally incognito) for clean state."""
    global PAGE
    
    if not BROWSER:
        await _init_browser()
        return
    
    if USE_INCOGNITO_CONTEXT:
        # Create new incognito context
        try:
            context = await BROWSER.new_context()
            PAGE = await context.new_page()
            
            # Inject watcher script
            if _WATCHER_SCRIPT:
                try:
                    await PAGE.add_init_script(_WATCHER_SCRIPT)
                except Exception as e:
                    log.error("add_init_script failed: %s", e)
                    
            log.info("Created new incognito context")
        except Exception as e:
            log.error("Failed to create incognito context: %s", e)
            # Fallback to existing page
            pass


async def _save_debug_artifacts(correlation_id: str, error_context: str = "") -> str:
    """Save screenshot and HTML for debugging purposes."""
    if not SAVE_DEBUG_ARTIFACTS or not PAGE:
        return ""
    
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        
        # Save screenshot
        screenshot_path = os.path.join(DEBUG_DIR, f"{correlation_id}_screenshot.png")
        try:
            screenshot = await PAGE.screenshot(type="png")
            with open(screenshot_path, "wb") as f:
                f.write(screenshot)
        except Exception as e:
            log.warning("Could not save screenshot: %s", e)
        
        # Save HTML
        html_path = os.path.join(DEBUG_DIR, f"{correlation_id}_page.html")
        try:
            html = await PAGE.content()
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception as e:
            log.warning("Could not save HTML: %s", e)
        
        # Save error context
        if error_context:
            error_path = os.path.join(DEBUG_DIR, f"{correlation_id}_error.txt")
            try:
                with open(error_path, "w", encoding="utf-8") as f:
                    f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"Correlation ID: {correlation_id}\n")
                    f.write(f"Error Context:\n{error_context}\n")
            except Exception as e:
                log.warning("Could not save error context: %s", e)
        
        return f"Debug artifacts saved with ID: {correlation_id}"
        
    except Exception as e:
        log.error("Failed to save debug artifacts: %s", e)
        return ""


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


async def _check_browser_health() -> bool:
    """Check if browser and page are still functional."""
    try:
        if PAGE is None or BROWSER is None:
            return False
        
        # Simple health check - try to evaluate a basic script
        await PAGE.evaluate("() => document.readyState")
        return True
    except Exception as e:
        log.warning("Browser health check failed: %s", e)
        return False


async def _recreate_browser():
    """Recreate browser and page when health check fails."""
    global PW, BROWSER, PAGE
    
    try:
        if PAGE:
            try:
                await PAGE.close()
            except Exception:
                pass
        if BROWSER:
            try:
                await BROWSER.close()
            except Exception:
                pass
        if PW:
            try:
                await PW.stop()
            except Exception:
                pass
    except Exception:
        pass
    
    # Reset globals
    PW = BROWSER = PAGE = None
    
    # Reinitialize
    await _init_browser()
async def _init_browser():
    global PW, BROWSER, PAGE
    if PAGE and await _check_browser_health():
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
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                _WATCHER_SCRIPT = f.read()
    
    if _WATCHER_SCRIPT:
        try:
            await PAGE.add_init_script(_WATCHER_SCRIPT)
        except Exception as e:
            log.error("add_init_script failed: %s", e)

    try:
        await PAGE.goto(DEFAULT_URL, wait_until="load", timeout=NAVIGATION_TIMEOUT)
    except Exception as e:
        log.warning("Failed to navigate to default URL: %s", e)
        
    log.info("browser ready")


# -------------------------------------------------- アクション実装
async def _prepare_element(loc, timeout=None):
    """Ensure the element is visible, enabled and ready for interaction."""
    if timeout is None:
        timeout = ACTION_TIMEOUT
    
    # Wait for element to be attached
    await loc.first.wait_for(state="attached", timeout=timeout)
    
    # Scroll into view if needed
    await loc.first.scroll_into_view_if_needed(timeout=timeout)
    
    # Wait for visibility
    await loc.first.wait_for(state="visible", timeout=timeout)
    
    # Additional check for interactability
    if not await loc.first.is_enabled():
        raise Exception("Element is not enabled for interaction")


async def _safe_click(l, force=False, timeout=None):
    """Enhanced safe clicking with multiple fallback strategies."""
    if timeout is None:
        timeout = ACTION_TIMEOUT
        
    try:
        await _prepare_element(l, timeout)
        
        # Try hover first to ensure element is ready
        await l.first.hover(timeout=timeout)
        await asyncio.sleep(0.1)  # Short pause after hover
        
        await l.first.click(timeout=timeout, force=force)
        
    except Exception as e:
        if not force:
            log.warning("Click retry with force due to: %s", e)
            try:
                await l.first.click(timeout=timeout, force=True)
            except Exception as force_error:
                # Try JavaScript click as last resort
                try:
                    await l.first.evaluate("el => el.click()")
                except Exception:
                    raise force_error
        else:
            raise


async def _safe_fill(l, val: str, timeout=None):
    """Enhanced safe filling with multiple strategies."""
    if timeout is None:
        timeout = ACTION_TIMEOUT
        
    try:
        await _prepare_element(l, timeout)
        
        # Clear existing content first
        await l.first.click(timeout=timeout)
        await l.first.fill("", timeout=timeout)
        
        # Fill new content
        await l.first.fill(val, timeout=timeout)
        
        # Verify the content was set
        current_val = await l.first.input_value()
        if current_val != val:
            # Try alternative method using keyboard
            await l.first.click(timeout=timeout)
            await l.first.press("Control+a")
            await l.first.type(val, delay=50)
            
    except Exception as e:
        log.warning("Fill retry with alternative method due to: %s", e)
        try:
            # Alternative: click first, then fill
            await l.first.click(timeout=timeout)
            await l.first.fill(val, timeout=timeout)
        except Exception as retry_error:
            # Last resort: JavaScript set value
            try:
                await l.first.evaluate(f"el => el.value = '{val}'")
                await l.first.dispatch_event("input")
            except Exception:
                raise retry_error


async def _safe_hover(l, timeout=None):
    """Enhanced safe hovering."""
    if timeout is None:
        timeout = ACTION_TIMEOUT
        
    await _prepare_element(l, timeout)
    await l.first.hover(timeout=timeout)


async def _safe_select(l, val: str, timeout=None):
    """Enhanced safe option selection."""
    if timeout is None:
        timeout = ACTION_TIMEOUT
        
    await _prepare_element(l, timeout)
    await l.first.select_option(val, timeout=timeout)


async def _safe_press(l, key: str, timeout=None):
    """Enhanced safe key pressing."""
    if timeout is None:
        timeout = ACTION_TIMEOUT
        
    await _prepare_element(l, timeout)
    await l.first.press(key, timeout=timeout)


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
        # ネットワーク要求が終わるまで待機
        await PAGE.wait_for_load_state("networkidle", timeout=SPA_STABILIZE_TIMEOUT)
    except Exception:
        pass
    await _wait_dom_idle(SPA_STABILIZE_TIMEOUT)


async def _apply(act: Dict) -> List[str]:
    """Execute a single action and return warnings instead of raising exceptions."""
    global PAGE
    action_warnings = []
    
    a = act["action"]
    tgt = act.get("target", "")
    if isinstance(tgt, list):
        tgt = " || ".join(str(s).strip() for s in tgt if s)
    val = act.get("value", "")
    ms = int(act.get("ms", 0))
    amt = int(act.get("amount", 400))
    dir_ = act.get("direction", "down")

    try:
        # -- navigate / wait / scroll はロケータ不要
        if a == "navigate":
            if not _validate_url(tgt):
                action_warnings.append(f"WARNING:auto:Skipping navigate - Invalid URL: {tgt}")
                return action_warnings
                
            # Security check for domain allowlist/blocklist
            domain_allowed, domain_msg = _is_domain_allowed(tgt)
            if not domain_allowed:
                action_warnings.append(f"ERROR:auto:Navigation blocked - {domain_msg}")
                return action_warnings
                
            timeout = NAVIGATION_TIMEOUT if ms == 0 else ms
            try:
                await PAGE.goto(tgt, wait_until="load", timeout=timeout)
                # Enhanced post-navigation stabilization
                await _stabilize_page()
            except Exception as e:
                friendly_msg, is_internal = _classify_error(str(e))
                action_warnings.append(f"WARNING:auto:Navigation failed - {friendly_msg}")
            return action_warnings
            
        if a == "go_back":
            await PAGE.go_back(wait_until="load", timeout=NAVIGATION_TIMEOUT)
            await _stabilize_page()
            return action_warnings
            
        if a == "go_forward":
            await PAGE.go_forward(wait_until="load", timeout=NAVIGATION_TIMEOUT)
            await _stabilize_page()
            return action_warnings
            
        if a == "wait":
            timeout = ms if ms > 0 else 1000
            await PAGE.wait_for_timeout(timeout)
            return action_warnings
            
        if a == "wait_for_selector":
            if not _validate_selector(tgt):
                action_warnings.append(f"WARNING:auto:Skipping wait_for_selector - Empty selector")
                return action_warnings
            timeout = ms if ms > 0 else WAIT_FOR_SELECTOR_TIMEOUT
            try:
                await PAGE.wait_for_selector(tgt, state="visible", timeout=timeout)
            except Exception as e:
                action_warnings.append(f"WARNING:auto:wait_for_selector failed for '{tgt}' - {str(e)}")
            return action_warnings
            
        if a == "scroll":
            offset = amt if dir_ == "down" else -amt
            if tgt:
                try:
                    await PAGE.locator(tgt).evaluate("(el,y)=>el.scrollBy(0,y)", offset)
                except Exception as e:
                    action_warnings.append(f"WARNING:auto:scroll failed for '{tgt}' - {str(e)}")
            else:
                await PAGE.evaluate("(y)=>window.scrollBy(0,y)", offset)
            return action_warnings
            
        if a == "eval_js":
            script = act.get("script") or val
            if script:
                try:
                    result = await PAGE.evaluate(script)
                    EVAL_RESULTS.append(result)
                except Exception as e:
                    action_warnings.append(f"WARNING:auto:eval_js failed - {str(e)}")
            return action_warnings

        # -- ロケータ系
        if not _validate_selector(tgt):
            action_warnings.append(f"WARNING:auto:Skipping {a} - Empty selector")
            return action_warnings

        loc: Optional = None
        for attempt in range(LOCATOR_RETRIES):
            try:
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
            except Exception as e:
                if attempt == LOCATOR_RETRIES - 1:
                    action_warnings.append(f"WARNING:auto:Locator search failed for '{tgt}' - {str(e)}")

        if loc is None:
            action_warnings.append(f"WARNING:auto:Element not found: {tgt}. Consider using alternative selectors or text matching.")
            return action_warnings

        # Execute the action with enhanced error handling
        try:
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
                else:
                    # Fallback to page-level keypress
                    if key:
                        await PAGE.keyboard.press(key)
                    else:
                        action_warnings.append(f"WARNING:auto:No key specified for press_key action")
            elif a == "extract_text":
                try:
                    attr = act.get("attr")
                    if attr:
                        text = await loc.get_attribute(attr)
                    else:
                        text = await loc.inner_text()
                    EXTRACTED_TEXTS.append(text or "")
                except Exception as e:
                    action_warnings.append(f"WARNING:auto:extract_text failed - {str(e)}")
        except Exception as e:
            action_warnings.append(f"WARNING:auto:{a} operation failed for '{tgt}' - {str(e)}")

    except Exception as e:
        action_warnings.append(f"WARNING:auto:Action '{a}' failed - {str(e)}")
    
    return action_warnings


async def _run_actions_with_lock(actions: List[Dict], correlation_id: str = "") -> tuple[str, List[str]]:
    """Run actions with execution lock to prevent concurrent execution issues."""
    async with _EXECUTION_LOCK:
        return await _run_actions(actions, correlation_id)


async def _run_actions(actions: List[Dict], correlation_id: str = "") -> tuple[str, List[str]]:
    all_warnings = []
    
    for i, act in enumerate(actions):
        # Enhanced DOM stabilization before each action
        await _stabilize_page()
        
        retries = int(act.get("retry", MAX_RETRIES))
        action_executed = False
        
        for attempt in range(1, retries + 1):
            try:
                action_warnings = await _apply(act)
                all_warnings.extend(action_warnings)
                action_executed = True
                
                # Enhanced DOM stabilization after each action
                await _stabilize_page()
                break
                
            except Exception as e:
                error_msg = f"Action {i+1} '{act.get('action', 'unknown')}' attempt {attempt}/{retries} failed: {str(e)}"
                log.error("[%s] %s", correlation_id, error_msg)
                
                if attempt == retries:
                    # Final retry failure - add to warnings instead of raising exception
                    friendly_msg, is_internal = _classify_error(str(e))
                    all_warnings.append(f"ERROR:auto:[{correlation_id}] {friendly_msg}")
                    
                    # Save debug artifacts for critical failures
                    if is_internal and SAVE_DEBUG_ARTIFACTS:
                        debug_info = await _save_debug_artifacts(f"{correlation_id}_action_{i+1}", error_msg)
                        if debug_info:
                            all_warnings.append(f"DEBUG:auto:{debug_info}")
                else:
                    # Wait with exponential backoff before retry
                    wait_time = min(1000 * (2 ** (attempt - 1)), 5000)  # Cap at 5 seconds
                    await asyncio.sleep(wait_time / 1000)
        
        # If action couldn't be executed due to critical errors, note it
        if not action_executed:
            all_warnings.append(f"WARNING:auto:[{correlation_id}] Action {i+1} '{act.get('action', 'unknown')}' was skipped due to errors")
    
    try:
        html = await PAGE.content()
    except Exception as e:
        html = ""
        all_warnings.append(f"WARNING:auto:Could not retrieve final page content - {str(e)}")
    
    return html, all_warnings


# -------------------------------------------------- HTTP エンドポイント
@app.post("/execute-dsl")
def execute_dsl():
    # Generate correlation ID for request tracking
    correlation_id = str(uuid.uuid4())[:8]
    log.info("Starting DSL execution with correlation ID: %s", correlation_id)
    
    warnings = []
    
    try:
        data = request.get_json(force=True)
        # 配列だけ来た場合の後方互換
        if isinstance(data, list):
            data = {"actions": data}
        _validate_schema(data)
        
        # Validate individual actions
        for i, action in enumerate(data.get("actions", [])):
            action_warnings = _validate_action_params(action)
            if action_warnings:
                # Add correlation ID and action index to warnings
                for warning in action_warnings:
                    warnings.append(f"[{correlation_id}] Action {i+1}: {warning}")
        
    except ValidationError as ve:
        warning_msg = f"[{correlation_id}] ERROR:auto:InvalidDSL - {str(ve)}"
        warnings.append(warning_msg)
        return jsonify({"html": "", "warnings": warnings, "correlation_id": correlation_id})
    except Exception as e:
        warning_msg = f"[{correlation_id}] ERROR:auto:ParseError - {str(e)}"
        warnings.append(warning_msg)
        return jsonify({"html": "", "warnings": warnings, "correlation_id": correlation_id})

    # If there are validation warnings for critical actions, skip execution
    critical_errors = [w for w in warnings if "Invalid navigate URL" in w or "Invalid selector" in w]
    if critical_errors:
        log.warning("Skipping execution due to critical validation errors: %s", critical_errors)
        return jsonify({"html": "", "warnings": warnings, "correlation_id": correlation_id})
    
    # Check DSL size limit
    action_count = len(data.get("actions", []))
    if action_count > MAX_DSL_ACTIONS:
        warning_msg = f"[{correlation_id}] ERROR:auto:DSL too large - {action_count} actions exceed limit of {MAX_DSL_ACTIONS}. Consider splitting into smaller chunks."
        warnings.append(warning_msg)
        # Truncate to limit but still execute  
        data["actions"] = data["actions"][:MAX_DSL_ACTIONS]

    try:
        _run(_init_browser())
        
        # Check browser health before execution
        if not _run(_check_browser_health()):
            log.warning("[%s] Browser unhealthy, recreating...", correlation_id)
            _run(_recreate_browser())
        
        # Optionally create clean context
        if USE_INCOGNITO_CONTEXT:
            _run(_create_clean_context())
            
        html, action_warns = _run(_run_actions_with_lock(data["actions"], correlation_id))
        warnings.extend(action_warns)
        
        return jsonify({"html": html, "warnings": warnings, "correlation_id": correlation_id})
        
    except Exception as e:
        error_msg = f"[{correlation_id}] ExecutionError - {str(e)}"
        log.exception("DSL execution failed: %s", error_msg)
        warnings.append(f"ERROR:auto:{error_msg}")
        
        # Save debug artifacts on critical failure
        debug_info = _run(_save_debug_artifacts(correlation_id, error_msg))
        if debug_info:
            warnings.append(f"DEBUG:auto:{debug_info}")
        
        # Return current page HTML even on failure to provide context
        try:
            html = _run(PAGE.content()) if PAGE else ""
        except Exception:
            html = ""
            
        return jsonify({"html": html, "warnings": warnings, "correlation_id": correlation_id})


@app.get("/source")
def source():
    try:
        _run(_init_browser())
        return Response(_run(PAGE.content()), mimetype="text/plain")
    except Exception as e:
        log.error("source error: %s", e)
        return Response("", mimetype="text/plain")


@app.get("/screenshot")
def screenshot():
    try:
        _run(_init_browser())
        img = _run(PAGE.screenshot(type="png"))
        return Response(base64.b64encode(img), mimetype="text/plain")
    except Exception as e:
        log.error("screenshot error: %s", e)
        return Response("", mimetype="text/plain")


@app.get("/elements")
def elements():
    try:
        _run(_init_browser())
        data = _run(_list_elements())
        return jsonify(data)
    except Exception as e:
        log.error("elements error: %s", e)
        return jsonify([])


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
