# vnc/automation_server.py
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import os
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse


import httpx
from flask import Flask, Response, jsonify, request
from jsonschema import Draft7Validator, ValidationError
from playwright.async_api import Error as PwError, Locator, async_playwright
from pydantic import ValidationError as PydanticValidationError

from automation.dsl.models import Selector
from vnc.locator_utils import SmartLocator  # 同ディレクトリ
from vnc.selector_resolver import SelectorResolver, StableNodeStore
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

# Import browser-use adapter
from vnc.browser_use_adapter import get_browser_adapter, close_browser_adapter, BrowserUseAdapter

# -------------------------------------------------- 基本設定
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("auto")


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
CDP_URL = "http://localhost:9222"
# Use about:blank as the default start page to avoid unexpected navigation
DEFAULT_URL = os.getenv("START_URL", "about:blank")
SPA_STABILIZE_TIMEOUT = int(
    os.getenv("SPA_STABILIZE_TIMEOUT", "2000")
)  # ms  SPA描画安定待ち
MAX_DSL_ACTIONS = int(os.getenv("MAX_DSL_ACTIONS", "50"))  # DSL アクション数上限
INDEX_MODE = os.getenv("INDEX_MODE", "true").lower() == "true"
CATALOG_MAX_ELEMENTS = int(os.getenv("CATALOG_MAX_ELEMENTS", "120"))

CATALOG_CACHE_DIR = Path(
    os.getenv("CATALOG_CACHE_DIR")
    or (Path(__file__).resolve().parents[1] / "catalog_cache")
)
CATALOG_CACHE_LIMIT = int(os.getenv("CATALOG_CACHE_LIMIT", "10"))
_CATALOG_ARCHIVE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

_STABLE_SELECTOR_STORE = StableNodeStore()


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


