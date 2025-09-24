# vnc/automation_server.py
from __future__ import annotations

import asyncio
import atexit
import base64
import hashlib
import inspect
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse, urlsplit, urlunsplit


import httpx
from flask import Flask, Response, jsonify, request
from jsonschema import Draft7Validator, ValidationError
from playwright.async_api import Error as PwError, Locator, async_playwright

from vnc.locator_utils import SmartLocator  # 同ディレクトリ
from vnc.executor import RunExecutor
from vnc.config import load_config
from vnc.safe_interactions import (
    prepare_locator,
    safe_click,
    safe_fill,
    safe_hover,
    safe_press,
    safe_select,
)
from vnc.page_stability import (
    safe_get_page_content,
    stabilize_page,
    wait_dom_idle,
    wait_for_loading_indicators,
    wait_for_page_ready,
)
from vnc.page_actions import (
    click_blank_area as perform_click_blank_area,
    close_popup as perform_close_popup,
    eval_js as run_eval_js,
    scroll_to_text as perform_scroll_to_text,
)
from vnc.dependency_check import ensure_component_dependencies

from agent.browser_use_runner import BrowserUseManager
from agent.utils.shared_browser import (
    format_shared_browser_error,
    normalise_cdp_websocket,
)

# -------------------------------------------------- 基本設定
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("auto")

# Validate that runtime dependencies match the declared requirements.  This
# surfaces missing packages (e.g. jsonschema) as a clear error instead of
# failing later during request handling.
ensure_component_dependencies("vnc", logger=log)


_browser_use_manager: BrowserUseManager | None = None

_DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemini")
_DEFAULT_MAX_STEPS = max(1, int(os.getenv("MAX_STEPS", "15")))


def _get_browser_use_manager() -> BrowserUseManager:
    global _browser_use_manager
    if _browser_use_manager is None:
        _browser_use_manager = BrowserUseManager()
    return _browser_use_manager


@atexit.register
def _shutdown_browser_use_manager() -> None:  # pragma: no cover - shutdown path
    manager = _browser_use_manager
    if manager is None:
        return
    try:
        manager.shutdown()
    except Exception as exc:
        log.debug("Browser-use manager shutdown failed: %s", exc)


@app.errorhandler(500)
def internal_server_error(error):
    """Global error handler to convert 500 errors to JSON warnings."""
    correlation_id = str(uuid.uuid4())[:8]
    error_msg = f"Internal server error - {str(error)}"
    log.exception("[%s] Unhandled exception: %s", correlation_id, error_msg)
    
    return jsonify({
        "html": "", 
        "warnings": [f"ERROR:auto:[{correlation_id}] Internal failure - An unexpected error occurred"],
        "correlation_id": correlation_id
    }), 200  # Return 200 instead of 500


@app.errorhandler(Exception)
def handle_exception(error):
    """Global exception handler to catch all uncaught exceptions."""
    correlation_id = str(uuid.uuid4())[:8]
    log.exception("[%s] Uncaught exception: %s", correlation_id, str(error))
    
    return jsonify({
        "html": "",
        "warnings": [f"ERROR:auto:[{correlation_id}] Internal failure - {str(error)}"],
        "correlation_id": correlation_id
    }), 200  # Return 200 instead of 500

