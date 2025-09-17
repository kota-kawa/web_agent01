import os
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import requests
from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    Response,
    send_from_directory,
)

# --------------- Agent modules -----------------------------------
from agent.llm.client import call_llm
from agent.browser.vnc import (
    get_html as vnc_html,
    execute_dsl,
    get_elements as vnc_elements,
    get_dom_tree as vnc_dom_tree,
    get_url as vnc_url,
)
from agent.browser.dom import DOMElementNode
from agent.controller.prompt import build_prompt
from agent.controller.async_executor import get_async_executor
from agent.utils.history import load_hist, save_hist, append_history_entry
from agent.utils.html import strip_html
from agent.element_catalog import (
    get_catalog_for_prompt,
    get_expected_version as get_catalog_expected_version,
    is_enabled as is_catalog_enabled,
)
from automation.dsl import models as dsl_models
from pydantic import ValidationError

# --------------- Flask & Logger ----------------------------------
app = Flask(__name__)
log = logging.getLogger("agent")
log.setLevel(logging.INFO)

# Pre-initialize AsyncExecutor for immediate Playwright execution
_async_executor_instance = None

def get_preinitialized_async_executor():
    """Get pre-initialized async executor to reduce startup overhead."""
    global _async_executor_instance
    if _async_executor_instance is None:
        _async_executor_instance = get_async_executor()
        log.info("Pre-initialized AsyncExecutor for immediate execution")
    return _async_executor_instance


@app.errorhandler(500)
def internal_server_error(error):
    """Global error handler to convert 500 errors to JSON warnings."""
    import uuid
    correlation_id = str(uuid.uuid4())[:8]
    error_msg = f"Internal server error - {str(error)}"
    log.exception("[%s] Unhandled exception: %s", correlation_id, error_msg)
    
    return jsonify({
        "error": f"Internal failure - An unexpected error occurred",
        "correlation_id": correlation_id
    }), 200  # Return 200 instead of 500