CATALOG_COLLECTION_SCRIPT = r"""
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
  const skipTagNames = new Set(['html', 'head', 'body']);
  const registerCandidate = (el) => {
    if (!el || !(el instanceof Element)) return;
    const tagName = el.tagName ? el.tagName.toLowerCase() : '';
    if (skipTagNames.has(tagName)) return;
    tags.add(el);
  };
  interactiveSelectors.forEach(sel => {
    for (const el of document.querySelectorAll(sel)) {
      registerCandidate(el);
    }
  });

  const hasAnyHandler = (el) => {
    if (!el || !(el instanceof Element)) return false;
    for (const key in el) {
      if (key && key.startsWith('on') && typeof el[key] === 'function') {
        return true;
      }
    }
    if (el.attributes) {
      for (const attr of el.attributes) {
        if (attr && attr.name && attr.name.startsWith('on')) {
          return true;
        }
      }
    }
    if (typeof getEventListeners === 'function') {
      try {
        const listeners = getEventListeners(el);
        if (listeners && typeof listeners === 'object') {
          for (const type in listeners) {
            if (listeners[type] && listeners[type].length > 0) {
              return true;
            }
          }
        }
      } catch (err) {
        // ignore errors from getEventListeners
      }
    }
    if (typeof window !== 'undefined' && typeof window.__ag_get_events === 'function') {
      try {
        const evs = window.__ag_get_events(el);
        if (evs && evs.length > 0) {
          return true;
        }
      } catch (err) {
        // ignore errors from custom trackers
      }
    }
    return false;
  };

  const focusableContainerTags = new Set(['div', 'span', 'section', 'article', 'main', 'aside', 'header', 'footer', 'nav', 'li']);
  for (const el of document.querySelectorAll('*')) {
    if (!el || !(el instanceof Element)) continue;
    const tagName = el.tagName ? el.tagName.toLowerCase() : '';
    const roleAttr = (el.getAttribute('role') || '').trim().toLowerCase();
    const tabindexAttr = el.getAttribute('tabindex');
    let explicitTabIndex = null;
    if (tabindexAttr !== null && tabindexAttr !== '') {
      const parsed = parseInt(tabindexAttr, 10);
      if (!Number.isNaN(parsed)) {
        explicitTabIndex = parsed;
      }
    }
    const hasHandler = hasAnyHandler(el);
    if (hasHandler) {
      registerCandidate(el);
    }
    if (explicitTabIndex !== null && explicitTabIndex >= 0) {
      registerCandidate(el);
      continue;
    }
    if (!roleAttr && focusableContainerTags.has(tagName)) {
      const computedTabIndex = typeof el.tabIndex === 'number' ? el.tabIndex : NaN;
      if (!Number.isNaN(computedTabIndex) && computedTabIndex >= 0) {
        registerCandidate(el);
      }
    }
  }

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


def _catalog_cache_path(version: str) -> Path:
    """Build the cache file path for a given catalog version."""

    sanitized = version.strip()
    return CATALOG_CACHE_DIR / f"{sanitized}.json"


def _dedupe_selectors(selectors: Iterable[str]) -> List[str]:
    """Remove duplicates while preserving selector order."""

    seen: set[str] = set()
    ordered: List[str] = []
    for selector in selectors:
        if not selector or not isinstance(selector, str):
            continue
        normalized = selector.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


_SELECTOR_METADATA_KEYS = {"frame", "container", "selectors", "fallbacks"}
_SELECTOR_VALUE_KEYS = {
    "css",
    "xpath",
    "text",
    "role",
    "priority",
    "near_text",
    "near-text",
    "aria_label",
    "aria-label",
    "stable_id",
    "stable-id",
    "index",
}


@dataclass
class StructuredSelectorCandidate:
    selector: Any
    frame: Any = None
    container: Any = None
    origin: Any = None


def _merge_selector_payload(selector_data: Any, extras: Dict[str, Any]) -> Any:
    if not extras:
        return selector_data

    if isinstance(selector_data, list):
        merged: List[Any] = []
        for item in selector_data:
            if isinstance(item, dict):
                enriched = dict(item)
                for key, value in extras.items():
                    enriched.setdefault(key, value)
                merged.append(enriched)
            else:
                merged.append(item)
        return merged

    if isinstance(selector_data, dict):
        enriched = dict(selector_data)
        for key, value in extras.items():
            enriched.setdefault(key, value)
        return enriched

    if isinstance(selector_data, str):
        enriched = dict(extras)
        if selector_data.startswith("xpath="):
            enriched.setdefault("xpath", selector_data[len("xpath="):])
        elif selector_data.startswith("text="):
            enriched.setdefault("text", selector_data[len("text="):])
        elif selector_data.startswith("role="):
            enriched.setdefault("role", selector_data[len("role="):])
        else:
            enriched.setdefault("css", selector_data)
        return enriched

    return selector_data


def _looks_like_selector_data(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in value for key in _SELECTOR_VALUE_KEYS):
            return True
        if "selector" in value:
            return True
    return False


def _collect_structured_candidates(action: Dict[str, Any]) -> List[StructuredSelectorCandidate]:
    candidates: List[StructuredSelectorCandidate] = []

    def _walk(node: Any, *, frame: Any = None, container: Any = None) -> None:
        if node is None:
            return
        if isinstance(node, list):
            for item in node:
                _walk(item, frame=frame, container=container)
            return
        if isinstance(node, dict):
            next_frame = node.get("frame", frame)
            next_container = node.get("container", container)

            if "selectors" in node:
                _walk(node.get("selectors"), frame=next_frame, container=next_container)

            if "fallbacks" in node:
                _walk(node.get("fallbacks"), frame=next_frame, container=next_container)

            if "selector" in node:
                extras = {
                    key: value
                    for key, value in node.items()
                    if key not in _SELECTOR_METADATA_KEYS.union({"selector"})
                }
                merged = _merge_selector_payload(node.get("selector"), extras)
                _walk(merged, frame=next_frame, container=next_container)
                return

            payload = {
                key: value
                for key, value in node.items()
                if key not in _SELECTOR_METADATA_KEYS
            }
            if payload:
                candidates.append(
                    StructuredSelectorCandidate(
                        selector=payload,
                        frame=next_frame,
                        container=next_container,
                        origin=node,
                    )
                )
            return

        candidates.append(
            StructuredSelectorCandidate(
                selector=node,
                frame=frame,
                container=container,
                origin=node,
            )
        )

    sources: List[Any] = []
    if "selector" in action:
        sources.append(action.get("selector"))
    if "selectors" in action:
        sources.append(action.get("selectors"))
    target_value = action.get("target")
    if isinstance(target_value, (dict, list)):
        sources.append(target_value)

    for source in sources:
        _walk(source)

    seen: set[str] = set()
    unique: List[StructuredSelectorCandidate] = []
    for candidate in candidates:
        try:
            key = json.dumps(
                {
                    "selector": candidate.selector,
                    "frame": candidate.frame,
                    "container": candidate.container,
                },
                sort_keys=True,
                default=str,
                ensure_ascii=False,
            )
        except TypeError:
            key = repr((candidate.selector, candidate.frame, candidate.container))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    return unique


def _format_selector_display(value: Any) -> str:
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(value)
    return str(value)


async def _resolve_selector_in_context(context: Any, selector_data: Any):
    try:
        selector_model = Selector.model_validate(selector_data)
    except PydanticValidationError as exc:
        log.debug("Structured selector validation failed: %s", exc)
        return None

    resolver = SelectorResolver(context, store=_STABLE_SELECTOR_STORE)
    try:
        return await resolver.resolve(selector_model)
    except Exception as exc:
        log.debug("Structured selector resolution failed: %s", exc)
        return None


async def _resolve_frame_reference(frame_spec: Any):
    if PAGE is None:
        return None

    if frame_spec is None:
        return PAGE

    if isinstance(frame_spec, (int, float)) and not isinstance(frame_spec, bool):
        try:
            idx = int(frame_spec)
        except (TypeError, ValueError):
            idx = 0
        frames = PAGE.frames if PAGE else []
        if 0 <= idx < len(frames):
            return frames[idx]
        return PAGE

    if isinstance(frame_spec, str):
        frames = PAGE.frames if PAGE else []
        for frame in frames:
            try:
                if frame.name == frame_spec:
                    return frame
            except Exception:
                pass
            try:
                if frame_spec and frame_spec in (frame.url or ""):
                    return frame
            except Exception:
                continue
        return PAGE

    if isinstance(frame_spec, list):
        for item in frame_spec:
            frame = await _resolve_frame_reference(item)
            if frame is not None:
                return frame
        return PAGE

    if isinstance(frame_spec, dict):
        strategy = frame_spec.get("strategy")
        value = frame_spec.get("value")
        frames = PAGE.frames if PAGE else []

        if strategy == "index":
            try:
                idx = int(value)
            except (TypeError, ValueError):
                idx = 0
            if 0 <= idx < len(frames):
                return frames[idx]
            return PAGE

        if strategy == "name":
            name_value = str(value or "")
            for frame in frames:
                try:
                    if frame.name == name_value:
                        return frame
                except Exception:
                    continue
            return PAGE

        if strategy == "url":
            url_value = str(value or "")
            for frame in frames:
                try:
                    if url_value and url_value in (frame.url or ""):
                        return frame
                except Exception:
                    continue
            return PAGE

        if strategy == "parent":
            main = PAGE.main_frame if PAGE else None
            return (main.parent_frame if main else None) or PAGE

        if strategy == "root":
            return PAGE

        if strategy == "element" and value is not None:
            resolved = await _resolve_selector_in_context(PAGE, value)
            if resolved and resolved.element is not None:
                try:
                    frame = await resolved.element.content_frame()
                    if frame:
                        return frame
                except Exception:
                    return PAGE
            return PAGE

        if strategy is None and _looks_like_selector_data(frame_spec):
            selector_payload = frame_spec.get("selector") or {
                key: frame_spec[key]
                for key in frame_spec
                if key != "selector"
            }
            resolved = await _resolve_selector_in_context(PAGE, selector_payload)
            if resolved and resolved.element is not None:
                try:
                    frame = await resolved.element.content_frame()
                    if frame:
                        return frame
                except Exception:
                    return PAGE
            return PAGE

    return PAGE


async def _resolve_structured_candidate(
    candidate: StructuredSelectorCandidate,
):
    frame_context = await _resolve_frame_reference(candidate.frame)
    if frame_context is None:
        return None, None

    scope = frame_context
    container_resolved = None
    if candidate.container is not None:
        container_resolved = await _resolve_selector_in_context(scope, candidate.container)
        if container_resolved is None or container_resolved.locator is None:
            return None, None
        scope = container_resolved.locator

    resolved = await _resolve_selector_in_context(scope, candidate.selector)
    if resolved is None or resolved.locator is None:
        return None, container_resolved

    return resolved, container_resolved


def _compose_text_signature(entry: Dict[str, Any]) -> str:
    """Create a textual signature by combining primary labels and nearby text."""

    parts: List[str] = []
    for key in ("primary_label", "secondary_label", "section_hint", "state_hint"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    for text in entry.get("nearest_texts", []) or []:
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return " ".join(parts)


def _build_selector_fallbacks(entry: Dict[str, Any]) -> List[str]:
    """Generate fallback selectors from catalog metadata when robust selectors are empty."""

    candidates: List[str] = []
    role = (entry.get("role") or "").strip()
    primary_label = (entry.get("primary_label") or "").strip()
    href_full = (entry.get("href_full") or entry.get("href_short") or "").strip()

    if role and primary_label:
        quoted = primary_label.replace('"', '\\"')
        candidates.append(f'role={role}[name="{quoted}"]')
    if primary_label:
        candidates.append(f"text={primary_label}")
    for nearby in entry.get("nearest_texts", []) or []:
        if isinstance(nearby, str) and nearby.strip():
            candidates.append(f"text={nearby.strip()}")
    if href_full:
        quoted_href = href_full.replace('"', '\\"')
        candidates.append(f'css=[href="{quoted_href}"]')

    return _dedupe_selectors(candidates)


def _store_catalog_snapshot(catalog: Dict[str, Any]) -> None:
    """Persist catalog snapshot to memory and disk, pruning to the configured limit."""

    if not INDEX_MODE:
        return
    version = str(catalog.get("catalog_version") or "").strip()
    if not version:
        return

    snapshot = {
        "catalog_version": version,
        "index_map": dict(catalog.get("index_map") or {}),
        "dom_hash": catalog.get("dom_hash"),
        "url": catalog.get("url"),
        "title": catalog.get("title"),
        "generated_at": catalog.get("generated_at"),
    }

    try:
        CATALOG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - defensive logging only
        log.warning("Failed to create catalog cache directory %s: %s", CATALOG_CACHE_DIR, exc)

    _CATALOG_ARCHIVE.pop(version, None)
    _CATALOG_ARCHIVE[version] = snapshot

    try:
        with _catalog_cache_path(version).open("w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
    except Exception as exc:  # pragma: no cover - IO failure is non-fatal
        log.warning("Failed to persist catalog snapshot %s: %s", version, exc)

    if CATALOG_CACHE_LIMIT <= 0:
        return

    while len(_CATALOG_ARCHIVE) > CATALOG_CACHE_LIMIT:
        oldest_version, _ = _CATALOG_ARCHIVE.popitem(last=False)
        if oldest_version == version:
            continue
        try:
            path = _catalog_cache_path(oldest_version)
            if path.exists():
                path.unlink()
        except Exception:  # pragma: no cover - cache pruning best effort
            log.debug("Failed to remove old catalog snapshot %s", oldest_version)


def _load_catalog_snapshot(version: str) -> Optional[Dict[str, Any]]:
    """Load a catalog snapshot from memory or disk."""

    if not version:
        return None
    if version in _CATALOG_ARCHIVE:
        snapshot = _CATALOG_ARCHIVE[version]
        _CATALOG_ARCHIVE.move_to_end(version)
        return snapshot

    path = _catalog_cache_path(version)
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:  # pragma: no cover - IO failure is non-fatal
        log.warning("Failed to load catalog snapshot %s: %s", version, exc)
        return None

    if not isinstance(data, dict):
        return None

    _CATALOG_ARCHIVE[version] = data

    if CATALOG_CACHE_LIMIT > 0 and len(_CATALOG_ARCHIVE) > CATALOG_CACHE_LIMIT:
        while len(_CATALOG_ARCHIVE) > CATALOG_CACHE_LIMIT:
            oldest_version, _ = _CATALOG_ARCHIVE.popitem(last=False)
            if oldest_version == version:
                continue
            try:
                old_path = _catalog_cache_path(oldest_version)
                if old_path.exists():
                    old_path.unlink()
            except Exception:  # pragma: no cover - best effort cleanup
                log.debug("Failed to prune catalog snapshot %s", oldest_version)

    return data


def _find_matching_catalog_entry(
    expected_entry: Dict[str, Any],
    candidate_index_map: Dict[str, Dict[str, Any]],
) -> Tuple[Optional[int], Optional[Dict[str, Any]], Dict[str, float]]:
    """Find the best matching entry in the current catalog for a prior index entry."""

    best_index: Optional[int] = None
    best_entry: Optional[Dict[str, Any]] = None
    best_score = -1.0
    best_reason = {"dom_hash": 0.0, "selector_overlap": 0.0, "textual": 0.0, "score": 0.0}

    expected_hash = (expected_entry.get("dom_path_hash") or "").strip()
    expected_selectors = _dedupe_selectors(expected_entry.get("robust_selectors", []) or [])
    expected_selector_set = set(expected_selectors)
    expected_text = _compose_text_signature(expected_entry)

    for idx_str, candidate in (candidate_index_map or {}).items():
        if not isinstance(candidate, dict):
            continue
        try:
            idx = int(idx_str)
        except (TypeError, ValueError):
            continue

        candidate_hash = (candidate.get("dom_path_hash") or "").strip()
        candidate_selectors = _dedupe_selectors(candidate.get("robust_selectors", []) or [])
        candidate_selector_set = set(candidate_selectors)
        candidate_text = _compose_text_signature(candidate)

        dom_match = 1.0 if expected_hash and expected_hash == candidate_hash else 0.0
        if expected_hash and not candidate_hash:
            dom_match = 0.0

        overlap = 0.0
        if expected_selector_set and candidate_selector_set:
            shared = expected_selector_set.intersection(candidate_selector_set)
            overlap = len(shared) / float(max(len(expected_selector_set), len(candidate_selector_set)))

        textual_similarity = 0.0
        if expected_text and candidate_text:
            textual_similarity = SequenceMatcher(None, expected_text, candidate_text).ratio()

        score = (dom_match * 3.0) + (overlap * 2.0) + textual_similarity

        if score > best_score:
            best_score = score
            best_index = idx
            best_entry = candidate
            best_reason = {
                "dom_hash": dom_match,
                "selector_overlap": overlap,
                "textual": textual_similarity,
                "score": score,
            }

        # Prefer exact DOM hash matches even if scores tie due to weight rounding
        elif score == best_score and dom_match > best_reason.get("dom_hash", 0.0):
            best_index = idx
            best_entry = candidate
            best_reason = {
                "dom_hash": dom_match,
                "selector_overlap": overlap,
                "textual": textual_similarity,
                "score": score,
            }

    if best_score <= 0:
        return None, None, best_reason

    return best_index, best_entry, best_reason


def _rebind_actions_for_catalog(
    actions: List[Dict[str, Any]],
    expected_version: Optional[str],
    current_catalog: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Attempt to update actions bound to catalog indices when versions diverge."""

    if not INDEX_MODE or not actions or not expected_version:
        return []

    snapshot = _load_catalog_snapshot(expected_version)
    if not snapshot:
        return [
            (
                "WARNING:auto:Catalog snapshot unavailable for version "
                f"{expected_version}. A new plan is required."
            )
        ]

    current = current_catalog or _CURRENT_CATALOG or {}
    index_map = current.get("index_map") or {}
    if not index_map:
        return [
            "WARNING:auto:Current catalog missing index map. Unable to rebind indices. A new plan is required.",
        ]

    expected_index_map = snapshot.get("index_map") or {}
    mapping_cache: Dict[int, Dict[str, Any]] = {}
    messages: List[str] = []

    for action in actions:
        if not isinstance(action, dict):
            continue
        raw_target = action.get("target")
        if not isinstance(raw_target, str):
            continue
        expected_index = _parse_index_target(raw_target)
        if expected_index is None:
            continue

        created_mapping = False
        if expected_index not in mapping_cache:
            expected_entry = expected_index_map.get(str(expected_index)) or {}
            match_index, match_entry, reason = _find_matching_catalog_entry(expected_entry, index_map)

            combined_selectors: List[str] = []
            combined_selectors.extend(expected_entry.get("robust_selectors", []) or [])
            if match_entry:
                combined_selectors.extend(match_entry.get("robust_selectors", []) or [])
            combined_selectors.extend(_build_selector_fallbacks(expected_entry))
            if match_entry:
                combined_selectors.extend(_build_selector_fallbacks(match_entry))
            selectors = _dedupe_selectors(combined_selectors)

            binding: Dict[str, Any] = {
                "target": raw_target,
                "expected_index": expected_index,
                "match_index": match_index,
                "selectors": selectors,
                "match_reason": reason,
                "expected_catalog_version": expected_version,
                "current_catalog_version": current.get("catalog_version"),
            }

            if match_index is None:
                message = (
                    "WARNING:auto:Catalog rebind failed for index "
                    f"{expected_index}. A new plan is required."
                )
            else:
                message = (
                    "INFO:auto:Catalog index "
                    f"{expected_index} rebound to {match_index} "
                    f"(dom_hash={reason.get('dom_hash', 0.0):.2f}, "
                    f"selector_overlap={reason.get('selector_overlap', 0.0):.2f}, "
                    f"textual={reason.get('textual', 0.0):.2f})."
                )

            mapping_cache[expected_index] = {"binding": binding, "message": message}
            created_mapping = True

        cached = mapping_cache[expected_index]
        action["_catalog_binding"] = dict(cached["binding"])

        match_index = cached["binding"].get("match_index")
        if match_index is not None:
            action["target"] = f"index={int(match_index)}"

        if created_mapping:
            messages.append(cached["message"])

    return messages


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
        _store_catalog_snapshot(catalog)
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