# Configurable timeouts and retry settings
ACTION_TIMEOUT = int(os.getenv("ACTION_TIMEOUT", "10000"))  # ms  個別アクション猶予
NAVIGATION_TIMEOUT = int(os.getenv("NAVIGATION_TIMEOUT", "30000"))  # ms  ナビゲーション専用
WAIT_FOR_SELECTOR_TIMEOUT = int(os.getenv("WAIT_FOR_SELECTOR_TIMEOUT", "5000"))  # ms  セレクタ待機
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
LOCATOR_RETRIES = int(os.getenv("LOCATOR_RETRIES", "3"))
# CDP detection helpers ensure the automation server attaches to the browser
# instance that backs the live VNC view.  This avoids launching a separate
# headless instance when a shared Chromium with remote debugging is already
# running.
_CDP_ENV_VARS = ("VNC_CDP_URL", "BROWSER_USE_CDP_URL", "CDP_URL")
_CDP_DEFAULT_ENDPOINTS = (
    "http://127.0.0.1:9222",
    "http://localhost:9222",
    "http://vnc:9222",
)
_CDP_FALLBACK = "http://127.0.0.1:9222"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _dedupe_candidates(*groups: Iterable[str | None]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()

    for group in groups:
        if not group:
            continue
        for candidate in group:
            normalised = _normalise_cdp_candidate(candidate)
            if not normalised or normalised in seen:
                continue
            merged.append(normalised)
            seen.add(normalised)

    return merged


def _candidate_hostname(candidate: str) -> str:
    candidate = (candidate or "").strip()
    if not candidate:
        return ""
    parsed = urlsplit(candidate if "://" in candidate else f"http://{candidate}")
    return parsed.hostname or ""


def _candidate_port(candidate: str) -> Optional[int]:
    candidate = (candidate or "").strip()
    if not candidate:
        return None
    parsed = urlsplit(candidate if "://" in candidate else f"http://{candidate}")
    return parsed.port


def _normalise_cdp_candidate(value: Optional[str]) -> str:
    if not value:
        return ""
    trimmed = value.strip()
    if not trimmed:
        return ""
    lowered = trimmed.lower()
    if lowered.startswith(("http://", "https://", "ws://", "wss://")):
        return trimmed.rstrip("/")
    if trimmed.startswith("//"):
        return f"http:{trimmed}".rstrip("/")
    if ":" in trimmed:
        return f"http://{trimmed}".rstrip("/")
    return trimmed


def _initial_cdp_endpoint() -> str:
    for env_name in _CDP_ENV_VARS:
        candidate = _normalise_cdp_candidate(os.getenv(env_name))
        if candidate:
            return candidate
    return _CDP_FALLBACK


def _candidate_cdp_endpoints() -> List[str]:
    candidates: List[str] = []
    seen: set[str] = set()

    def _add(value: Optional[str]) -> None:
        normalised = _normalise_cdp_candidate(value)
        if normalised and normalised not in seen:
            candidates.append(normalised)
            seen.add(normalised)

    _add(_initial_cdp_endpoint())
    for env_name in _CDP_ENV_VARS:
        _add(os.getenv(env_name))
    for default in _CDP_DEFAULT_ENDPOINTS:
        _add(default)
    return candidates


def _json_version_url(endpoint: str) -> str:
    endpoint = (endpoint or "").strip()
    if not endpoint:
        return ""

    parsed = urlsplit(endpoint)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc
    path = parsed.path or ""

    if not scheme:
        return _json_version_url(f"http://{endpoint}")

    if not netloc and path:
        netloc = path
        path = ""

    if scheme in {"ws", "wss"}:
        http_scheme = "http" if scheme == "ws" else "https"
        trimmed = path.rstrip("/")
        lowered = trimmed.lower()
        if lowered.endswith("/devtools/browser"):
            trimmed = trimmed[: lowered.rfind("/devtools/browser")]
        if lowered.endswith("/json/version"):
            final_path = trimmed
        elif trimmed:
            final_path = f"{trimmed}/json/version"
        else:
            final_path = "/json/version"
        return urlunsplit((http_scheme, netloc, final_path, "", ""))

    if scheme in {"http", "https"}:
        trimmed = path.rstrip("/")
        lowered = trimmed.lower()
        if lowered.endswith("/json/version"):
            final_path = trimmed
        elif trimmed:
            final_path = f"{trimmed}/json/version"
        else:
            final_path = "/json/version"
        return urlunsplit((scheme, netloc, final_path, "", ""))

    return _json_version_url(f"http://{endpoint}")


CDP_URL = _initial_cdp_endpoint()
# Use Yahoo! JAPAN as the default start page for immediate usability
DEFAULT_URL = os.getenv("START_URL", "https://www.yahoo.co.jp/")
SPA_STABILIZE_TIMEOUT = int(
    os.getenv("SPA_STABILIZE_TIMEOUT", "2000")
)  # ms  SPA描画安定待ち
MAX_DSL_ACTIONS = int(os.getenv("MAX_DSL_ACTIONS", "50"))  # DSL アクション数上限
INDEX_MODE = os.getenv("INDEX_MODE", "true").lower() == "true"
CATALOG_MAX_ELEMENTS = int(os.getenv("CATALOG_MAX_ELEMENTS", "120"))


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
    
    # Page navigation errors (internal, actionable) - check first as they're specific
    if any(x in error_lower for x in ["navigating and changing", "page is navigating"]):
        return "ページが読み込み中です - しばらく待ってから再試行してください", True
    
    # Element/interaction errors (actionable) - check next as they're most common
    if any(x in error_lower for x in ["element not found", "locator not found", "not found"]):
        return "要素が見つかりませんでした - セレクタを確認するか、ページの読み込みを待ってください", True
    if any(x in error_lower for x in ["timeout", "timed out"]) and not any(x in error_lower for x in ["dns", "connection", "network"]):
        return "操作がタイムアウトしました - ページの応答が遅い可能性があります", True
    if any(x in error_lower for x in ["not enabled", "not visible", "not interactable"]):
        return "要素が操作できません - 要素が無効化されているか見えない状態です", True
    
    # Network/DNS errors (external)
    if any(x in error_lower for x in ["dns", "connection", "network", "err_name_not_resolved", "net::"]):
        return "ネットワークエラー - サイトに接続できません", False
    
    # HTTP errors (external)
    if "403" in error_str or "forbidden" in error_lower:
        return "アクセス拒否 - サイトがアクセスを拒否しました", False
    if "404" in error_str or ("not found" in error_lower and any(x in error_lower for x in ["page", "file", "resource"])):
        return "ページが見つかりません", False
    if "500" in error_str or "internal server error" in error_lower:
        return "サイトの内部エラー", False
    
    # Default classification as internal
    return f"内部処理エラー - {error_str}", True


_CATALOG_LOCK = asyncio.Lock()
_CURRENT_CATALOG: Optional[Dict[str, Any]] = None
_CURRENT_CATALOG_SIGNATURE: Optional[Dict[str, Any]] = None
_INDEX_ADOPTION_HISTORY: List[Tuple[str, int, str, str]] = []
_MAX_ADOPTION_HISTORY = 50


CATALOG_COLLECTION_SCRIPT = """
(() => {
  const results = [];
  const interactiveSelectors = [
    'a[href]',
    'button',
    'input:not([type="hidden"])',
    'select',
    'textarea',
    'summary',
    '[contenteditable="true"]',
    '[role="button"]',
    '[role="link"]',
    '[role="tab"]',
    '[role="menuitem"]',
    '[role="option"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="textbox"]'
  ];

  const tags = new Set();
  interactiveSelectors.forEach(sel => {
    for (const el of document.querySelectorAll(sel)) {
      tags.add(el);
    }
  });

  const isVisible = (el) => {
    if (!(el instanceof Element)) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    return rect.width > 0 && rect.height > 0;
  };

  const getRole = (el) => {
    const ariaRole = el.getAttribute('role');
    if (ariaRole) return ariaRole.trim().toLowerCase();
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute('type');
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'summary') return 'button';
    if (tag === 'input') {
      if (type === 'submit' || type === 'button' || type === 'reset') return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      return 'textbox';
    }
    return '';
  };

  const getLabelFromIds = (el) => {
    const labelledby = el.getAttribute('aria-labelledby');
    if (!labelledby) return '';
    const ids = labelledby.split(/\s+/).filter(Boolean);
    const parts = [];
    ids.forEach(id => {
      const ref = document.getElementById(id);
      if (ref) {
        const text = ref.innerText || ref.textContent;
        if (text) parts.push(text.trim());
      }
    });
    return parts.join(' ').trim();
  };

  const getPrimaryLabel = (el) => {
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();
    const labelled = getLabelFromIds(el);
    if (labelled) return labelled;
    const text = (el.innerText || '').trim();
    if (text) return text;
    const value = el.value && typeof el.value === 'string' ? el.value.trim() : '';
    if (value) return value;
    const placeholder = el.getAttribute('placeholder');
    if (placeholder && placeholder.trim()) return placeholder.trim();
    const alt = el.getAttribute('alt');
    if (alt && alt.trim()) return alt.trim();
    return '';
  };

  const getSecondaryLabel = (el, primary) => {
    const placeholder = el.getAttribute('placeholder');
    if (placeholder && placeholder.trim() && placeholder.trim() !== primary) return placeholder.trim();
    const title = el.getAttribute('title');
    if (title && title.trim() && title.trim() !== primary) return title.trim();
    const ariaDescription = el.getAttribute('aria-description');
    if (ariaDescription && ariaDescription.trim()) return ariaDescription.trim();
    return '';
  };

  const getStateHint = (el) => {
    const states = [];
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') states.push('disabled');
    if (el.getAttribute('aria-selected') === 'true') states.push('selected');
    if (el.getAttribute('aria-checked') === 'true') states.push('checked');
    if (el.getAttribute('aria-expanded') === 'true') states.push('expanded');
    return states.join(', ');
  };

  const computeXPath = (el) => {
    if (el === document.body) return '/html/body';
    const parts = [];
    while (el && el.nodeType === Node.ELEMENT_NODE) {
      let index = 1;
      let sibling = el.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === el.tagName) index++;
        sibling = sibling.previousElementSibling;
      }
      parts.unshift(`/${el.tagName.toLowerCase()}[${index}]`);
      el = el.parentElement;
    }
    return parts.join('');
  };

  const cssEscape = (value) => {
    if (window.CSS && window.CSS.escape) {
      return window.CSS.escape(value);
    }
    return value.replace(/([#.;:])/g, '\\$1');
  };

  const buildSelectors = (el, role, primaryLabel) => {
    const selectors = [];
    const trimmedPrimary = primaryLabel ? primaryLabel.substring(0, 80) : '';
    if (role && trimmedPrimary) {
      const quoted = trimmedPrimary.replace(/"/g, '\\"');
      selectors.push(`role=${role}[name="${quoted}"]`);
    }
    if (trimmedPrimary) {
      selectors.push(`text=${trimmedPrimary}`);
    }
    const testId = el.getAttribute('data-testid');
    if (testId) {
      selectors.push(`css=[data-testid="${testId.replace(/"/g, '\\"')}"]`);
    }
    const elId = el.id;
    if (elId) {
      selectors.push(`css=#${cssEscape(elId)}`);
    }
    const nameAttr = el.getAttribute('name');
    if (nameAttr) {
      selectors.push(`css=[name="${nameAttr.replace(/"/g, '\\"')}"]`);
    }
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) {
      selectors.push(`css=[aria-label="${ariaLabel.replace(/"/g, '\\"')}"]`);
    }
    const classList = Array.from(el.classList || []);
    if (classList.length && classList.length <= 3) {
      selectors.push(`css=${el.tagName.toLowerCase()}.${classList.map(cssEscape).join('.')}`);
    }
    selectors.push(`xpath=${computeXPath(el)}`);
    const uniqueSelectors = [];
    const seen = new Set();
    selectors.forEach(sel => {
      if (sel && !seen.has(sel)) {
        seen.add(sel);
        uniqueSelectors.push(sel);
      }
    });
    return uniqueSelectors;
  };

  const findSection = (el) => {
    let current = el;
    const sectionTags = ['section', 'article', 'nav', 'main', 'aside', 'form', 'fieldset', 'details'];
    while (current) {
      if (sectionTags.includes(current.tagName?.toLowerCase())) {
        const rect = current.getBoundingClientRect();
        let headingText = '';
        const heading = current.querySelector('h1, h2, h3, h4, h5, h6');
        if (heading && heading.innerText.trim()) {
          headingText = heading.innerText.trim();
        }
        const ariaLabel = current.getAttribute('aria-label');
        if (!headingText && ariaLabel) headingText = ariaLabel.trim();
        if (!headingText && current.id) headingText = current.id;
        return {
          sectionId: headingText || sectionTags[0],
          sectionHint: headingText,
          sectionTop: rect.top
        };
      }
      current = current.parentElement;
    }
    const bodyRect = document.body.getBoundingClientRect();
    return { sectionId: 'page', sectionHint: 'Page', sectionTop: bodyRect.top };
  };

  const nearestTexts = (el, primary, secondary) => {
    const texts = [];
    if (primary) texts.push(primary);
    if (secondary && secondary !== primary) texts.push(secondary);
    const describedby = el.getAttribute('aria-describedby');
    if (describedby) {
      describedby.split(/\s+/).forEach(id => {
        const ref = document.getElementById(id);
        if (ref) {
          const text = (ref.innerText || ref.textContent || '').trim();
          if (text) texts.push(text);
        }
      });
    }
    const title = el.getAttribute('title');
    if (title) texts.push(title.trim());
    return Array.from(new Set(texts)).slice(0, 5);
  };

  const entries = [];
  for (const el of tags) {
    if (!isVisible(el)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const role = getRole(el);
    const primary = getPrimaryLabel(el);
    const secondary = getSecondaryLabel(el, primary);
    const section = findSection(el);
    const state = getStateHint(el);
    const href = el.getAttribute('href');
    entries.push({
      role,
      tag: el.tagName.toLowerCase(),
      primaryLabel: primary ? primary.substring(0, 120) : '',
      secondaryLabel: secondary ? secondary.substring(0, 120) : '',
      sectionHint: section.sectionHint || '',
      sectionId: section.sectionId || 'page',
      sectionTop: section.sectionTop || rect.top,
      stateHint: state,
      hrefFull: href || '',
      hrefShort: href ? (href.length > 80 ? `${href.substring(0, 77)}...` : href) : '',
      rect: { top: rect.top, left: rect.left, width: rect.width, height: rect.height },
      disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'),
      selectors: buildSelectors(el, role, primary),
      domPath: computeXPath(el),
      nearestTexts: nearestTexts(el, primary, secondary)
    });
  }

  entries.sort((a, b) => {
    if (Math.abs(a.sectionTop - b.sectionTop) > 4) return a.sectionTop - b.sectionTop;
    if (Math.abs(a.rect.top - b.rect.top) > 4) return a.rect.top - b.rect.top;
    return a.rect.left - b.rect.left;
  });

  const limited = entries.slice(0, %d);
  limited.forEach((entry, idx) => {
    entry.index = idx;
  });

  return {
    elements: limited,
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight
    }
  };
})()
""" % CATALOG_MAX_ELEMENTS

# Event listener tracker script will be injected on every page load
_WATCHER_SCRIPT = None


class ExecutionError(Exception):
    """Custom exception carrying structured error information."""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _trim(text: str, limit: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _build_catalog_entries(raw: Dict[str, Any], signature: Dict[str, Any]) -> Dict[str, Any]:
    elements = raw.get("elements", []) if isinstance(raw, dict) else []
    abbreviated: List[Dict[str, Any]] = []
    full: List[Dict[str, Any]] = []
    index_map: Dict[str, Dict[str, Any]] = {}

    for item in elements:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if idx is None:
            continue
        primary = _trim(item.get("primaryLabel", ""), 60)
        secondary = _trim(item.get("secondaryLabel", ""), 40)
        state = _trim(item.get("stateHint", ""), 40)
        section_hint = _trim(item.get("sectionHint", ""), 60)
        href_short = _trim(item.get("hrefShort", ""), 80)
        selectors = [s for s in item.get("selectors", []) if isinstance(s, str) and s]
        rect = item.get("rect") or {}
        bbox = {
            "x": float(rect.get("left", 0.0)),
            "y": float(rect.get("top", 0.0)),
            "width": float(rect.get("width", 0.0)),
            "height": float(rect.get("height", 0.0)),
        }
        dom_path = item.get("domPath", "")
        dom_path_hash = hashlib.sha1(dom_path.encode("utf-8", "ignore")).hexdigest() if dom_path else ""
        nearest_texts = [
            _trim(t, 80)
            for t in item.get("nearestTexts", [])
            if isinstance(t, str) and t.strip()
        ]

        abbreviated_entry = {
            "index": idx,
            "role": item.get("role", ""),
            "tag": item.get("tag", ""),
            "primary_label": primary,
            "secondary_label": secondary,
            "section_hint": section_hint,
            "state_hint": state,
            "href_short": href_short,
        }

        full_entry = {
            "index": idx,
            "role": item.get("role", ""),
            "tag": item.get("tag", ""),
            "primary_label": primary,
            "secondary_label": secondary,
            "section_hint": section_hint,
            "state_hint": state,
            "href_full": item.get("hrefFull", ""),
            "href_short": href_short,
            "robust_selectors": selectors,
            "bbox": bbox,
            "visible": True,
            "disabled": bool(item.get("disabled", False)),
            "dom_path_hash": dom_path_hash,
            "nearest_texts": nearest_texts,
            "section_id": _trim(str(item.get("sectionId", "page")), 80),
        }

        abbreviated.append(abbreviated_entry)
        full.append(full_entry)
        index_map[str(idx)] = full_entry

    return {
        "abbreviated": abbreviated,
        "full": full,
        "index_map": index_map,
    }


async def _compute_dom_signature() -> Dict[str, Any]:
    if PAGE is None:
        return {}
    try:
        url = await _get_page_url_value()
        title = await PAGE.title()
        content = await PAGE.content()
        viewport = PAGE.viewport_size or {}
        dom_hash = hashlib.sha1(content.encode("utf-8", "ignore")).hexdigest()
        viewport_hash = hashlib.sha1(
            json.dumps(viewport or {}, sort_keys=True).encode("utf-8")
        ).hexdigest()
        version_seed = f"{url}|{dom_hash}|{viewport_hash}"
        catalog_version = hashlib.sha1(version_seed.encode("utf-8")).hexdigest()
        return {
            "url": url,
            "title": title,
            "dom_hash": dom_hash,
            "viewport_hash": viewport_hash,
            "catalog_version": catalog_version,
        }
    except Exception as exc:  # pragma: no cover - defensive
        log.error("Failed to compute DOM signature: %s", exc)
        return {}


async def _generate_element_catalog(force: bool = False) -> Dict[str, Any]:
    global _CURRENT_CATALOG, _CURRENT_CATALOG_SIGNATURE
    if not INDEX_MODE:
        return {}
    if PAGE is None:
        raise ExecutionError("CATALOG_UNAVAILABLE", "Browser page not initialized")

    async with _CATALOG_LOCK:
        signature = await _compute_dom_signature()
        if not signature:
            _CURRENT_CATALOG = None
            _CURRENT_CATALOG_SIGNATURE = None
            return {}

        current_version = (_CURRENT_CATALOG_SIGNATURE or {}).get("catalog_version")
        if not force and _CURRENT_CATALOG and current_version == signature.get("catalog_version"):
            return _CURRENT_CATALOG

        raw_data = await PAGE.evaluate(CATALOG_COLLECTION_SCRIPT)
        catalog = _build_catalog_entries(raw_data, signature)
        catalog.update(signature)
        catalog["generated_at"] = time.time()
        _CURRENT_CATALOG = catalog
        _CURRENT_CATALOG_SIGNATURE = signature
        log.info(
            "Element catalog generated: version=%s entries=%d",
            signature.get("catalog_version"),
            len(catalog.get("abbreviated", [])),
        )
        return catalog


async def _ensure_catalog_signature() -> Dict[str, Any]:
    global _CURRENT_CATALOG_SIGNATURE
    if not INDEX_MODE or PAGE is None:
        return _CURRENT_CATALOG_SIGNATURE or {}
    if _CURRENT_CATALOG_SIGNATURE is None:
        _CURRENT_CATALOG_SIGNATURE = await _compute_dom_signature()
        if _CURRENT_CATALOG_SIGNATURE:
            _CURRENT_CATALOG_SIGNATURE["generated_at"] = time.time()
    return _CURRENT_CATALOG_SIGNATURE or {}


def _mark_catalog_outdated(new_signature: Optional[Dict[str, Any]] = None) -> None:
    global _CURRENT_CATALOG, _CURRENT_CATALOG_SIGNATURE
    _CURRENT_CATALOG = None
    if new_signature:
        new_signature = dict(new_signature)
        new_signature["generated_at"] = time.time()
        _CURRENT_CATALOG_SIGNATURE = new_signature


def _build_observation(nav_detected: bool = False, signature: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    sig = signature or _CURRENT_CATALOG_SIGNATURE or {}
    title = sig.get("title") or ""
    url = sig.get("url") or ""
    if title:
        short_summary = _trim(title, 120)
    elif url:
        short_summary = _trim(url, 120)
    else:
        short_summary = ""
    return {
        "url": url,
        "title": title,
        "short_summary": short_summary,
        "catalog_version": sig.get("catalog_version"),
        "nav_detected": bool(nav_detected),
    }


def _parse_index_target(target: str) -> Optional[int]:
    if not target or not isinstance(target, str):
        return None
    text = target.strip()
    if not text.lower().startswith("index="):
        return None
    try:
        return int(text.split("=", 1)[1])
    except ValueError:
        return None


def _actions_use_catalog_indices(actions: Iterable[Dict[str, Any]]) -> bool:
    for act in actions or []:
        if not isinstance(act, dict):
            continue

        candidates: List[str] = []
        target = act.get("target")
        if isinstance(target, list):
            candidates.extend(str(t) for t in target if isinstance(t, str))
        elif isinstance(target, str):
            candidates.append(target)

        value = act.get("value")
        if isinstance(value, list):
            candidates.extend(str(v) for v in value if isinstance(v, str))
        elif isinstance(value, str):
            candidates.append(value)

        for candidate in candidates:
            if _parse_index_target(candidate) is not None:
                return True

    return False


def _log_index_adoption(version: str, index: int, selector: str, action: str) -> None:
    entry = (version or "", index, selector, action)
    _INDEX_ADOPTION_HISTORY.append(entry)
    if len(_INDEX_ADOPTION_HISTORY) > _MAX_ADOPTION_HISTORY:
        _INDEX_ADOPTION_HISTORY.pop(0)
    log.info(
        "Catalog selector adoption: version=%s index=%s selector=%s action=%s",
        version,
        index,
        selector,
        action,
    )


def _resolve_index_entry(index: int) -> Tuple[List[str], Dict[str, Any]]:
    if not INDEX_MODE:
        raise ExecutionError(
            "UNSUPPORTED_ACTION",
            "Index-based targeting is disabled in this environment",
            {"index": index},
        )

    if not isinstance(index, int) or index < 0:
        raise ExecutionError("ELEMENT_NOT_FOUND", f"Invalid catalog index: {index}", {"index": index})

    if not _CURRENT_CATALOG or "index_map" not in _CURRENT_CATALOG:
        raise ExecutionError(
            "CATALOG_OUTDATED",
            "Element catalog is not available. Please execute refresh_catalog.",
            {"index": index},
        )

    entry = _CURRENT_CATALOG["index_map"].get(str(index))
    if not entry:
        raise ExecutionError(
            "ELEMENT_NOT_FOUND",
            f"Index {index} not present in current catalog",
            {"index": index},
        )

    selectors = entry.get("robust_selectors") or []
    if not selectors:
        raise ExecutionError(
            "ELEMENT_NOT_FOUND",
            f"No selectors recorded for index {index}",
            {"index": index},
        )

    return selectors, entry


def _collect_basic_signature() -> Dict[str, Any]:
    if PAGE is None:
        return {}

    def _value_from_attribute(attr_name: str) -> str:
        try:
            attr = getattr(PAGE, attr_name, None)
            if callable(attr):
                return _run(attr()) if asyncio.iscoroutinefunction(attr) else attr()
            return attr or ""
        except TypeError:
            try:
                return attr()
            except Exception:
                return ""
        except Exception:
            return ""

    url = _value_from_attribute("url") or ""
    title = _value_from_attribute("title") or ""
    signature = {"url": url, "title": title}
    if INDEX_MODE and _CURRENT_CATALOG_SIGNATURE:
        signature["catalog_version"] = _CURRENT_CATALOG_SIGNATURE.get("catalog_version")
    return signature

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
    "click_blank_area",
    "close_popup",
    "stop",
    "refresh_catalog",
    "scroll_to_text",
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
                    "reason": {"type": "string"},
                    "message": {"type": "string"},
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
        raise ValidationError("; ".join(err.message for err in errs))


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

    elif action == "stop":
        reason = act.get("reason", "")
        if not reason:
            warnings.append("ERROR:auto:Stop action requires non-empty 'reason'")

    elif action == "wait":
        wait_until = (act.get("until") or "").strip()
        if wait_until and wait_until not in {"network_idle", "selector", "timeout"}:
            warnings.append(f"ERROR:auto:Unsupported wait condition '{wait_until}'")
        if wait_until == "selector":
            selector = act.get("target") or act.get("value", "")
            if not _validate_selector(selector):
                warnings.append("ERROR:auto:wait selector condition requires non-empty selector in 'target' or 'value'")

    elif action == "scroll_to_text":
        text = act.get("target") or act.get("text") or act.get("value")
        if not text or not str(text).strip():
            warnings.append("ERROR:auto:scroll_to_text requires non-empty 'target' or 'text'")

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

# Global variable to store stop requests for user intervention
_STOP_REQUEST = None

# Debug artifacts directory
DEBUG_DIR = os.getenv("DEBUG_DIR", "./debug_artifacts")
SAVE_DEBUG_ARTIFACTS = os.getenv("SAVE_DEBUG_ARTIFACTS", "true").lower() == "true"

# Browser context management
USE_INCOGNITO_CONTEXT = os.getenv("USE_INCOGNITO_CONTEXT", "false").lower() == "true"
BROWSER_REFRESH_INTERVAL = int(os.getenv("BROWSER_REFRESH_INTERVAL", "50"))  # Refresh after N DSL executions
_DSL_EXECUTION_COUNT = 0

# Browser first initialization tracking
_BROWSER_FIRST_INIT = True


async def _check_and_refresh_browser(correlation_id: str = "") -> bool:
    """Check if browser should be refreshed and do it if needed."""
    global _DSL_EXECUTION_COUNT
    
    _DSL_EXECUTION_COUNT += 1
    
    if _DSL_EXECUTION_COUNT >= BROWSER_REFRESH_INTERVAL:
        log.info("[%s] Periodic browser refresh triggered after %d executions", 
                correlation_id, _DSL_EXECUTION_COUNT)
        
        try:
            await _recreate_browser()
            _DSL_EXECUTION_COUNT = 0  # Reset counter
            log.info("[%s] Browser refreshed successfully", correlation_id)
            return True
        except Exception as e:
            log.error("[%s] Browser refresh failed: %s", correlation_id, str(e))
            # Reset counter anyway to avoid getting stuck
            _DSL_EXECUTION_COUNT = 0
            return False
    
    return False


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
            html = await _safe_get_page_content()
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


async def _get_page_url_value() -> str:
    """Safely retrieve the current page URL for both async and property-based APIs."""
    if PAGE is None:
        return ""

    try:
        attr = getattr(PAGE, "url", "")
    except Exception as exc:
        log.debug("Unable to access page.url: %s", exc)
        return ""

    try:
        if callable(attr):
            value = attr()
            if inspect.isawaitable(value):
                value = await value
        else:
            value = attr
    except TypeError:
        # Some Playwright versions expose url as a simple property; if callable checks
        # misfire fall back to the raw attribute value.
        value = attr
    except Exception as exc:
        log.debug("Failed to resolve page URL: %s", exc)
        return ""

    if value is None:
        return ""
    return str(value)


def _get_page_url_sync() -> str:
    """Helper used by Flask routes to fetch the current page URL safely."""
    if PAGE is None:
        return ""

    try:
        return _run(_get_page_url_value())
    except RuntimeError as exc:
        # Fallback to direct attribute access if the event loop is already running.
        log.debug("Event loop busy while fetching page URL: %s", exc)
    except Exception as exc:
        log.debug("Failed to fetch page URL via event loop: %s", exc)

    attr = getattr(PAGE, "url", "")
    return attr if isinstance(attr, str) else ""


async def _wait_cdp(
    endpoint: str,
    timeout: int = 45,
    poll_interval: float = 1.0,
) -> bool:
    """Wait for a DevTools endpoint to become reachable.

    Chromium can take a noticeable amount of time to expose the DevTools
    protocol socket, especially when the desktop environment is still being
    initialised.  Extend the wait window and poll at a steady cadence so we do
    not prematurely fall back to a headless browser.
    """

    version_url = _json_version_url(endpoint)
    if not version_url:
        return False

    # Guard against misconfiguration by ensuring we at least wait a fraction
    # of a second between attempts.  This avoids tight loops if a caller passes
    # zero or negative values via the environment.
    poll_interval = max(poll_interval, 0.25)

    deadline = time.time() + max(timeout, 1)
    async with httpx.AsyncClient(timeout=2) as client:
        while time.time() < deadline:
            try:
                response = await client.get(version_url)
                if response.status_code == 200:
                    return True
            except httpx.HTTPError as exc:
                log.debug("CDP endpoint %s not ready: %s", version_url, exc)

            await asyncio.sleep(poll_interval)

    log.warning("Timed out waiting for CDP endpoint %s", version_url)
    return False


async def _fetch_cdp_metadata(endpoint: str) -> Dict[str, Any]:
    """Fetch DevTools metadata for *endpoint* if it responds."""

    version_url = _json_version_url(endpoint)
    if not version_url:
        return {}

    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(version_url)
    except httpx.HTTPError as exc:
        log.debug("Failed to fetch CDP metadata from %s: %s", version_url, exc)
        return {}

    if response.status_code != 200:
        log.debug(
            "CDP metadata request to %s returned status %s",
            version_url,
            response.status_code,
        )
        return {}

    try:
        payload = response.json()
    except ValueError:
        log.debug("CDP metadata response from %s was not valid JSON", version_url)
        return {}

    return payload if isinstance(payload, dict) else {}


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
    
    # Save current URL before closing the browser to preserve task context
    current_url = None
    if PAGE:
        try:
            current_url = await _get_page_url_value()
            # Avoid restoring internal about: pages and default/initial URLs
            if (current_url and 
                not current_url.startswith("about:") and 
                current_url != DEFAULT_URL and
                current_url.strip() != ""):
                log.info(
                    "Preserving current URL during browser recreation: %s",
                    current_url,
                )
            else:
                current_url = None
        except Exception:
            current_url = None
    
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
    
    # Reinitialize (but don't reset first init flag, as this is a recreation)
    await _init_browser()
    
    # Navigate back to preserved URL if we had one
    if current_url and PAGE:
        # Multiple retry attempts with different strategies to ensure URL restoration
        restore_attempts = [
            {"wait_until": "load", "timeout": NAVIGATION_TIMEOUT},
            {"wait_until": "domcontentloaded", "timeout": NAVIGATION_TIMEOUT // 2},
            {"wait_until": "networkidle", "timeout": NAVIGATION_TIMEOUT // 3},
        ]
        
        url_restored = False
        for i, attempt_params in enumerate(restore_attempts):
            try:
                log.info("Navigating back to preserved URL after browser recreation (attempt %d/%d): %s", 
                        i + 1, len(restore_attempts), current_url)
                await PAGE.goto(current_url, **attempt_params)
                
                # Verify we successfully navigated to the intended URL
                try:
                    final_url = await _get_page_url_value()
                    if final_url == current_url or final_url.startswith(current_url):
                        log.info("Successfully restored URL after browser recreation: %s", final_url)
                        url_restored = True
                        break
                    else:
                        log.warning("URL restoration attempt %d resulted in different URL: %s (expected: %s)", 
                                  i + 1, final_url, current_url)
                except Exception as url_check_error:
                    # If we can't verify the URL but navigation didn't throw, assume success
                    log.warning("Could not verify URL after navigation attempt %d: %s", i + 1, url_check_error)
                    url_restored = True
                    break
                    
            except Exception as e:
                log.warning("URL restoration attempt %d failed for %s: %s", i + 1, current_url, e)
                if i < len(restore_attempts) - 1:
                    # Wait briefly before next attempt
                    await asyncio.sleep(1)
        
        if not url_restored:
            log.error("Failed to restore URL after browser recreation despite multiple attempts: %s", current_url)
            # Do NOT navigate to DEFAULT_URL as fallback - stay where we are
            log.info("Browser recreation complete - remaining on current page instead of falling back to default URL")
async def _init_browser():
    global PW, BROWSER, PAGE, _BROWSER_FIRST_INIT, CDP_URL
    if PAGE and await _check_browser_health():
        return

    PW = await async_playwright().start()

    candidates = _candidate_cdp_endpoints()
    connection_errors: List[str] = []
    connected_endpoint = None
    for candidate in candidates:
        if not candidate:
            continue
        if not await _wait_cdp(candidate):
            log.debug("CDP endpoint %s not reachable", candidate)
            connection_errors.append(f"共有ブラウザ {candidate} が応答しませんでした")
            continue
        browser = None
        try:
            browser = await PW.chromium.connect_over_cdp(candidate)
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.bring_to_front()
        except PwError as exc:
            log.warning("Failed to attach to browser via %s: %s", candidate, exc)
            connection_errors.append(
                f"共有ブラウザ {candidate} への接続に失敗しました（{type(exc).__name__}: {exc}）"
            )
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            continue
        except Exception as exc:
            log.warning("Unexpected error attaching to browser via %s: %s", candidate, exc)
            connection_errors.append(
                f"共有ブラウザ {candidate} への接続に失敗しました（{type(exc).__name__}: {exc}）"
            )
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            continue

        BROWSER = browser
        PAGE = page
        connected_endpoint = candidate
        break

    if PAGE is None:
        reason = (
            connection_errors[-1]
            if connection_errors
            else "共有ブラウザの CDP エンドポイントが見つからないか応答しませんでした"
        )
        message = format_shared_browser_error(reason, candidates=candidates)
        log.error(
            "Automation server could not connect to a shared browser: %s",
            message,
        )
        try:
            if PW:
                await PW.stop()
        except Exception as stop_exc:
            log.debug("Failed to stop Playwright after connection failure: %s", stop_exc)
        finally:
            PW = None
            BROWSER = None
            PAGE = None
        raise RuntimeError(message)
    else:
        CDP_URL = connected_endpoint or CDP_URL
        if connected_endpoint:
            log.info("Connected to shared browser via %s", connected_endpoint)

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

    # Only navigate to DEFAULT_URL on the very first initialization
    if _BROWSER_FIRST_INIT:
        try:
            await PAGE.goto(DEFAULT_URL, wait_until="load", timeout=NAVIGATION_TIMEOUT)
            log.info("Initial navigation to default URL: %s", DEFAULT_URL)
        except Exception as e:
            log.warning("Failed to navigate to default URL: %s", e)
        
        _BROWSER_FIRST_INIT = False
    else:
        log.info("Browser recreated - skipping navigation to default URL")
        
    log.info("browser ready")


# -------------------------------------------------- アクション実装

async def _prepare_element(loc: Locator, timeout: Optional[int] = None) -> Locator:
    if PAGE is None:
        raise RuntimeError("Browser page is not initialized")
    timeout = timeout or ACTION_TIMEOUT
    return await prepare_locator(PAGE, loc, timeout=timeout)


async def _safe_click(
    l: Locator,
    force: bool = False,
    timeout: Optional[int] = None,
    *,
    button: str = "left",
    click_count: int = 1,
    delay_ms: Optional[int] = None,
):
    if PAGE is None:
        raise RuntimeError("Browser page is not initialized")
    timeout = timeout or ACTION_TIMEOUT
    await safe_click(
        PAGE,
        l,
        force=force,
        timeout=timeout,
        button=button,
        click_count=click_count,
        delay_ms=delay_ms,
    )


async def _safe_fill(l: Locator, val: str, timeout: Optional[int] = None, *, original_target: str = ""):
    if PAGE is None:
        raise RuntimeError("Browser page is not initialized")
    timeout = timeout or ACTION_TIMEOUT
    await safe_fill(PAGE, l, val, timeout=timeout, original_target=original_target)


async def _safe_hover(l: Locator, timeout: Optional[int] = None):
    if PAGE is None:
        raise RuntimeError("Browser page is not initialized")
    timeout = timeout or ACTION_TIMEOUT
    await safe_hover(PAGE, l, timeout=timeout)


async def _safe_select(l: Locator, val: str, timeout: Optional[int] = None):
    if PAGE is None:
        raise RuntimeError("Browser page is not initialized")
    timeout = timeout or ACTION_TIMEOUT
    await safe_select(PAGE, l, val, timeout=timeout)


async def _safe_press(l: Locator, key: str, timeout: Optional[int] = None):
    if PAGE is None:
        raise RuntimeError("Browser page is not initialized")
    timeout = timeout or ACTION_TIMEOUT
    await safe_press(PAGE, l, key, timeout=timeout)


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



async def _wait_for_page_ready(timeout: int = 3000) -> List[str]:
    if PAGE is None:
        return []
    return await wait_for_page_ready(PAGE, timeout=timeout)


async def _wait_dom_idle(timeout_ms: int = SPA_STABILIZE_TIMEOUT):
    if PAGE is None:
        return
    await wait_dom_idle(PAGE, timeout_ms=timeout_ms)


async def _wait_for_loading_indicators_to_disappear(timeout: int = 3000):
    if PAGE is None:
        return
    await wait_for_loading_indicators(PAGE, timeout=timeout)


async def _safe_get_page_content(max_retries: int = 3, delay_ms: int = 500) -> str:
    if PAGE is None:
        return ""
    return await safe_get_page_content(
        PAGE,
        max_retries=max_retries,
        delay_ms=delay_ms,
        stabilization_timeout=SPA_STABILIZE_TIMEOUT,
    )


async def _stabilize_page():
    if PAGE is None:
        return
    await stabilize_page(PAGE, timeout=SPA_STABILIZE_TIMEOUT)


async def _apply(act: Dict, is_final_retry: bool = False) -> List[str]:
    """Execute a single action. Raises exceptions for retryable errors unless is_final_retry=True."""
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
        # Check if PAGE is available for actions that need it
        page_required_actions = [
            "navigate",
            "go_back",
            "go_forward",
            "wait",
            "wait_for_selector",
            "scroll",
            "eval_js",
            "click",
            "click_text",
            "type",
            "hover",
            "select_option",
            "press_key",
            "extract_text",
            "click_blank_area",
            "close_popup",
            "refresh_catalog",
            "scroll_to_text",
        ]
        
        if a in page_required_actions and PAGE is None:
            error_msg = f"Browser not initialized - cannot execute {a} action"
            if is_final_retry:
                action_warnings.append(f"WARNING:auto:{error_msg}")
                return action_warnings
            else:
                raise Exception(error_msg)

        # -- stop action (user intervention)
        if a == "stop":
            reason = act.get("reason", "user_intervention")
            message = act.get("message", "")

            # Create a stop request that will be handled by the frontend
            stop_info = {
                "reason": reason,
                "message": message,
                "timestamp": time.time()
            }
            
            # Store the stop request globally so it can be retrieved
            global _STOP_REQUEST
            _STOP_REQUEST = stop_info
            
            # Add a warning to indicate the stop action was executed
            action_warnings.append(f"STOP:auto:Execution paused - {reason}: {message}")
            return action_warnings

        if a == "refresh_catalog":
            if not INDEX_MODE:
                action_warnings.append("INFO:auto:Index-based catalog is disabled")
                return action_warnings
            try:
                catalog = await _generate_element_catalog(force=True)
                version = (catalog or {}).get("catalog_version") or (_CURRENT_CATALOG_SIGNATURE or {}).get("catalog_version")
                action_warnings.append(
                    f"INFO:auto:Element catalog refreshed (version={version})"
                )
            except ExecutionError as exc:
                if is_final_retry:
                    action_warnings.append(f"WARNING:auto:{exc}")
                else:
                    raise
            return action_warnings

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
                # Enhanced post-navigation stabilization with automatic wait
                await _stabilize_page()
                wait_warnings = await _wait_for_page_ready()
                action_warnings.extend(wait_warnings)
            except Exception as e:
                error_msg = f"Navigation failed - {str(e)}"
                friendly_msg, is_internal = _classify_error(str(e))
                if is_final_retry:
                    action_warnings.append(f"WARNING:auto:{friendly_msg}")
                else:
                    # Not final retry, raise exception to trigger retry for internal errors
                    if is_internal:
                        raise Exception(error_msg)
                    else:
                        # External error, don't retry
                        action_warnings.append(f"WARNING:auto:{friendly_msg}")
            return action_warnings
            
        if a == "go_back":
            await PAGE.go_back(wait_until="load", timeout=NAVIGATION_TIMEOUT)
            await _stabilize_page()
            wait_warnings = await _wait_for_page_ready()
            action_warnings.extend(wait_warnings)
            return action_warnings
            
        if a == "go_forward":
            await PAGE.go_forward(wait_until="load", timeout=NAVIGATION_TIMEOUT)
            await _stabilize_page()
            wait_warnings = await _wait_for_page_ready()
            action_warnings.extend(wait_warnings)
            return action_warnings
            
        if a == "wait":
            wait_until = (act.get("until") or "").strip()
            default_ms = ms if ms > 0 else 1000

            def _coerce_timeout(value: Any, fallback: int) -> int:
                try:
                    val_int = int(value)
                    return val_int if val_int > 0 else fallback
                except (TypeError, ValueError):
                    return fallback

            if wait_until == "network_idle":
                timeout = _coerce_timeout(act.get("value"), max(default_ms, 3000))
                try:
                    await PAGE.wait_for_load_state("networkidle", timeout=timeout)
                except Exception as exc:
                    if is_final_retry:
                        action_warnings.append(f"WARNING:auto:wait network_idle failed - {str(exc)}")
                    else:
                        raise
            elif wait_until == "selector":
                selector = act.get("target") or act.get("value")
                if not selector:
                    action_warnings.append("WARNING:auto:wait selector condition missing selector")
                    return action_warnings
                try:
                    await SmartLocator(PAGE, selector).locate()
                except Exception as exc:
                    if is_final_retry:
                        action_warnings.append(f"WARNING:auto:wait selector failed - {str(exc)}")
                    else:
                        raise
            else:
                timeout = _coerce_timeout(act.get("value"), default_ms)
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
                error_msg = f"wait_for_selector failed for '{tgt}' - {str(e)}"
                if is_final_retry:
                    action_warnings.append(f"WARNING:auto:{error_msg}")
                else:
                    # Not final retry, raise exception to trigger retry
                    raise Exception(error_msg)
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

        if a == "scroll_to_text":
            text = act.get("target") or act.get("text") or act.get("value")
            if not text or not str(text).strip():
                action_warnings.append("WARNING:auto:scroll_to_text missing target text")
                return action_warnings
            try:
                result = await perform_scroll_to_text(PAGE, str(text))
            except Exception as exc:
                raise ExecutionError("ELEMENT_NOT_FOUND", f"scroll_to_text failed - {str(exc)}", {"text": text})
            if not result.get("success"):
                raise ExecutionError("ELEMENT_NOT_FOUND", f"Text '{text}' not found on page", {"text": text})
            return action_warnings

        if a == "eval_js":
            script = act.get("script") or val
            if script:
                try:
                    result = await run_eval_js(PAGE, script)
                    EVAL_RESULTS.append(result)
                except Exception as e:
                    action_warnings.append(f"WARNING:auto:eval_js failed - {str(e)}")
            return action_warnings

        if a == "click_blank_area":
            try:
                result = await perform_click_blank_area(PAGE)
                EVAL_RESULTS.append(result)
                if result.get("fallback"):
                    action_warnings.append("INFO:auto:Used fallback coordinates for blank area click")
                if not result.get("success"):
                    action_warnings.append("WARNING:auto:click_blank_area did not report success")
            except Exception as e:
                action_warnings.append(f"WARNING:auto:click_blank_area failed - {str(e)}")
            return action_warnings

        if a == "close_popup":
            try:
                result = await perform_close_popup(PAGE)
                EVAL_RESULTS.append(result)
                if result.get("found") and result.get("clicked"):
                    action_warnings.append(
                        f"INFO:auto:Closed {result.get('popupCount', 0)} popup(s) by clicking outside at ({result.get('x')}, {result.get('y')})"
                    )
                elif result.get("found") and not result.get("clicked"):
                    action_warnings.append("WARNING:auto:Popup detected but could not find safe click area")
                else:
                    action_warnings.append("INFO:auto:No popups detected to close")
            except Exception as e:
                action_warnings.append(f"WARNING:auto:close_popup failed - {str(e)}")
            return action_warnings

        locator_actions = {"click", "click_text", "type", "hover", "select_option", "press_key", "extract_text"}
        if a in locator_actions:
            selectors_to_try: List[str] = []
            resolved_entry: Optional[Dict[str, Any]] = None
            chosen_selector: Optional[str] = None
            index_value = _parse_index_target(tgt)

            if index_value is not None:
                auto_refresh_message: Optional[str] = None
                if INDEX_MODE and (not _CURRENT_CATALOG or "index_map" not in _CURRENT_CATALOG):
                    try:
                        catalog = await _generate_element_catalog(force=True)
                        if catalog and catalog.get("index_map"):
                            version = (catalog or {}).get("catalog_version") or (
                                _CURRENT_CATALOG_SIGNATURE or {}
                            ).get("catalog_version")
                            if version:
                                auto_refresh_message = (
                                    f"INFO:auto:Element catalog refreshed automatically (version={version})"
                                )
                            else:
                                auto_refresh_message = (
                                    "INFO:auto:Element catalog refreshed automatically"
                                )
                        else:
                            raise ExecutionError(
                                "CATALOG_OUTDATED",
                                "Element catalog is not available. Please execute refresh_catalog.",
                                {"index": index_value},
                            )
                    except ExecutionError as exc:
                        if is_final_retry:
                            action_warnings.append(f"WARNING:auto:{exc}")
                            return action_warnings
                        raise
                    except Exception as exc:
                        log.error("Automatic catalog refresh failed: %s", exc)
                        if is_final_retry:
                            action_warnings.append(
                                f"WARNING:auto:Failed to refresh element catalog automatically - {str(exc)}"
                            )
                            return action_warnings
                        raise ExecutionError(
                            "CATALOG_OUTDATED",
                            "Element catalog is not available. Please execute refresh_catalog.",
                            {"index": index_value},
                        ) from exc
                selectors_to_try, resolved_entry = _resolve_index_entry(index_value)
                if auto_refresh_message:
                    action_warnings.append(auto_refresh_message)
            else:
                if not _validate_selector(tgt):
                    action_warnings.append(f"WARNING:auto:Skipping {a} - Empty selector")
                    return action_warnings
                selectors_to_try = [tgt]

            if PAGE is None:
                error_msg = f"Browser not initialized - cannot execute {a} action"
                if is_final_retry:
                    action_warnings.append(f"WARNING:auto:{error_msg}")
                    return action_warnings
                raise Exception(error_msg)

            loc: Optional = None
            last_error: Optional[str] = None
            for selector in selectors_to_try:
                candidate = selector
                for attempt in range(LOCATOR_RETRIES):
                    try:
                        if a == "click_text" and index_value is None:
                            if "||" in candidate or candidate.strip().startswith(("css=", "text=", "role=", "xpath=")):
                                loc = await SmartLocator(PAGE, candidate).locate()
                            else:
                                loc = await SmartLocator(PAGE, f"text={candidate}").locate()
                        else:
                            loc = await SmartLocator(PAGE, candidate).locate()
                        if loc is not None:
                            chosen_selector = candidate
                            break
                        await _stabilize_page()
                    except Exception as exc:
                        last_error = str(exc)
                        if attempt == LOCATOR_RETRIES - 1:
                            action_warnings.append(
                                f"WARNING:auto:Locator search failed for '{candidate}' - {last_error}"
                            )
                if loc is not None:
                    break

            if loc is None:
                if index_value is not None:
                    raise ExecutionError(
                        "ELEMENT_NOT_FOUND",
                        f"Catalog index {index_value} could not be resolved to a live element",
                        {"index": index_value, "selectors": selectors_to_try},
                    )
                error_msg = (
                    f"Element not found: {tgt}. Consider using alternative selectors or text matching."
                )
                if is_final_retry:
                    action_warnings.append(f"WARNING:auto:{error_msg}")
                    return action_warnings
                raise Exception(error_msg)

            if index_value is not None and resolved_entry:
                catalog_version = (_CURRENT_CATALOG_SIGNATURE or {}).get("catalog_version", "")
                _log_index_adoption(catalog_version, index_value, chosen_selector or selectors_to_try[0], a)

            # Execute the action with enhanced error handling
            action_timeout = ACTION_TIMEOUT if ms == 0 else ms

            try:
                display_target = chosen_selector or tgt
                if a in ("click", "click_text"):
                    await _safe_click(loc, timeout=action_timeout)
                elif a == "type":
                    await _safe_fill(
                        loc,
                        val,
                        timeout=action_timeout,
                        original_target=display_target,
                    )
                elif a == "hover":
                    await _safe_hover(loc, timeout=action_timeout)
                elif a == "select_option":
                    await _safe_select(loc, val, timeout=action_timeout)
                elif a == "press_key":
                    key = act.get("key", "")
                    if key:
                        await _safe_press(loc, key, timeout=action_timeout)
                    else:
                        if key:
                            await PAGE.keyboard.press(key)
                        else:
                            action_warnings.append("WARNING:auto:No key specified for press_key action")
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
                return action_warnings
            except ExecutionError:
                raise
            except Exception as e:
                error_msg = str(e)
                if "failed -" in error_msg and ("Original:" in error_msg or "Fallback" in error_msg):
                    action_guidance = _get_action_guidance(a, display_target, error_msg)
                    action_warnings.append(
                        f"WARNING:auto:{a} operation failed for '{display_target}' after trying multiple methods. {error_msg}. {action_guidance}"
                    )
                else:
                    basic_guidance = _get_basic_guidance(a, error_msg)
                    action_warnings.append(
                        f"WARNING:auto:{a} operation failed for '{display_target}' - {error_msg}. {basic_guidance}"
                    )
                return action_warnings

        # Actions that reach here without return fall back to success
        return action_warnings

    except ExecutionError as exc:
        action_warnings.append(f"ERROR:auto:{exc}")
        raise
    except Exception as e:
        action_warnings.append(f"WARNING:auto:Action '{a}' failed - {str(e)}")

    return action_warnings


def _get_action_guidance(action: str, target: str, error_msg: str) -> str:
    """Provide specific guidance to LLM based on the type of action and failure."""
    guidance_map = {
        "hover": "Try using 'click' action instead if the hover was for triggering a menu, or wait longer for page elements to stabilize before hovering.",
        "select_option": "Consider using 'click' to open the dropdown first, or try using a different selector like text content instead of value. Check if the select element is properly loaded.",
        "press_key": "Try using 'type' action if you were entering text, or 'click' if you were trying to trigger a button. Ensure the element is focused before key operations.",
        "click": "Try using a different selector (text content, ARIA labels, or CSS classes). Wait for page to fully load, or try 'click_text' with visible text.",
        "type": "Ensure the input field is visible and enabled. Try clicking the field first, or use 'press_key' for special keys like Tab or Enter."
    }
    
    base_guidance = guidance_map.get(action, "Try using alternative selectors or wait for page elements to fully load.")
    
    # Add specific guidance based on error patterns
    if "timeout" in error_msg.lower():
        return f"{base_guidance} Consider increasing wait time or checking if elements are dynamically loaded."
    elif "not found" in error_msg.lower() or "not visible" in error_msg.lower():
        return f"{base_guidance} The element may not be present yet - try waiting or using text-based selectors."
    elif "not enabled" in error_msg.lower() or "disabled" in error_msg.lower():
        return f"{base_guidance} The element appears to be disabled - check for prerequisite actions or form validation."
    else:
        return base_guidance


def _get_basic_guidance(action: str, error_msg: str) -> str:
    """Provide basic guidance for non-fallback failures."""
    if "timeout" in error_msg.lower():
        return "Consider waiting longer or checking if the page is fully loaded."
    elif "not found" in error_msg.lower():
        return "Try using alternative selectors like text content, CSS classes, or ARIA attributes."
    elif "network" in error_msg.lower() or "connection" in error_msg.lower():
        return "This appears to be a network issue - consider retrying the operation."
    else:
        return "Consider using alternative approaches or waiting for page to stabilize."


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
                is_final_retry = (attempt == retries)
                action_warnings = await _apply(act, is_final_retry)
                all_warnings.extend(action_warnings)
                action_executed = True

                # Enhanced DOM stabilization after each action
                await _stabilize_page()
                break

            except ExecutionError as exec_err:
                log.error("[%s] Execution error on action %d: %s", correlation_id, i + 1, str(exec_err))
                raise
            except Exception as e:
                error_msg = f"Action {i+1} '{act.get('action', 'unknown')}' attempt {attempt}/{retries} failed: {str(e)}"
                log.error("[%s] %s", correlation_id, error_msg)

                if attempt == retries:
                    # Final retry failure - try once more with is_final_retry=True to get warnings
                    try:
                        action_warnings = await _apply(act, is_final_retry=True)
                        all_warnings.extend(action_warnings)
                    except Exception as final_e:
                        # If even final retry with warnings fails, add error message
                        friendly_msg, is_internal = _classify_error(str(final_e))
                        all_warnings.append(f"ERROR:auto:[{correlation_id}] {friendly_msg}")
                    
                    # Save debug artifacts for critical failures
                    friendly_msg, is_internal = _classify_error(str(e))
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

        # Stop further action processing if stop action was executed
        if act.get("action") == "stop":
            break

    # Use safe content retrieval to avoid navigation errors
    html = await _safe_get_page_content()
    if not html:
        all_warnings.append(f"WARNING:auto:Could not retrieve final page content - page may be navigating")
    
    return html, all_warnings


# -------------------------------------------------- HTTP エンドポイント
@app.post("/execute-dsl")
def execute_dsl():
    global _CURRENT_CATALOG_SIGNATURE
    correlation_id = str(uuid.uuid4())[:8]
    log.info("Starting DSL execution with correlation ID: %s", correlation_id)

    warnings: List[str] = []
    success = True
    error_info: Optional[Dict[str, Any]] = None
    html = ""
    nav_detected = False
    observation_signature: Optional[Dict[str, Any]] = None

    def _handle_typed_run(payload: Dict[str, Any]):
        try:
            _run(_init_browser())
            if PAGE is None:
                raise RuntimeError("Browser page is not initialized")
            if USE_INCOGNITO_CONTEXT:
                _run(_create_clean_context())
            executor = RunExecutor(PAGE)
            result = _run(executor.run(payload))
            result.setdefault("correlation_id", correlation_id)
            return jsonify(result)
        except Exception as exc:
            log.exception("[%s] Typed DSL execution failed: %s", correlation_id, exc)
            response = {
                "success": False,
                "error": {"code": "EXECUTION_FAILED", "message": str(exc), "details": {}},
                "warnings": warnings,
                "html": html,
                "correlation_id": correlation_id,
                "results": [],
            }
            return jsonify(response)

    try:
        data = request.get_json(force=True)
        if isinstance(data, list):
            data = {"actions": data}
        if "plan" in data:
            return _handle_typed_run(data)
        _validate_schema(data)
    except ValidationError as ve:
        warnings.append(f"[{correlation_id}] ERROR:auto:InvalidDSL - {str(ve)}")
        success = False
        error_info = {"code": "INVALID_DSL", "message": str(ve), "details": {}}
        observation_signature = _run(_ensure_catalog_signature()) if INDEX_MODE else _collect_basic_signature()
        html = _run(_safe_get_page_content()) if PAGE else ""
        response = {
            "success": success,
            "error": error_info,
            "warnings": warnings,
            "html": html,
            "correlation_id": correlation_id,
            "observation": _build_observation(nav_detected=False, signature=observation_signature),
            "is_done": False,
            "complete": False,
        }
        return jsonify(response)
    except Exception as e:
        warnings.append(f"[{correlation_id}] ERROR:auto:ParseError - {str(e)}")
        success = False
        error_info = {"code": "INVALID_DSL", "message": str(e), "details": {}}
        observation_signature = _run(_ensure_catalog_signature()) if INDEX_MODE else _collect_basic_signature()
        html = _run(_safe_get_page_content()) if PAGE else ""
        response = {
            "success": success,
            "error": error_info,
            "warnings": warnings,
            "html": html,
            "correlation_id": correlation_id,
            "observation": _build_observation(nav_detected=False, signature=observation_signature),
            "is_done": False,
            "complete": False,
        }
        return jsonify(response)

    actions = data.get("actions", [])
    uses_catalog_indices = _actions_use_catalog_indices(actions)
    for i, action in enumerate(actions):
        for warning in _validate_action_params(action):
            warnings.append(f"[{correlation_id}] Action {i+1}: {warning}")

    critical_errors = [
        w
        for w in warnings
        if "Invalid navigate URL" in w or "Invalid selector" in w or "scroll_to_text requires" in w
    ]
    if critical_errors:
        log.warning("Skipping execution due to critical validation errors: %s", critical_errors)
        success = False
        error_info = {
            "code": "INVALID_DSL",
            "message": "Critical validation errors detected in DSL actions",
            "details": {"warnings": critical_errors},
        }
        observation_signature = _run(_ensure_catalog_signature()) if INDEX_MODE else _collect_basic_signature()
        html = _run(_safe_get_page_content()) if PAGE else ""
        response = {
            "success": success,
            "error": error_info,
            "warnings": warnings,
            "html": html,
            "correlation_id": correlation_id,
            "observation": _build_observation(nav_detected=False, signature=observation_signature),
            "is_done": False,
            "complete": False,
        }
        return jsonify(response)

    action_count = len(actions)
    if action_count > MAX_DSL_ACTIONS:
        warnings.append(
            f"[{correlation_id}] ERROR:auto:DSL too large - {action_count} actions exceed limit of {MAX_DSL_ACTIONS}. Consider splitting into smaller chunks."
        )
        actions = actions[:MAX_DSL_ACTIONS]
        data["actions"] = actions

    complete_flag = bool(data.get("complete"))
    expected_catalog_version = data.get("expected_catalog_version")

    try:
        _run(_init_browser())
        if not _run(_check_browser_health()):
            log.warning("[%s] Browser unhealthy, recreating...", correlation_id)
            _run(_recreate_browser())
        if USE_INCOGNITO_CONTEXT:
            _run(_create_clean_context())
    except Exception as init_error:
        success = False
        error_info = {
            "code": "BROWSER_INIT_FAILED",
            "message": str(init_error),
            "details": {},
        }
        observation_signature = _run(_ensure_catalog_signature()) if INDEX_MODE else _collect_basic_signature()
        response = {
            "success": success,
            "error": error_info,
            "warnings": warnings,
            "html": html,
            "correlation_id": correlation_id,
            "observation": _build_observation(nav_detected=False, signature=observation_signature),
            "is_done": complete_flag,
            "complete": complete_flag,
        }
        return jsonify(response)

    before_signature: Optional[Dict[str, Any]] = None
    if INDEX_MODE:
        observation_signature = _run(_ensure_catalog_signature())
        before_signature = dict(observation_signature) if observation_signature else {}
        current_version = (observation_signature or {}).get("catalog_version")
        refresh_only = all(a.get("action") == "refresh_catalog" for a in actions)
        if (
            expected_catalog_version
            and current_version
            and current_version != expected_catalog_version
            and not refresh_only
        ):
            warnings.append(
                f"[{correlation_id}] WARNING:auto:Catalog version mismatch detected (expected {expected_catalog_version}, current {current_version}). Attempting automatic refresh."
            )
            auto_refresh_succeeded = False
            new_version = current_version
            try:
                refreshed_catalog = _run(_generate_element_catalog(force=True))
                auto_refresh_succeeded = bool(refreshed_catalog)
                new_version = (refreshed_catalog or {}).get("catalog_version") or current_version
                updated_signature = _CURRENT_CATALOG_SIGNATURE or observation_signature
                if updated_signature:
                    observation_signature = dict(updated_signature)
                    before_signature = dict(observation_signature)
                version_text = new_version or current_version or "unknown"
                warnings.append(
                    f"[{correlation_id}] INFO:auto:Element catalog auto-refreshed (version={version_text})."
                )
            except ExecutionError as exc:
                warnings.append(
                    f"[{correlation_id}] ERROR:auto:Automatic catalog refresh failed - {exc}"
                )
            except Exception as exc:
                warnings.append(
                    f"[{correlation_id}] ERROR:auto:Automatic catalog refresh failed - {str(exc)}"
                )
            current_version = new_version
            if (
                uses_catalog_indices
                and expected_catalog_version
                and current_version != expected_catalog_version
            ):
                warnings.append(
                    f"[{correlation_id}] WARNING:auto:Catalog version still differs from planner expectation (expected {expected_catalog_version}, now {current_version}). Index-based targets may require a new plan."
                )
                if not auto_refresh_succeeded:
                    warnings.append(
                        f"[{correlation_id}] WARNING:auto:Proceeding without a refreshed catalog may cause element mismatches."
                    )
    else:
        observation_signature = _collect_basic_signature()

    try:
        html_result, action_warns = _run(_run_actions_with_lock(actions, correlation_id))
        warnings.extend(action_warns)

        refresh_done = _run(_check_and_refresh_browser(correlation_id))
        if refresh_done:
            warnings.append(f"INFO:auto:[{correlation_id}] Browser context refreshed for stability")

        html = html_result

    except ExecutionError as exec_err:
        success = False
        error_info = {
            "code": exec_err.code,
            "message": str(exec_err),
            "details": exec_err.details,
        }
        log.warning(
            "[%s] Structured execution error: code=%s message=%s details=%s",
            correlation_id,
            exec_err.code,
            str(exec_err),
            exec_err.details,
        )
        html = _run(_safe_get_page_content()) if PAGE else ""
        if INDEX_MODE:
            observation_signature = observation_signature or _run(_ensure_catalog_signature())
        else:
            observation_signature = observation_signature or _collect_basic_signature()

    except Exception as e:
        success = False
        err_message = str(e)
        log.exception("DSL execution failed: %s", err_message)
        warnings.append(f"ERROR:auto:[{correlation_id}] ExecutionError - {err_message}")
        debug_info = _run(_save_debug_artifacts(correlation_id, err_message))
        if debug_info:
            warnings.append(f"DEBUG:auto:{debug_info}")
        html = _run(_safe_get_page_content()) if PAGE else ""
        if INDEX_MODE:
            observation_signature = observation_signature or _run(_ensure_catalog_signature())
        else:
            observation_signature = observation_signature or _collect_basic_signature()
        error_info = {"code": "UNEXPECTED_ERROR", "message": err_message, "details": {}}

    if INDEX_MODE:
        try:
            after_signature = _run(_compute_dom_signature())
        except Exception as exc:
            log.debug("Failed to compute DOM signature after execution: %s", exc)
            after_signature = observation_signature or before_signature
        if after_signature:
            if before_signature and after_signature.get("catalog_version") != before_signature.get("catalog_version"):
                nav_detected = True
                _mark_catalog_outdated(after_signature)
            else:
                _CURRENT_CATALOG_SIGNATURE = after_signature
            observation_signature = after_signature
    else:
        observation_signature = observation_signature or _collect_basic_signature()

    response = {
        "success": success,
        "error": error_info,
        "warnings": warnings,
        "html": html,
        "correlation_id": correlation_id,
        "observation": _build_observation(nav_detected=nav_detected, signature=observation_signature),
        "is_done": complete_flag,
        "complete": complete_flag,
    }
    return jsonify(response)


@app.post("/browser-use/session")
def start_browser_use_session():
    correlation_id = str(uuid.uuid4())[:8]
    data = request.get_json(force=True) or {}

    command = str(data.get("command", "")).strip()
    if not command:
        return jsonify({"error": "command empty"}), 400

    model_value = data.get("model")
    model = str(model_value).strip() if model_value is not None else _DEFAULT_MODEL
    if not model:
        model = _DEFAULT_MODEL

    requested_steps = data.get("max_steps")
    max_steps = _DEFAULT_MAX_STEPS
    if requested_steps is not None:
        try:
            max_steps = int(requested_steps)
        except (TypeError, ValueError):
            return jsonify({"error": "max_steps must be an integer"}), 400
        if max_steps <= 0:
            return jsonify({"error": "max_steps must be positive"}), 400

    manager = _get_browser_use_manager()
    try:
        session_id = manager.start_session(command, model=model, max_steps=max_steps)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        message = str(exc)
        payload = {"error": message}
        status = 500
        if "ライブビューのブラウザに接続できないため実行できません" in message:
            payload["code"] = "shared_browser_unavailable"
            status = 503
            log.error("[%s] Failed to start browser-use session: %s", correlation_id, message)
        else:
            log.exception("[%s] Browser-use session start failed", correlation_id)
        return jsonify(payload), status
    except Exception as exc:  # pragma: no cover - defensive fallback
        log.exception("[%s] Browser-use session start failed unexpectedly", correlation_id)
        return jsonify({"error": "failed to start automation"}), 500

    return jsonify({"session_id": session_id})


@app.get("/browser-use/session/<session_id>")
def get_browser_use_session(session_id: str):
    info = _get_browser_use_manager().get_status(session_id)
    if info is None:
        return jsonify({"error": "session not found"}), 404
    return jsonify(info)


@app.post("/browser-use/session/<session_id>/cancel")
def cancel_browser_use_session(session_id: str):
    if not _get_browser_use_manager().cancel_session(session_id):
        return jsonify({"error": "session not found"}), 404
    return jsonify({"status": "cancelled"})


@app.post("/shared-browser/ensure")
def ensure_shared_browser():
    correlation_id = str(uuid.uuid4())[:8]
    candidates = _candidate_cdp_endpoints()
    deduped = _dedupe_candidates(candidates)
    payload: Dict[str, Any] = {
        "correlation_id": correlation_id,
        "candidates": deduped,
    }

    try:
        _run(_init_browser())
        ready = bool(_run(_check_browser_health()))
    except Exception as exc:
        log.error("[%s] Shared browser warmup failed: %s", correlation_id, exc)
        payload.update(
            {
                "status": "error",
                "ready": False,
                "cdp_ready": False,
                "error": str(exc),
            }
        )
        return jsonify(payload), 503

    active_endpoint = CDP_URL or (deduped[0] if deduped else "")

    public_endpoint = ""
    for candidate in deduped:
        host = _candidate_hostname(candidate).lower()
        if host and host not in _LOOPBACK_HOSTS:
            public_endpoint = candidate
            break

    if not public_endpoint:
        forwarded_host = request.headers.get("X-Forwarded-Host", "").split(",")[0].strip()
        request_host = forwarded_host or request.host or ""
        host_only = request_host.split(":")[0].strip()
        if host_only and host_only.lower() not in _LOOPBACK_HOSTS:
            port = (
                _candidate_port(active_endpoint)
                or (_candidate_port(deduped[0]) if deduped else None)
                or int(os.getenv("CHROMIUM_REMOTE_DEBUG_PORT", "9222"))
            )
            public_endpoint = f"http://{host_only}:{port}"
    if not public_endpoint:
        public_endpoint = active_endpoint

    version_url = _json_version_url(public_endpoint) if public_endpoint else ""

    cdp_ready = False
    metadata: Dict[str, Any] = {}
    public_websocket: Optional[str] = None

    if public_endpoint:
        try:
            cdp_ready = bool(
                _run(_wait_cdp(public_endpoint, timeout=5, poll_interval=0.5))
            )
        except Exception as exc:
            log.debug(
                "[%s] CDP readiness check failed for %s: %s",
                correlation_id,
                public_endpoint,
                exc,
            )
            cdp_ready = False

        try:
            metadata = _run(_fetch_cdp_metadata(public_endpoint))
        except Exception as exc:
            log.debug(
                "[%s] Failed to retrieve CDP metadata from %s: %s",
                correlation_id,
                public_endpoint,
                exc,
            )
            metadata = {}

        raw_ws = None
        if metadata:
            raw_ws = metadata.get("webSocketDebuggerUrl") or metadata.get(
                "websocketDebuggerUrl"
            )
        if isinstance(raw_ws, str) and raw_ws.strip():
            public_websocket = normalise_cdp_websocket(public_endpoint, raw_ws.strip())

    payload.update(
        {
            "status": "ready" if ready else "initialising",
            "ready": ready,
            "cdp_ready": cdp_ready,
            "active_endpoint": active_endpoint,
            "public_endpoint": public_endpoint,
            "json_version_url": version_url,
            "candidates": _dedupe_candidates(deduped, [public_endpoint], [active_endpoint]),
        }
    )

    if metadata:
        payload["metadata"] = metadata
    if public_websocket:
        payload["public_websocket"] = public_websocket

    log.info(
        "[%s] Shared browser status: ready=%s cdp_ready=%s public_endpoint=%s",
        correlation_id,
        ready,
        cdp_ready,
        public_endpoint,
    )

    return jsonify(payload)


@app.get("/source")
def source():
    try:
        # Only initialize browser if it's not already healthy
        if not PAGE or not _run(_check_browser_health()):
            _run(_init_browser())
        return Response(_run(_safe_get_page_content()), mimetype="text/plain")
    except Exception as e:
        log.error("source error: %s", e)
        return Response(str(e), mimetype="text/plain")


@app.get("/url")
def current_url():
    try:
        # Only initialize browser if it's not already healthy
        if not PAGE or not _run(_check_browser_health()):
            _run(_init_browser())
        url = _get_page_url_sync() if PAGE else ""
        return jsonify({"url": url})
    except Exception as e:
        log.error("url error: %s", e)
        return jsonify({"url": "", "error": str(e)})


@app.get("/screenshot")
def screenshot():
    try:
        # Only initialize browser if it's not already healthy
        if not PAGE or not _run(_check_browser_health()):
            _run(_init_browser())
        img = _run(PAGE.screenshot(type="png"))
        return Response(base64.b64encode(img), mimetype="text/plain")
    except Exception as e:
        log.error("screenshot error: %s", e)
        return Response(str(e), mimetype="text/plain", status=500)


@app.get("/elements")
def elements():
    try:
        # Only initialize browser if it's not already healthy
        if not PAGE or not _run(_check_browser_health()):
            _run(_init_browser())
        data = _run(_list_elements())
        return jsonify(data)
    except Exception as e:
        log.error("elements error: %s", e)
        return jsonify([])


@app.get("/catalog")
def catalog():
    if not INDEX_MODE:
        return jsonify(
            {
                "abbreviated": [],
                "full": [],
                "catalog_version": None,
                "index_mode_enabled": False,
            }
        )

    refresh_requested = request.args.get("refresh", "false").lower() in {"1", "true", "yes"}

    try:
        if not PAGE or not _run(_check_browser_health()):
            _run(_init_browser())
        catalog_data = _run(_generate_element_catalog(force=refresh_requested))
        signature = _CURRENT_CATALOG_SIGNATURE or {}
        return jsonify(
            {
                "abbreviated": catalog_data.get("abbreviated", []),
                "full": catalog_data.get("full", []),
                "catalog_version": signature.get("catalog_version"),
                "metadata": {
                    "url": signature.get("url"),
                    "title": signature.get("title"),
                },
                "index_mode_enabled": True,
            }
        )
    except ExecutionError as exc:
        log.warning("Catalog generation error: %s", exc)
        return jsonify(
            {
                "abbreviated": [],
                "full": [],
                "catalog_version": None,
                "index_mode_enabled": True,
                "error": {"code": exc.code, "message": str(exc), "details": exc.details},
            }
        )
    except Exception as e:
        log.error("catalog error: %s", e)
        return jsonify(
            {
                "abbreviated": [],
                "full": [],
                "catalog_version": None,
                "index_mode_enabled": True,
                "error": {"code": "CATALOG_ERROR", "message": str(e)},
            }
        )


@app.get("/extracted")
def extracted():
    return jsonify(EXTRACTED_TEXTS)


@app.get("/eval_results")
def eval_results():
    return jsonify(EVAL_RESULTS)


@app.get("/stop-request")
def get_stop_request():
    """Get current stop request if any."""
    global _STOP_REQUEST
    if _STOP_REQUEST:
        return jsonify(_STOP_REQUEST)
    return jsonify(None)


@app.post("/stop-response")
def post_stop_response():
    """Handle user response to stop request."""
    global _STOP_REQUEST
    data = request.get_json(force=True)
    user_response = data.get("response", "")
    
    # Clear the stop request
    _STOP_REQUEST = None

    # Return the user response for inclusion in conversation history
    return jsonify({"status": "success", "user_response": user_response})


@app.get("/events/<run_id>")
def get_run_events(run_id: str):
    """Return structured log events for the given run identifier."""
    config = load_config()
    events_path = Path(config.log_root) / run_id / "events.jsonl"
    if not events_path.exists():
        return jsonify({"error": "events_not_found"}), 404
    try:
        return Response(events_path.read_text(encoding="utf-8"), mimetype="application/json")
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/healthz")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run("0.0.0.0", 7000, threaded=False)