@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors without logging a full exception."""
    import uuid
    correlation_id = str(uuid.uuid4())[:8]
    # Avoid noisy stack traces for missing routes
    return jsonify({
        "error": f"Resource not found - {request.path}",
        "correlation_id": correlation_id
    }), 200


@app.errorhandler(Exception)
def handle_exception(error):
    """Global exception handler to catch all uncaught exceptions."""
    import uuid
    correlation_id = str(uuid.uuid4())[:8]
    log.exception("[%s] Uncaught exception: %s", correlation_id, str(error))
    
    return jsonify({
        "error": f"Internal failure - {str(error)}",
        "correlation_id": correlation_id
    }), 200  # Return 200 instead of 500

# --------------- VNC / Playwright API ----------------------------
VNC_API = "http://vnc:7000"  # Playwright 側の API
# Default to a blank page to prevent unintended navigation to external sites
START_URL = os.getenv("START_URL", "about:blank")
MAX_STEPS = int(os.getenv("MAX_STEPS", "30"))

# --------------- Conversation History ----------------------------
LOG_DIR = os.getenv("LOG_DIR", "./")
os.makedirs(LOG_DIR, exist_ok=True)
HIST_FILE = os.path.join(LOG_DIR, "conversation_history.json")


def _format_index_value(value: Any) -> str | None:
    """Convert index-like values to the legacy ``index=N`` selector form."""

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        if value < 0:
            return None
        return f"index={value}"

    if isinstance(value, float):
        if value.is_integer() and value >= 0:
            return f"index={int(value)}"
        return None

    try:
        text = str(value).strip()
    except Exception:
        return None

    if not text:
        return None

    try:
        idx = int(text)
    except ValueError:
        return None

    if idx < 0:
        return None

    return f"index={idx}"


def _escape_quotes(value: str) -> str:
    return value.replace("\"", "\\\"")


def _stringify_selector(selector: Any) -> str:
    """Convert structured selector data into the legacy string-based DSL form."""

    if selector is None:
        return ""

    if isinstance(selector, str):
        return selector

    if isinstance(selector, list):
        parts = []
        for item in selector:
            formatted = _stringify_selector(item)
            if formatted:
                if formatted not in parts:
                    parts.append(formatted)
        return " || ".join(parts)

    index_form = _format_index_value(selector)
    if index_form:
        return index_form

    if isinstance(selector, dict):
        if "selector" in selector and selector["selector"]:
            candidate = _stringify_selector(selector["selector"])
            if candidate:
                return candidate

        if "index" in selector:
            index_form = _format_index_value(selector.get("index"))
            if index_form:
                return index_form

        css_value = selector.get("css")
        if css_value:
            return f"css={css_value}"

        xpath_value = selector.get("xpath")
        if xpath_value:
            return f"xpath={xpath_value}"

        role_value = selector.get("role")
        if role_value:
            name_value = selector.get("name") or selector.get("text")
            role_value = str(role_value).strip()
            if name_value:
                name_text = _escape_quotes(str(name_value).strip())
                if name_text:
                    return f'role={role_value}[name="{name_text}"]'
            if role_value:
                return f"role={role_value}"

        text_value = selector.get("text")
        if text_value:
            return str(text_value)

        aria_label = selector.get("aria_label") or selector.get("aria-label")
        if aria_label:
            escaped = _escape_quotes(str(aria_label).strip())
            if escaped:
                return f'css=[aria-label="{escaped}"]'

        stable_id = selector.get("stable_id")
        if stable_id:
            stable = str(stable_id).strip()
            if stable:
                escaped = _escape_quotes(stable)
                candidates = [f'css=[data-testid="{escaped}"]', f'css=[name="{escaped}"]']
                if re.fullmatch(r"[A-Za-z_][-A-Za-z0-9_]*", stable):
                    candidates.insert(1, f"css=#{stable}")
                return " || ".join(candidates)

        for key in ("value", "target"):
            if selector.get(key):
                candidate = _stringify_selector(selector[key])
                if candidate:
                    return candidate

        for key, value in selector.items():
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return str(value)

    # Fallback to simple string conversion for any remaining types
    try:
        return str(selector)
    except Exception:
        return ""


@dataclass
class NormalizedActions:
    """Container holding the typed plan payload and legacy fallback."""

    typed: List[Dict[str, Any]]
    legacy: List[Dict[str, Any]]

    @property
    def has_typed(self) -> bool:
        return bool(self.typed)


_PRIORITY_CANONICAL_MAP = {
    "css": "css",
    "xpath": "xpath",
    "role": "role",
    "text": "text",
    "name": "text",
    "aria_label": "aria_label",
    "aria-label": "aria_label",
    "arialabel": "aria_label",
    "aria": "aria_label",
    "near_text": "near_text",
    "near-text": "near_text",
    "near": "near_text",
    "index": "index",
    "stable_id": "stable_id",
    "stable-id": "stable_id",
}


def _canonical_selector_key(key: Any) -> str:
    if not isinstance(key, str):
        return ""
    lower = key.strip().lower()
    mapping = {
        "aria-label": "aria_label",
        "arialabel": "aria_label",
        "aria_label": "aria_label",
        "aria": "aria_label",
        "near-text": "near_text",
        "near_text": "near_text",
        "near": "near_text",
        "stable-id": "stable_id",
        "stable_id": "stable_id",
        "selector": "selector",
        "target": "target",
        "label": "label",
    }
    return mapping.get(lower, lower)


def _canonical_priority(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return _PRIORITY_CANONICAL_MAP.get(value.strip().lower())


def _parse_index_value(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value.is_integer() and value >= 0:
            return int(value)
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.lower().startswith("index="):
            return _parse_index_value(text.split("=", 1)[1])
        if text.isdigit():
            return int(text)
    return None


def _merge_selector_data(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    if not update:
        return base
    priority: List[str] = list(base.get("priority", []))
    for key, value in update.items():
        if key == "priority":
            items = value if isinstance(value, list) else [value]
            for item in items:
                if item and item not in priority:
                    priority.append(item)
        elif key == "index":
            if value is not None:
                base["index"] = value
        elif value is not None:
            if key in {"text", "aria_label", "near_text"}:
                if key not in base or not base.get(key):
                    base[key] = value
            else:
                base.setdefault(key, value)
    if priority:
        base["priority"] = priority
    elif "priority" in base:
        base.pop("priority", None)
    return base


_INDEX_PATTERN = re.compile(r"^index\s*=\s*(\d+)$", re.I)
_CSS_PATTERN = re.compile(r"^css\s*=\s*(.+)$", re.I)
_XPATH_PATTERN = re.compile(r"^xpath\s*=\s*(.+)$", re.I)
_ROLE_PATTERN = re.compile(r"^role\s*=\s*([^\[\s]+)(?:\[(.+)\])?$", re.I)
_ARIA_PATTERN = re.compile(r"^(?:aria[-_]?label|aria)\s*=\s*(.+)$", re.I)
_TEXT_PATTERN = re.compile(r"^text\s*=\s*(.+)$", re.I)
_NEAR_PATTERN = re.compile(r"^near(?:[-_]?text)?\s*=\s*(.+)$", re.I)
_STABLE_PATTERN = re.compile(r"^stable[-_]?id\s*=\s*(.+)$", re.I)


def _parse_selector_string(selector: str) -> Dict[str, Any]:
    text = selector.strip()
    if not text:
        return {}
    if "||" in text:
        result: Dict[str, Any] = {}
        for part in [p.strip() for p in text.split("||") if p.strip()]:
            result = _merge_selector_data(result, _parse_selector_string(part))
        return result

    if (match := _INDEX_PATTERN.match(text)):
        return {"index": int(match.group(1)), "priority": ["index"]}
    if (match := _CSS_PATTERN.match(text)):
        return {"css": match.group(1).strip(), "priority": ["css"]}
    if (match := _XPATH_PATTERN.match(text)):
        return {"xpath": match.group(1).strip(), "priority": ["xpath"]}
    if (match := _ROLE_PATTERN.match(text)):
        role = match.group(1).strip()
        attrs = match.group(2) or ""
        data: Dict[str, Any] = {"role": role, "priority": ["role"]}
        for attr, value in re.findall(r"([\w\-]+)\s*=\s*['\"]([^'\"]+)['\"]", attrs):
            canonical = _canonical_selector_key(attr)
            if canonical in {"name", "text"}:
                data["text"] = value
            elif canonical == "aria_label":
                data["aria_label"] = value
            elif canonical == "near_text":
                data["near_text"] = value
        return data
    if (match := _ARIA_PATTERN.match(text)):
        return {"aria_label": match.group(1).strip(), "priority": ["aria_label"]}
    if (match := _TEXT_PATTERN.match(text)):
        return {"text": match.group(1).strip(), "priority": ["text"]}
    if (match := _NEAR_PATTERN.match(text)):
        return {"near_text": match.group(1).strip(), "priority": ["near_text"]}
    if (match := _STABLE_PATTERN.match(text)):
        return {"stable_id": match.group(1).strip(), "priority": ["stable_id"]}

    if text.startswith("//") or text.startswith("./") or text.startswith("(//"):
        return {"xpath": text, "priority": ["xpath"]}

    if (index_value := _parse_index_value(text)) is not None:
        return {"index": index_value, "priority": ["index"]}

    if text.startswith("#") or text.startswith(".") or " " in text or "[" in text or ":" in text:
        return {"css": text, "priority": ["css"]}

    if "=" in text:
        attr_name, _, attr_value = text.partition("=")
        attr_name = attr_name.strip()
        attr_value = attr_value.strip().strip('"\'')
        if attr_name and attr_value:
            if attr_name.lower() == "id":
                return {"css": f"#{attr_value}", "priority": ["css"]}
            if attr_name.lower() == "data-testid":
                return {"css": f"[data-testid='{attr_value}']", "priority": ["css"]}
            if attr_name.lower() == "name":
                return {"css": f"[name='{attr_value}']", "priority": ["css"]}
            return {"css": f"[{attr_name}='{attr_value}']", "priority": ["css"]}

    return {"text": text, "priority": ["text"]}


def _normalize_selector_data(selector: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {}

    def merge(value: Dict[str, Any]) -> None:
        nonlocal base
        base = _merge_selector_data(base, value)

    if selector is None:
        return base
    if isinstance(selector, list):
        for item in selector:
            merge(_normalize_selector_data(item))
        return base
    if isinstance(selector, dict):
        for key, value in selector.items():
            canonical_key = _canonical_selector_key(key)
            if canonical_key in {"selector", "target"}:
                merge(_normalize_selector_data(value))
                continue
            if canonical_key == "priority":
                items = value if isinstance(value, (list, tuple)) else [value]
                normalized = [item for item in (_canonical_priority(v) for v in items) if item]
                if normalized:
                    merge({"priority": normalized})
                continue
            if canonical_key == "index":
                idx = _parse_index_value(value)
                if idx is not None:
                    merge({"index": idx})
                continue
            if canonical_key in {"css", "xpath", "role", "text", "aria_label", "near_text", "stable_id"}:
                if value is None:
                    continue
                text_value = str(value).strip()
                if text_value:
                    merge({canonical_key: text_value})
                continue
            if canonical_key == "name":
                if value is None:
                    continue
                text_value = str(value).strip()
                if text_value:
                    merge({"text": text_value})
                continue
            if canonical_key == "label":
                if value is None:
                    continue
                label_value = str(value).strip()
                if label_value:
                    merge({"aria_label": label_value})
                continue
        return base
    if isinstance(selector, str):
        merge(_parse_selector_string(selector))
        return base

    merge({"text": str(selector)})
    return base


def _coerce_selector(selector: Any) -> Optional[dsl_models.Selector]:
    data = _normalize_selector_data(selector)
    if not data:
        return None
    try:
        return dsl_models.Selector(**data)
    except ValidationError:
        return None


def _coerce_tab_target(value: Any) -> Optional[dsl_models.TabTarget]:
    if isinstance(value, dsl_models.TabTarget):
        return value
    if value is None:
        return dsl_models.TabTarget()
    if isinstance(value, dict):
        strategy = str(value.get("strategy", "index")).lower()
        raw_value = value.get("value")
        if strategy == "index":
            idx = _parse_index_value(raw_value)
            raw_value = idx if idx is not None else 0
        elif strategy in {"previous", "next", "latest"}:
            raw_value = None
        elif raw_value is not None:
            raw_value = str(raw_value)
        return dsl_models.TabTarget(strategy=strategy, value=raw_value)
    if isinstance(value, (int, float)):
        idx = _parse_index_value(value)
        if idx is not None:
            return dsl_models.TabTarget(strategy="index", value=idx)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"previous", "next", "latest"}:
            return dsl_models.TabTarget(strategy=lowered, value=None)
        if lowered.startswith("index="):
            idx = _parse_index_value(lowered.split("=", 1)[1])
            return dsl_models.TabTarget(strategy="index", value=idx if idx is not None else 0)
        idx = _parse_index_value(value)
        if idx is not None:
            return dsl_models.TabTarget(strategy="index", value=idx)
        return dsl_models.TabTarget(strategy="title", value=value.strip())
    return None


def _coerce_frame_target(value: Any) -> Optional[dsl_models.FrameTarget]:
    if isinstance(value, dsl_models.FrameTarget):
        return value
    if value is None:
        return dsl_models.FrameTarget()
    if isinstance(value, dict):
        strategy = str(value.get("strategy", "index")).lower()
        raw_value = value.get("value")
        if strategy == "index":
            idx = _parse_index_value(raw_value)
            raw_value = idx if idx is not None else 0
        elif strategy in {"parent", "root"}:
            raw_value = None
        elif strategy == "element":
            selector_source = raw_value if raw_value is not None else value.get("selector")
            selector = _coerce_selector(selector_source)
            if selector is None:
                return None
            raw_value = selector
        elif raw_value is not None:
            raw_value = str(raw_value)
        return dsl_models.FrameTarget(strategy=strategy, value=raw_value)
    if isinstance(value, (int, float)):
        idx = _parse_index_value(value)
        if idx is not None:
            return dsl_models.FrameTarget(strategy="index", value=idx)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"parent", "root"}:
            return dsl_models.FrameTarget(strategy=lowered, value=None)
        if lowered.startswith("index="):
            idx = _parse_index_value(lowered.split("=", 1)[1])
            return dsl_models.FrameTarget(strategy="index", value=idx if idx is not None else 0)
        selector = _coerce_selector(value)
        if selector is not None:
            return dsl_models.FrameTarget(strategy="element", value=selector)
        return dsl_models.FrameTarget(strategy="name", value=value.strip())
    selector = _coerce_selector(value)
    if selector is not None:
        return dsl_models.FrameTarget(strategy="element", value=selector)
    return None


def _convert_to_typed_action(action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(action, dict):
        return None
    action_name = str(action.get("action") or action.get("type") or "").strip().lower()
    if not action_name:
        return None

    try:
        if action_name == "navigate":
            url = action.get("url") or action.get("target")
            if not isinstance(url, str) or not url.strip():
                return None
            model = dsl_models.NavigateAction(url=url.strip())
            return model.payload()

        if action_name == "click":
            selector = _coerce_selector(action.get("target") or action.get("selector"))
            if selector is None:
                return None
            button = str(action.get("button", "left")).lower()
            if button not in {"left", "right", "middle"}:
                button = "left"
            click_count_raw = action.get("click_count") or action.get("clicks") or 1
            try:
                click_count = int(click_count_raw)
                if click_count < 1:
                    click_count = 1
            except Exception:
                click_count = 1
            delay_raw = action.get("delay_ms") or action.get("delay")
            delay_ms = None
            if delay_raw is not None:
                try:
                    delay_ms = max(0, int(delay_raw))
                except Exception:
                    delay_ms = None
            model = dsl_models.ClickAction(
                selector=selector,
                button=button,
                click_count=click_count,
                delay_ms=delay_ms,
            )
            return model.payload()

        if action_name == "type":
            selector = _coerce_selector(action.get("target") or action.get("selector"))
            if selector is None:
                return None
            text_value = action.get("text", action.get("value", ""))
            text = "" if text_value is None else str(text_value)
            model = dsl_models.TypeAction(
                selector=selector,
                text=text,
                press_enter=bool(action.get("press_enter")),
                clear=bool(action.get("clear")),
            )
            return model.payload()

        if action_name == "select":
            selector = _coerce_selector(action.get("target") or action.get("selector"))
            if selector is None:
                return None
            value = action.get("value_or_label", action.get("value"))
            if value is None:
                return None
            model = dsl_models.SelectAction(selector=selector, value_or_label=str(value))
            return model.payload()

        if action_name == "press_key":
            keys_raw = action.get("keys") or action.get("hotkeys")
            if isinstance(keys_raw, str):
                keys = [keys_raw]
            elif isinstance(keys_raw, list):
                keys = [str(item) for item in keys_raw if isinstance(item, (str, int, float))]
            else:
                key = action.get("key")
                keys = [str(key)] if key else []
            keys = [key for key in keys if key]
            if not keys:
                return None
            scope = str(action.get("scope", action.get("target_scope", "active_element"))).lower()
            if scope not in {"active_element", "page"}:
                scope = "active_element"
            model = dsl_models.PressKeyAction(keys=keys, scope=scope)
            return model.payload()

        if action_name == "wait":
            timeout_raw = action.get("timeout_ms", action.get("ms"))
            timeout_ms = None
            if timeout_raw is not None:
                try:
                    timeout_ms = max(0, int(timeout_raw))
                except Exception:
                    timeout_ms = None
            condition = action.get("for") or action.get("condition") or action.get("until")
            selector_condition = action.get("target") if not isinstance(condition, dict) else None
            wait_condition = None
            state = action.get("state")
            if isinstance(condition, dict):
                if "selector" in condition or "target" in condition:
                    selector = _coerce_selector(condition.get("selector") or condition.get("target"))
                    if selector is None:
                        return None
                    cond_state = condition.get("state") or state or "visible"
                    wait_condition = dsl_models.WaitForSelector(selector=selector, state=cond_state)
                elif "state" in condition and isinstance(condition["state"], str):
                    wait_condition = dsl_models.WaitForState(state=condition["state"])
                elif "timeout_ms" in condition or "ms" in condition:
                    wait_timeout = condition.get("timeout_ms", condition.get("ms"))
                    if wait_timeout is not None:
                        try:
                            wait_condition = dsl_models.WaitForTimeout(timeout_ms=max(0, int(wait_timeout)))
                        except Exception:
                            wait_condition = None
            elif isinstance(condition, str):
                lowered = condition.lower()
                if lowered in {"load", "domcontentloaded", "networkidle"}:
                    wait_condition = dsl_models.WaitForState(state=lowered)
                else:
                    selector = _coerce_selector(condition)
                    if selector is None:
                        selector = _coerce_selector(selector_condition)
                    if selector is not None:
                        wait_condition = dsl_models.WaitForSelector(selector=selector, state=state or "visible")
            elif selector_condition:
                selector = _coerce_selector(selector_condition)
                if selector is not None:
                    wait_condition = dsl_models.WaitForSelector(selector=selector, state=state or "visible")
            model = dsl_models.WaitAction(for_=wait_condition, timeout_ms=timeout_ms or 10000)
            return model.payload()

        if action_name == "scroll":
            to_value = action.get("to")
            amount = action.get("amount")
            direction = action.get("direction")
            container_selector = _coerce_selector(action.get("container"))
            scroll_to: Any = None
            if to_value is None and amount is not None:
                to_value = amount
            if isinstance(to_value, str):
                lowered = to_value.strip().lower()
                if lowered in {"top", "bottom"}:
                    scroll_to = lowered
                else:
                    selector = _coerce_selector(to_value)
                    if selector is not None:
                        scroll_to = dsl_models.ScrollTarget(selector=selector)
            elif isinstance(to_value, (int, float)):
                try:
                    scroll_to = int(to_value)
                except Exception:
                    scroll_to = None
            elif isinstance(to_value, dict):
                selector = _coerce_selector(to_value.get("selector") or to_value.get("target") or to_value)
                if selector is not None:
                    scroll_to = dsl_models.ScrollTarget(selector=selector)
            elif to_value is None and action.get("target"):
                selector = _coerce_selector(action.get("target"))
                if selector is not None:
                    scroll_to = dsl_models.ScrollTarget(selector=selector)
            kwargs: Dict[str, Any] = {}
            if scroll_to is not None:
                kwargs["to"] = scroll_to
            if container_selector is not None:
                kwargs["container"] = container_selector
            if direction in {"up", "down"}:
                kwargs["direction"] = direction
            model = dsl_models.ScrollAction(**kwargs)
            return model.payload()

        if action_name == "screenshot":
            selector = _coerce_selector(action.get("target") or action.get("selector"))
            mode = action.get("mode") or "viewport"
            if selector is not None:
                model = dsl_models.ScreenshotAction(mode=mode, selector=selector, file_name=action.get("file_name"))
            else:
                model = dsl_models.ScreenshotAction(mode=mode, file_name=action.get("file_name"))
            return model.payload()

        if action_name == "extract":
            selector = _coerce_selector(action.get("target") or action.get("selector"))
            if selector is None:
                return None
            attr = action.get("attr") or action.get("attribute") or "text"
            model = dsl_models.ExtractAction(selector=selector, attr=str(attr))
            return model.payload()

        if action_name == "assert":
            selector = _coerce_selector(action.get("target") or action.get("selector"))
            if selector is None:
                return None
            state_value = action.get("state") or "visible"
            model = dsl_models.AssertAction(selector=selector, state=str(state_value))
            return model.payload()

        if action_name == "switch_tab":
            target = action.get("target") or action.get("tab")
            tab_target = _coerce_tab_target(target)
            if tab_target is None:
                return None
            model = dsl_models.SwitchTabAction(target=tab_target)
            return model.payload()

        if action_name == "focus_iframe":
            target = action.get("target") or action.get("frame")
            frame_target = _coerce_frame_target(target)
            if frame_target is None:
                return None
            model = dsl_models.FocusIframeAction(target=frame_target)
            return model.payload()

    except ValidationError:
        return None

    return None


def _legacy_normalize_action(action: Dict[str, Any]) -> Dict[str, Any]:
    normalized_action = dict(action)
    if "action" in normalized_action:
        normalized_action["action"] = str(normalized_action["action"]).lower()
    if "selector" in action and "target" not in action:
        normalized_action["target"] = action["selector"]
    elif normalized_action.get("action") == "click_text" and "text" in action and "target" not in action:
        normalized_action["target"] = action["text"]
    if "target" in normalized_action:
        normalized_action["target"] = _stringify_selector(normalized_action["target"])
    if "value" in normalized_action and not isinstance(normalized_action["value"], str):
        normalized_action["value"] = str(normalized_action["value"])
    return normalized_action

def normalize_actions(llm_response: Dict[str, Any] | None) -> NormalizedActions:
    """Normalise LLM output into typed DSL payloads with legacy fallback."""

    if not llm_response:
        return NormalizedActions(typed=[], legacy=[])

    actions = llm_response.get("actions", []) if isinstance(llm_response, dict) else None
    if not isinstance(actions, list):
        return NormalizedActions(typed=[], legacy=[])

    typed_payloads: List[Dict[str, Any]] = []
    for action in actions:
        typed = _convert_to_typed_action(action)
        if typed is None:
            typed_payloads = []
            break
        typed_payloads.append(typed)

    if typed_payloads:
        return NormalizedActions(typed=typed_payloads, legacy=[])

    legacy_payloads: List[Dict[str, Any]] = []
    for action in actions:
        if isinstance(action, dict):
            legacy_payloads.append(_legacy_normalize_action(action))

    return NormalizedActions(typed=[], legacy=legacy_payloads)


@app.route("/history", methods=["GET"])
def get_history():
    try:
        history_data = load_hist()
        return jsonify(history_data)
    except Exception as e:
        import uuid
        correlation_id = str(uuid.uuid4())[:8]
        log.error("[%s] get_history error: %s", correlation_id, e)
        # Return structured error response instead of 500
        return jsonify({
            "error": f"Failed to load history - {str(e)}",
            "correlation_id": correlation_id,
            "data": []  # Provide empty data as fallback
        }), 200


@app.route("/history.json", methods=["GET"])
def download_history():
    if os.path.exists(HIST_FILE):
        return send_from_directory(
            directory=os.path.dirname(HIST_FILE),
            path=os.path.basename(HIST_FILE),
            mimetype="application/json",
        )
    return jsonify(error="history file not found"), 404


# ----- Memory endpoint -----
@app.route("/memory", methods=["GET"])
def memory():
    try:
        history_data = load_hist()
        return jsonify(history_data)
    except Exception as e:
        import uuid
        correlation_id = str(uuid.uuid4())[:8]
        log.error("[%s] memory error: %s", correlation_id, e)
        # Return structured error response instead of 500
        return jsonify({
            "error": f"Failed to load memory - {str(e)}",
            "correlation_id": correlation_id,
            "data": []  # Provide empty data as fallback
        }), 200


# ----- Reset endpoint -----
@app.post("/reset")
def reset():
    """Reset conversation history by clearing the history file"""
    try:
        # Clear the history by saving an empty list
        save_hist([])
        log.info("Conversation history reset successfully")
        return jsonify({"status": "success", "message": "会話履歴がリセットされました"})
    except Exception as e:
        log.error("reset error: %s", e)
        return jsonify(error=str(e)), 500


def update_last_history_url(url=None):
    """Update the most recent conversation entry with the current page URL."""
    try:
        hist = load_hist()
        if not hist:
            log.debug("No conversation history found to update with URL")
            return

        # Use provided URL or fetch from VNC server
        current_url = url
        if not current_url:
            try:
                current_url = vnc_url()
            except Exception as vnc_error:
                log.error("Failed to get URL from VNC server: %s", vnc_error)
                return
        
        if current_url:  # Only update if we have a valid URL
            hist[-1]["url"] = current_url
            save_hist(hist)
            log.debug("Updated conversation history URL to: %s", current_url)
        else:
            log.warning("No valid URL available to update conversation history")
            
    except Exception as e:
        log.error("update_last_history_url error: %s", e)


# --------------- API ---------------------------------------------
@app.post("/execute")
def execute():
    data = request.get_json(force=True)
    cmd = data.get("command", "").strip()
    if not cmd:
        return jsonify(error="command empty"), 400

    page = data.get("pageSource") or vnc_html()
    shot = data.get("screenshot")
    model = data.get("model", "gemini")
    prev_error = data.get("error")
    hist = load_hist()
    current_url = data.get("url") or vnc_url()
    elements, dom_err = vnc_dom_tree()
    if elements is None:
        try:
            fallback = vnc_elements()
            elements = [
                DOMElementNode(
                    tagName=e.get("tag", ""),
                    attributes={
                        k: v
                        for k, v in {
                            "id": e.get("id"),
                            "class": e.get("class"),
                        }.items()
                        if v
                    },
                    text=e.get("text"),
                    xpath=e.get("xpath", ""),
                    highlightIndex=e.get("index"),
                    isVisible=True,
                    isInteractive=True,
                )
                for e in fallback
            ]
        except Exception as fbe:
            log.error("fallback elements error: %s", fbe)
    err_msg = "\n".join(filter(None, [prev_error, dom_err])) or None

    catalog_prompt_text = ""
    catalog_data: Dict[str, Any] = {"abbreviated": [], "metadata": {}, "catalog_version": None}
    expected_catalog_version = None
    if is_catalog_enabled():
        try:
            catalog_info = get_catalog_for_prompt(refresh=False)
            catalog_prompt_text = catalog_info.get("prompt_text", "")
            catalog_data = catalog_info.get("catalog", {}) or {}
            expected_catalog_version = catalog_data.get("catalog_version") or get_catalog_expected_version()
        except Exception as catalog_error:
            log.error("Failed to fetch element catalog: %s", catalog_error)

    prompt = build_prompt(
        cmd,
        page,
        hist,
        bool(shot),
        elements,
        err_msg,
        element_catalog_text=catalog_prompt_text,
        catalog_metadata=catalog_data,
    )
    
    # Call LLM first
    res = call_llm(prompt, model, shot)

    # Save conversation history immediately with current URL
    append_history_entry(cmd, res, current_url)
    
    # Extract and normalize actions from LLM response
    normalized_actions = normalize_actions(res)

    # If there are actions, start async Playwright execution immediately
    task_id = None
    payload: Optional[Dict[str, Any]] = None
    if normalized_actions.has_typed:
        payload = {"plan": normalized_actions.typed}
    elif normalized_actions.legacy:
        payload = {"actions": normalized_actions.legacy}

    if payload:
        if is_catalog_enabled() and expected_catalog_version:
            payload["expected_catalog_version"] = expected_catalog_version
        try:
            executor = get_preinitialized_async_executor()
            task_id = executor.create_task()
            success = executor.submit_playwright_execution(task_id, execute_dsl, payload)

            if success:
                executor.submit_parallel_data_fetch(task_id, {"updated_html": vnc_html})
                log.info("Started immediate async execution for task %s", task_id)
            else:
                log.error("Failed to start async execution")
                task_id = None
        except Exception as e:
            log.error("Error starting async execution: %s", e)
            task_id = None
    
    # Return LLM response immediately with task_id for status tracking (optimized)
    if task_id:
        # Direct field assignment instead of dict copying for speed
        res["task_id"] = task_id
        res["async_execution"] = True
    else:
        res["async_execution"] = False
    
    return jsonify(res)


@app.route("/execution-status/<task_id>", methods=["GET"])
def get_execution_status(task_id):
    """Get the status of an async execution task."""
    try:
        executor = get_async_executor()
        status = executor.get_task_status(task_id)

        if status is None:
            return jsonify({"error": "Task not found"}), 404

        # When task completes, update conversation history with current URL
        if status.get("status") == "completed":
            update_last_history_url()

        # Include all warnings without character limits
        if status and "result" in status and status["result"] and isinstance(status["result"], dict):
            if "warnings" in status["result"] and status["result"]["warnings"]:
                status["result"]["warnings"] = [_truncate_warning(warning) for warning in status["result"]["warnings"]]
        
        # Clean up old tasks periodically
        executor.cleanup_old_tasks()
        
        return jsonify(status)
        
    except Exception as e:
        import uuid
        correlation_id = str(uuid.uuid4())[:8]
        log.error("[%s] get_execution_status error: %s", correlation_id, e)
        error_warning = _truncate_warning(f"Failed to get status - {str(e)}")
        return jsonify({
            "error": error_warning,
            "correlation_id": correlation_id
        }), 200


@app.post("/store-warnings")
def store_warnings():
    """Store warnings in the last conversation history item."""
    try:
        data = request.get_json(force=True)
        warnings = data.get("warnings", [])
        
        if not warnings:
            return jsonify({"status": "success", "message": "No warnings to store"})
        
        # Process warnings without character limits (as requested)
        processed_warnings = [_truncate_warning(warning) for warning in warnings]
        
        # Load current history
        hist = load_hist()
        
        if not hist:
            log.warning("No conversation history found to update with warnings")
            return jsonify({"status": "error", "message": "No conversation history found"})
        
        # Get the last conversation item
        last_item = hist[-1]
        
        # Add warnings to the bot response, above the "complete" field
        if "bot" in last_item and isinstance(last_item["bot"], dict):
            # Make a copy of bot response to preserve order
            bot_response = last_item["bot"].copy()
            
            # Remove complete field temporarily
            complete_value = bot_response.pop("complete", True)
            
            # Add processed warnings (without character limits)
            bot_response["warnings"] = processed_warnings
            
            # Re-add complete field at the end
            bot_response["complete"] = complete_value
            
            # Update the history item
            last_item["bot"] = bot_response
            
            # Save updated history
            save_hist(hist)
            
            log.info("Added %d warnings to conversation history (character limits removed)", len(processed_warnings))
            return jsonify({"status": "success", "message": f"Stored {len(processed_warnings)} warnings"})
        else:
            log.warning("Invalid conversation history format - cannot add warnings")
            return jsonify({"status": "error", "message": "Invalid conversation history format"})
            
    except Exception as e:
        log.error("store_warnings error: %s", e)
        error_msg = _truncate_warning(f"Failed to store warnings: {str(e)}")
        return jsonify({"status": "error", "message": error_msg})


def _truncate_warning(warning_msg, max_length=None):
    """Return warning message without truncation (character limits removed)."""
    # Character limits removed for conversation history as requested
    return warning_msg


@app.post("/automation/execute-dsl")
def forward_dsl():
    payload = request.get_json(force=True)
    if not payload.get("actions"):
        return jsonify({"html": "", "warnings": []})
    try:
        res_obj = execute_dsl(payload, timeout=120)

        # Update conversation history with the current URL after execution
        update_last_history_url()

        # Include all warnings without character limits
        if res_obj and isinstance(res_obj, dict) and "warnings" in res_obj:
            res_obj["warnings"] = [_truncate_warning(warning) for warning in res_obj["warnings"]]

        return jsonify(res_obj)
    except requests.Timeout:
        log.error("forward_dsl timeout")
        timeout_warning = _truncate_warning("ERROR:auto:Request timeout - The operation took too long to complete")
        return jsonify({"html": "", "warnings": [timeout_warning]})
    except Exception as e:
        log.error("forward_dsl error: %s", e)
        error_warning = _truncate_warning(f"ERROR:auto:Communication error - {str(e)}")
        return jsonify({"html": "", "warnings": [error_warning]})


@app.route("/automation/stop-request", methods=["GET"])
def get_stop_request():
    """Get current stop request from automation server."""
    try:
        res = requests.get(f"{VNC_API}/stop-request", timeout=10)
        if res.ok:
            return jsonify(res.json())
        else:
            return jsonify(None)
    except Exception as e:
        log.error("get_stop_request error: %s", e)
        return jsonify(None)


@app.route("/automation/stop-response", methods=["POST"])
def post_stop_response():
    """Forward user response to automation server."""
    try:
        data = request.get_json(force=True)
        res = requests.post(f"{VNC_API}/stop-response", json=data, timeout=10)
        if res.ok:
            return jsonify(res.json())
        else:
            return jsonify({"status": "error", "message": "Failed to send response"})
    except Exception as e:
        log.error("post_stop_response error: %s", e)
        return jsonify({"status": "error", "message": str(e)})


@app.route("/vnc-source", methods=["GET"])
def vhtml():
    return Response(vnc_html(), mimetype="text/plain")


@app.route("/screenshot", methods=["GET"])
def get_screenshot():
    """VNCサーバーからスクリーンショットを取得してブラウザに返す"""
    try:
        res = requests.get(f"{VNC_API}/screenshot", timeout=300)
        res.raise_for_status()
        return Response(res.text, mimetype="text/plain")
    except Exception as e:
        log.error("get_screenshot error: %s", e)
        return Response("", mimetype="text/plain")


# --------------- UI エントリポイント ------------------------------
@app.route("/")
def outer():
    return render_template(
        "layout.html",
        vnc_url="http://localhost:6901/vnc.html?host=localhost&port=6901&resize=scale",
        start_url=START_URL,
        max_steps=MAX_STEPS,
    )


if __name__ == "__main__":
    import atexit
    
    # Setup cleanup on shutdown
    def cleanup():
        executor = get_async_executor()
        executor.shutdown()
        log.info("Application shutdown cleanup completed")
    
    atexit.register(cleanup)
    
    app.run(host="0.0.0.0", port=5000, debug=True)