def _validate_selector(selector: Any) -> bool:
    """Validate that selector is non-empty."""
    if isinstance(selector, str):
        return bool(selector.strip())
    if isinstance(selector, (int, float)) and not isinstance(selector, bool):
        return True
    if isinstance(selector, list):
        return any(_validate_selector(item) for item in selector)
    if isinstance(selector, dict):
        if not selector:
            return False
        if any(key in selector for key in _SELECTOR_VALUE_KEYS):
            return True
        return any(
            _validate_selector(value)
            for value in selector.values()
            if isinstance(value, (str, dict, list, int, float)) and not isinstance(value, bool)
        )
    return bool(selector)


def _validate_action_params(act: Dict) -> List[str]:
    """Validate action parameters and return list of validation warnings."""
    warnings = []
    action = act.get("action")
    
    if action == "navigate":
        url = act.get("target", "")
        if not _validate_url(url):
            warnings.append(f"ERROR:auto:Invalid navigate URL '{url}' - URL must be non-empty and properly formatted")
    
    elif action == "wait_for_selector":
        selector = act.get("selector", act.get("target", ""))
        if not _validate_selector(selector):
            display = _format_selector_display(selector)
            warnings.append(f"ERROR:auto:Invalid selector '{display}' - Selector must be non-empty")

    elif action in ["click", "click_text", "type", "hover", "select_option", "press_key", "extract_text"]:
        selector = act.get("selector", act.get("target", ""))
        if not _validate_selector(selector):
            display = _format_selector_display(selector)
            warnings.append(
                f"ERROR:auto:Invalid selector '{display}' for action '{action}' - Selector must be non-empty"
            )

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


# -------------------------------------------------- Browser-use 管理
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

# Replace Playwright globals with browser-use adapter
PW = BROWSER = PAGE = None  # Keep for compatibility with legacy code
_BROWSER_ADAPTER: Optional[BrowserUseAdapter] = None
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
    """Safely retrieve the current page URL using browser-use adapter."""
    global _BROWSER_ADAPTER
    
    if _BROWSER_ADAPTER is None:
        return ""

    try:
        return await _BROWSER_ADAPTER.get_url()
    except Exception as exc:
        log.debug("Failed to get URL from browser-use adapter: %s", exc)
        return ""


def _get_page_url_sync() -> str:
    """Helper used by Flask routes to fetch the current page URL safely."""
    global _BROWSER_ADAPTER
    
    if _BROWSER_ADAPTER is None:
        return ""

    try:
        return _run(_get_page_url_value())
    except RuntimeError as exc:
        # Fallback to empty string if the event loop is already running.
        log.debug("Event loop busy while fetching page URL: %s", exc)
        return ""
    except Exception as exc:
        log.debug("Failed to fetch page URL via event loop: %s", exc)
        return ""


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
    """Check if browser-use adapter is still functional."""
    try:
        global _BROWSER_ADAPTER
        if _BROWSER_ADAPTER is None:
            return False
        
        # Use browser-use adapter health check
        return await _BROWSER_ADAPTER.is_healthy()
    except Exception as e:
        log.warning("Browser health check failed: %s", e)
        return False


async def _recreate_browser():
    """Recreate browser-use adapter when health check fails."""
    global PW, BROWSER, PAGE, _BROWSER_ADAPTER
    
    # Save current URL before closing the browser to preserve task context
    current_url = None
    if _BROWSER_ADAPTER:
        try:
            current_url = await _BROWSER_ADAPTER.get_url()
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
    
    # Close existing browser-use adapter
    try:
        if _BROWSER_ADAPTER:
            await _BROWSER_ADAPTER.close()
    except Exception as e:
        log.warning("Error closing browser adapter: %s", e)
    
    # Reset globals for compatibility
    PW = BROWSER = PAGE = None
    _BROWSER_ADAPTER = None
    
    # Reinitialize browser-use adapter
    await _init_browser()
    
    # Navigate back to preserved URL if we had one
    if current_url and _BROWSER_ADAPTER:
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
                result = await _BROWSER_ADAPTER.navigate(current_url, **attempt_params)
                
                if result.get("success"):
                    # Verify we successfully navigated to the intended URL
                    try:
                        final_url = await _BROWSER_ADAPTER.get_url()
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
                else:
                    log.warning("URL restoration attempt %d failed: %s", i + 1, result.get("error", "Unknown error"))
                    
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
    global PW, BROWSER, PAGE, _BROWSER_FIRST_INIT, _BROWSER_ADAPTER
    
    # Check if browser-use adapter is already healthy
    if _BROWSER_ADAPTER and await _BROWSER_ADAPTER.is_healthy():
        # Set PAGE to a placeholder for compatibility with legacy code
        PAGE = "browser-use-active"
        return
        
    # Initialize browser-use adapter
    _BROWSER_ADAPTER = await get_browser_adapter()
    
    # Set compatibility variables for legacy code
    PW = "browser-use-pw"
    BROWSER = "browser-use-browser" 
    PAGE = "browser-use-page"

    # Only navigate to DEFAULT_URL on the very first initialization
    if _BROWSER_FIRST_INIT:
        try:
            result = await _BROWSER_ADAPTER.navigate(DEFAULT_URL, wait_until="load", timeout=NAVIGATION_TIMEOUT)
            if result.get("success"):
                log.info("Initial navigation to default URL: %s", DEFAULT_URL)
            else:
                log.warning("Failed to navigate to default URL: %s", result.get("error", "Unknown error"))
        except Exception as e:
            log.warning("Failed to navigate to default URL: %s", e)
        
        _BROWSER_FIRST_INIT = False
    else:
        log.info("Browser recreated - skipping navigation to default URL")
        
    log.info("browser-use adapter ready")


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
    global _BROWSER_ADAPTER
    
    if not _BROWSER_ADAPTER or not _BROWSER_ADAPTER.page:
        # Return placeholder elements for compatibility
        return [
            {
                "index": 0,
                "tag": "div",
                "text": "Browser-use adapter placeholder element",
                "id": "placeholder",
                "class": "adapter-placeholder",
                "xpath": "/html/body/div[1]",
            }
        ]
    
    try:
        els = []
        # Use browser-use adapter to get elements
        result = await _BROWSER_ADAPTER.evaluate("""
            () => {
                const elements = Array.from(document.querySelectorAll('a,button,input,textarea,select'));
                return elements.slice(0, arguments[0]).map((el, i) => {
                    if (!el.offsetParent && el.style.display === 'none') return null;
                    
                    function getXPath(el) {
                        if (el === document.body) return '/html/body';
                        let ix = 0, s = el.previousSibling;
                        while (s) { 
                            if (s.nodeType === 1 && s.tagName === el.tagName) ix++; 
                            s = s.previousSibling; 
                        }
                        return getXPath(el.parentNode) + '/' + el.tagName.toLowerCase() + '[' + (ix + 1) + ']';
                    }
                    
                    return {
                        index: i,
                        tag: el.tagName.toLowerCase(),
                        text: (el.innerText || el.textContent || '').trim().substring(0, 50),
                        id: el.id || null,
                        class: el.className || null,
                        xpath: getXPath(el)
                    };
                }).filter(Boolean);
            }
        """)
        
        if result:
            return result[:limit]
            
    except Exception as e:
        log.error(f"Failed to list elements: {e}")
    
    return []



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
    global _BROWSER_ADAPTER
    if _BROWSER_ADAPTER is None:
        return ""
    
    # Use browser-use adapter to get page content
    try:
        return await _BROWSER_ADAPTER.get_page_content()
    except Exception as e:
        log.error("Failed to get page content from browser-use adapter: %s", e)
        return ""


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
            binding_info = act.get("_catalog_binding") or {}
            binding_selectors = _dedupe_selectors(binding_info.get("selectors", []) or [])
            selectors_to_try: List[str] = list(binding_selectors)
            resolved_entry: Optional[Dict[str, Any]] = None
            chosen_selector: Optional[str] = None
            index_value = _parse_index_target(tgt)
            structured_candidates = _collect_structured_candidates(act) if index_value is None else []
            binding_match_index_raw = binding_info.get("match_index")
            match_index_override: Optional[int] = None
            try:
                if binding_match_index_raw is not None:
                    match_index_override = int(binding_match_index_raw)
            except (TypeError, ValueError):
                match_index_override = None

            if match_index_override is not None:
                index_value = match_index_override
            elif (
                index_value is not None
                and binding_info
                and binding_match_index_raw is None
                and binding_selectors
            ):
                # Rebinding failed but fallback selectors are available. Skip stale index lookup.
                index_value = None

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
                selectors_from_index, resolved_entry = _resolve_index_entry(index_value)
                selectors_to_try.extend(selectors_from_index)
                if auto_refresh_message:
                    action_warnings.append(auto_refresh_message)
            else:
                if not selectors_to_try:
                    if _validate_selector(tgt):
                        selectors_to_try.append(tgt)
                    elif structured_candidates:
                        pass
                    else:
                        action_warnings.append(f"WARNING:auto:Skipping {a} - Empty selector")
                        return action_warnings

            selectors_to_try = _dedupe_selectors(selectors_to_try)

            if PAGE is None:
                error_msg = f"Browser not initialized - cannot execute {a} action"
                if is_final_retry:
                    action_warnings.append(f"WARNING:auto:{error_msg}")
                    return action_warnings
                raise Exception(error_msg)

            loc: Optional = None
            last_error: Optional[str] = None
            if structured_candidates and PAGE is not None:
                for candidate in structured_candidates:
                    try:
                        resolved_structured, _ = await _resolve_structured_candidate(candidate)
                    except Exception as exc:
                        last_error = str(exc)
                        continue
                    if resolved_structured and resolved_structured.locator is not None:
                        loc = resolved_structured.locator
                        chosen_selector = _format_selector_display(resolved_structured.selector.as_legacy())
                        break

            for selector in selectors_to_try:
                if loc is not None:
                    break
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
            if uses_catalog_indices:
                rebind_messages = _rebind_actions_for_catalog(
                    actions,
                    expected_catalog_version,
                    _CURRENT_CATALOG,
                )
                for message in rebind_messages:
                    warnings.append(f"[{correlation_id}] {message}")
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


@app.get("/source")
def source():
    try:
        # Only initialize browser if it's not already healthy
        global _BROWSER_ADAPTER
        if not _BROWSER_ADAPTER or not _run(_check_browser_health()):
            _run(_init_browser())
        return Response(_run(_safe_get_page_content()), mimetype="text/plain")
    except Exception as e:
        log.error("source error: %s", e)
        return Response("", mimetype="text/plain")


@app.get("/url")
def current_url():
    try:
        # Only initialize browser if it's not already healthy
        global _BROWSER_ADAPTER
        if not _BROWSER_ADAPTER or not _run(_check_browser_health()):
            _run(_init_browser())
        url = _get_page_url_sync()
        return jsonify({"url": url})
    except Exception as e:
        log.error("url error: %s", e)
        return jsonify({"url": ""})


@app.get("/screenshot")
def screenshot():
    try:
        # Only initialize browser if it's not already healthy
        global _BROWSER_ADAPTER
        if not _BROWSER_ADAPTER or not _run(_check_browser_health()):
            _run(_init_browser())
        img = _run(_BROWSER_ADAPTER.screenshot())
        return Response(base64.b64encode(img), mimetype="text/plain")
    except Exception as e:
        log.error("screenshot error: %s", e)
        return Response("", mimetype="text/plain")


@app.get("/elements")
def elements():
    try:
        # Only initialize browser if it's not already healthy
        global _BROWSER_ADAPTER
        if not _BROWSER_ADAPTER or not _run(_check_browser_health()):
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
