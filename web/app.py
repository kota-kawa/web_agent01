import os
import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
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
    get_vnc_api_base,
)
from agent.browser.dom import DOMElementNode
from agent.controller.prompt import build_prompt
from agent.controller.async_executor import get_async_executor
from agent.utils.history import load_hist, save_hist, append_history_entry
from agent.utils.html import strip_html
from agent.element_catalog import (
    actions_use_catalog_indices,
    consume_pending_prompt_messages,
    get_catalog_for_prompt,
    get_expected_version as get_catalog_expected_version,
    is_enabled as is_catalog_enabled,
    record_prompt_version,
    should_refresh_for_prompt,
)
from automation.dsl.models import ActionBase, Selector
from automation.dsl.registry import registry
from vnc.dependency_check import ensure_component_dependencies

# --------------- Flask & Logger ----------------------------------
app = Flask(__name__)
log = logging.getLogger("agent")
log.setLevel(logging.INFO)

# Validate that the Flask service has access to its declared dependencies at
# startup.  This makes missing packages surface as a clear actionable error.
ensure_component_dependencies("web", logger=log)

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
# Default to a blank page to prevent unintended navigation to external sites
START_URL = os.getenv("START_URL", "about:blank")
MAX_STEPS = int(os.getenv("MAX_STEPS", "30"))

# --------------- Conversation History ----------------------------
LOG_DIR = os.getenv("LOG_DIR", "./")
os.makedirs(LOG_DIR, exist_ok=True)
HIST_FILE = os.path.join(LOG_DIR, "conversation_history.json")


def _vnc_api_url(path: str) -> str:
    """Build a URL for the automation server using the resolved base URL."""

    base = get_vnc_api_base()
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


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


def _strip_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def _coerce_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "on"}:
            return True
        if text in {"false", "no", "0", "off"}:
            return False
    return None


def _normalize_priority(values: Any) -> List[str]:
    if isinstance(values, str):
        values = [values]
    result: List[str] = []
    if isinstance(values, Sequence):
        for item in values:
            text = str(item).strip()
            if text and text not in result:
                result.append(text)
    return result


def _parse_selector_segment(segment: str) -> Tuple[Dict[str, Any], List[str]]:
    data: Dict[str, Any] = {}
    priority: List[str] = []
    text = segment.strip()
    if not text:
        return data, priority
    lowered = text.lower()
    if lowered.startswith("css="):
        data["css"] = text.split("=", 1)[1].strip()
        priority.append("css")
        return data, priority
    if lowered.startswith("xpath="):
        data["xpath"] = text.split("=", 1)[1].strip()
        priority.append("xpath")
        return data, priority
    if lowered.startswith("text="):
        data["text"] = _strip_quotes(text.split("=", 1)[1])
        priority.append("text")
        return data, priority
    if lowered.startswith("role="):
        match = re.match(r"role=([^\[\]]+)(?:\[(name\*?=)\"([^\"]*)\"\])?", text)
        if match:
            data["role"] = match.group(1).strip()
            priority.append("role")
            if match.group(3):
                data["text"] = match.group(3)
                if "text" not in priority:
                    priority.append("text")
            return data, priority
        data["role"] = text.split("=", 1)[1].strip()
        priority.append("role")
        return data, priority
    if lowered.startswith("aria-label=") or lowered.startswith("aria_label="):
        data["aria_label"] = _strip_quotes(text.split("=", 1)[1])
        priority.append("aria_label")
        return data, priority
    if lowered.startswith("near_text=") or lowered.startswith("near-text="):
        data["near_text"] = _strip_quotes(text.split("=", 1)[1])
        priority.append("near_text")
        return data, priority
    if lowered.startswith("stable_id=") or lowered.startswith("stable-id="):
        data["stable_id"] = _strip_quotes(text.split("=", 1)[1])
        priority.append("stable_id")
        return data, priority
    if lowered.startswith("index="):
        idx = _coerce_int(text.split("=", 1)[1])
        if idx is not None and idx >= 0:
            data["index"] = idx
            priority.append("index")
        return data, priority
    data["css"] = text
    priority.append("css")
    return data, priority


def _normalize_selector_string(value: str) -> Optional[Dict[str, Any]]:
    text = value.strip()
    if not text:
        return None
    parts = [part.strip() for part in text.split("||") if part.strip()]
    combined: Dict[str, Any] = {}
    priority: List[str] = []
    for part in parts:
        fields, segment_priority = _parse_selector_segment(part)
        for key, val in fields.items():
            if key == "index":
                if "index" not in combined:
                    combined["index"] = val
            elif key not in combined:
                combined[key] = val
        for entry in segment_priority:
            if entry not in priority:
                priority.append(entry)
    if priority:
        combined["priority"] = priority
    return combined or None


def _normalize_selector_value(value: Any) -> Optional[Dict[str, Any] | Selector]:
    if value is None:
        return None
    if isinstance(value, Selector):
        return value
    if isinstance(value, dict):
        normalized: Dict[str, Any] = {}
        if "selector" in value and len(value) == 1:
            return _normalize_selector_value(value["selector"])
        mapping = {
            "css": "css",
            "xpath": "xpath",
            "text": "text",
            "role": "role",
            "aria_label": "aria_label",
            "aria-label": "aria_label",
            "ariaLabel": "aria_label",
            "near_text": "near_text",
            "nearText": "near_text",
            "stable_id": "stable_id",
            "stableId": "stable_id",
            "stable-id": "stable_id",
        }
        for source, target in mapping.items():
            if source in value and value[source] is not None:
                normalized[target] = value[source]
        if "index" in value:
            idx = _coerce_int(value.get("index"))
            if idx is not None and idx >= 0:
                normalized["index"] = idx
        if "priority" in value:
            normalized["priority"] = _normalize_priority(value["priority"])
        if "selector" in value:
            nested = _normalize_selector_value(value["selector"])
            if isinstance(nested, Selector):
                return nested
            if isinstance(nested, dict):
                for key, val in nested.items():
                    if key not in normalized:
                        normalized[key] = val
        if "name" in value and "text" not in normalized and value["name"] is not None:
            normalized["text"] = value["name"]
        if "label" in value and "text" not in normalized and value["label"] is not None:
            normalized["text"] = value["label"]
        return normalized or None
    if isinstance(value, list):
        combined: Dict[str, Any] = {}
        priority: List[str] = []
        for item in value:
            nested = _normalize_selector_value(item)
            if isinstance(nested, Selector):
                nested = nested.model_dump(exclude_none=True)
            if isinstance(nested, dict):
                for key, val in nested.items():
                    if key == "priority":
                        for entry in val:
                            if entry not in priority:
                                priority.append(entry)
                    elif key not in combined:
                        combined[key] = val
        if priority:
            combined["priority"] = priority
        return combined or None
    if isinstance(value, str):
        return _normalize_selector_string(value)
    return None


def _build_selector(value: Any) -> Optional[Selector]:
    normalized = _normalize_selector_value(value)
    if normalized is None:
        return None
    if isinstance(normalized, Selector):
        return normalized
    try:
        return Selector.model_validate(normalized)
    except Exception:
        return None


def _normalize_wait_state(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    mapping = {
        "networkidle": "networkidle",
        "network_idle": "networkidle",
        "network-idle": "networkidle",
        "load": "load",
        "domcontentloaded": "domcontentloaded",
        "dom_content_loaded": "domcontentloaded",
        "dom-content-loaded": "domcontentloaded",
        "visible": "visible",
        "hidden": "hidden",
        "attached": "attached",
        "detached": "detached",
    }
    return mapping.get(text)


def _normalize_assert_state(value: Any) -> str:
    state = _normalize_wait_state(value)
    if state in {"visible", "hidden", "attached", "detached"}:
        return state
    return "visible"


def _build_wait_condition(condition: Any, fallback: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if condition is None:
        return None
    if isinstance(condition, Selector):
        return {"selector": condition, "state": "visible"}
    if isinstance(condition, dict):
        if "selector" in condition or "target" in condition:
            selector_value = condition.get("selector") or condition.get("target")
            selector = _build_selector(selector_value)
            if selector is None:
                return None
            state = _normalize_wait_state(condition.get("state") or condition.get("visibility"))
            if not state:
                state = "visible"
            return {"selector": selector, "state": state}
        cond_type = condition.get("type") or condition.get("kind")
        if cond_type:
            normalized_type = str(cond_type).strip().lower()
            if normalized_type in {"selector", "element"}:
                selector_value = (
                    condition.get("selector")
                    or condition.get("target")
                    or fallback.get("target")
                )
                selector = _build_selector(selector_value)
                if selector is None:
                    return None
                state = _normalize_wait_state(condition.get("state") or condition.get("visibility"))
                if not state:
                    state = "visible"
                return {"selector": selector, "state": state}
            if normalized_type in {"state", "load_state"}:
                state = _normalize_wait_state(condition.get("state") or condition.get("value"))
                if state in {"load", "domcontentloaded", "networkidle"}:
                    return {"state": state}
            if normalized_type in {"timeout", "delay"}:
                timeout_val = _coerce_int(
                    condition.get("timeout_ms")
                    or condition.get("value")
                    or condition.get("ms")
                )
                if timeout_val is not None:
                    return {"timeout_ms": max(0, timeout_val)}
        state = _normalize_wait_state(condition.get("state") or condition.get("value"))
        if state in {"load", "domcontentloaded", "networkidle"}:
            return {"state": state}
        timeout_val = _coerce_int(
            condition.get("timeout_ms") or condition.get("ms") or condition.get("value")
        )
        if timeout_val is not None:
            return {"timeout_ms": max(0, timeout_val)}
        nested = condition.get("for") or condition.get("condition") or condition.get("until")
        if nested is not None:
            return _build_wait_condition(nested, fallback)
    if isinstance(condition, str):
        normalized = condition.strip().lower().replace("-", "_")
        if normalized in {"load", "domcontentloaded", "dom_content_loaded", "networkidle", "network_idle"}:
            state = _normalize_wait_state(normalized)
            if state:
                return {"state": state}
        if normalized in {"selector", "element"}:
            selector = _build_selector(
                fallback.get("selector") or fallback.get("target") or fallback.get("value")
            )
            if selector is None:
                return None
            state = _normalize_wait_state(fallback.get("state") or fallback.get("visibility"))
            if not state:
                state = "visible"
            return {"selector": selector, "state": state}
        if normalized in {"timeout", "time", "sleep"}:
            timeout_val = _coerce_int(
                fallback.get("timeout_ms") or fallback.get("ms") or fallback.get("value")
            )
            if timeout_val is not None:
                return {"timeout_ms": max(0, timeout_val)}
    if isinstance(condition, (int, float)):
        return {"timeout_ms": max(0, int(condition))}
    return None


def _parse_scroll_target(value: Any) -> Optional[Any]:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        lowered = text.lower()
        if lowered in {"top", "bottom"}:
            return lowered
        amount = _coerce_int(text)
        if amount is not None:
            return amount
        selector = _build_selector(text)
        if selector:
            return {"selector": selector}
    if isinstance(value, dict):
        target_selector = _build_selector(value.get("selector") or value.get("target"))
        container_selector = _build_selector(value.get("container"))
        axis = value.get("axis") if value.get("axis") in {"vertical", "horizontal", "both"} else None
        align = value.get("align") if value.get("align") in {"start", "center", "end", "nearest"} else None
        behavior = value.get("behavior") if value.get("behavior") in {"auto", "instant", "smooth"} else None
        payload: Dict[str, Any] = {}
        if target_selector:
            payload["selector"] = target_selector
        if container_selector:
            payload["container"] = container_selector
        if axis:
            payload["axis"] = axis
        if align:
            payload["align"] = align
        if behavior:
            payload["behavior"] = behavior
        return payload or None
    if isinstance(value, list):
        for item in value:
            parsed = _parse_scroll_target(item)
            if parsed is not None:
                return parsed
    return None


def _prepare_wait_action(raw: Dict[str, Any], *, force_selector: bool = False) -> Dict[str, Any]:
    prepared: Dict[str, Any] = {"action": "wait"}
    timeout_val = _coerce_int(raw.get("timeout_ms") or raw.get("ms") or raw.get("value"))
    if timeout_val is not None and timeout_val >= 0:
        prepared["timeout_ms"] = timeout_val
    condition_source = raw.get("for") or raw.get("condition")
    if condition_source is None:
        condition_source = raw.get("until")
    condition_payload = _build_wait_condition(condition_source, raw)
    if force_selector and condition_payload is None:
        selector = _build_selector(raw.get("selector") or raw.get("target") or raw.get("value"))
        if selector:
            state = _normalize_wait_state(raw.get("state") or raw.get("visibility"))
            if not state:
                state = "visible"
            condition_payload = {"selector": selector, "state": state}
    if condition_payload:
        if "selector" in condition_payload and "state" not in condition_payload:
            condition_payload["state"] = "visible"
        prepared["for"] = condition_payload
    return prepared


def _coerce_keys(keys_value: Any, single_key: Any) -> List[str]:
    keys: List[str] = []
    if isinstance(keys_value, list):
        for item in keys_value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                keys.append(text)
    elif isinstance(keys_value, str):
        keys = [part.strip() for part in keys_value.split("+") if part.strip()]
    elif keys_value is not None:
        text = str(keys_value).strip()
        if text:
            keys.append(text)
    if not keys and single_key:
        if isinstance(single_key, str):
            keys = [part.strip() for part in single_key.split("+") if part.strip()]
        else:
            text = str(single_key).strip()
            if text:
                keys.append(text)
    return keys


def _prepare_action(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    action_name = str(raw.get("action") or raw.get("type") or "").strip().lower()
    if not action_name:
        return None

    if action_name == "navigate":
        url = raw.get("url") or raw.get("target")
        if not url:
            return None
        prepared: Dict[str, Any] = {"action": "navigate", "url": str(url)}
        wait_for = raw.get("wait_for")
        wait_condition = _build_wait_condition(wait_for, raw) if wait_for is not None else None
        if wait_condition:
            prepared["wait_for"] = wait_condition
        return prepared

    if action_name in {"click", "click_text"}:
        selector_source = raw.get("selector") or raw.get("target")
        if action_name == "click_text":
            text_value = raw.get("text") or selector_source
            if text_value:
                selector_source = {"text": text_value, "priority": ["text"]}
        selector = _build_selector(selector_source)
        if selector is None:
            return None
        prepared = {"action": "click", "selector": selector}
        button_value = raw.get("button")
        if button_value:
            button_text = str(button_value).strip().lower()
            if button_text in {"left", "right", "middle"}:
                prepared["button"] = button_text
        click_count = _coerce_int(raw.get("click_count") or raw.get("clicks") or raw.get("count"))
        if click_count is not None and click_count > 1:
            prepared["click_count"] = click_count
        delay = _coerce_int(raw.get("delay_ms") or raw.get("delay"))
        if delay is not None and delay >= 0:
            prepared["delay_ms"] = delay
        return prepared

    if action_name == "hover":
        selector = _build_selector(raw.get("selector") or raw.get("target"))
        if selector is None:
            return None
        return {"action": "hover", "selector": selector}

    if action_name == "type":
        selector = _build_selector(raw.get("selector") or raw.get("target"))
        if selector is None:
            return None
        text_value = raw.get("text")
        if text_value is None:
            text_value = raw.get("value") or ""
        prepared = {"action": "type", "selector": selector, "text": str(text_value)}
        press_enter = _coerce_bool(raw.get("press_enter") or raw.get("enter"))
        if press_enter is not None:
            prepared["press_enter"] = press_enter
        clear_flag = _coerce_bool(raw.get("clear"))
        if clear_flag is not None:
            prepared["clear"] = clear_flag
        return prepared

    if action_name in {"select_option", "select"}:
        selector = _build_selector(raw.get("selector") or raw.get("target"))
        if selector is None:
            return None
        value = raw.get("value")
        if value is None:
            value = raw.get("label") or raw.get("option") or raw.get("text")
        if value is None:
            return None
        return {"action": "select", "selector": selector, "value_or_label": str(value)}

    if action_name == "press_key":
        keys = _coerce_keys(raw.get("keys") or raw.get("hotkeys"), raw.get("key"))
        if not keys:
            return None
        prepared = {"action": "press_key", "keys": keys}
        scope_value = raw.get("scope") or raw.get("target_scope")
        if scope_value:
            scope_text = str(scope_value).strip().lower()
            if scope_text in {"active_element", "page"}:
                prepared["scope"] = scope_text
        return prepared

    if action_name == "wait":
        return _prepare_wait_action(raw)

    if action_name == "wait_for_selector":
        return _prepare_wait_action(raw, force_selector=True)

    if action_name == "scroll":
        prepared: Dict[str, Any] = {"action": "scroll"}
        container = _build_selector(raw.get("container") or raw.get("target"))
        if container:
            prepared["container"] = container
        to_payload = _parse_scroll_target(raw.get("to"))
        if to_payload is None:
            amount = raw.get("amount")
            if amount is None:
                amount = raw.get("value")
            to_payload = _parse_scroll_target(amount)
        if to_payload is not None:
            prepared["to"] = to_payload
        else:
            direction_value = raw.get("direction")
            if direction_value:
                direction_text = str(direction_value).strip().lower()
                if direction_text in {"up", "down"}:
                    prepared["direction"] = direction_text
        return prepared

    if action_name == "scroll_to_text":
        text_value = raw.get("text") or raw.get("target") or raw.get("value")
        if text_value is None:
            return None
        return {"action": "scroll_to_text", "text": str(text_value)}

    if action_name == "eval_js":
        script = raw.get("script") or raw.get("value")
        if script is None:
            return None
        return {"action": "eval_js", "script": str(script)}

    if action_name == "click_blank_area":
        return {"action": "click_blank_area"}

    if action_name == "close_popup":
        return {"action": "close_popup"}

    if action_name == "refresh_catalog":
        return {"action": "refresh_catalog"}

    if action_name == "stop":
        reason = raw.get("reason") or "user_intervention"
        message = raw.get("message") or ""
        return {"action": "stop", "reason": str(reason), "message": str(message)}

    if action_name == "screenshot":
        prepared = {"action": "screenshot"}
        mode_value = raw.get("mode")
        mode = str(mode_value).strip().lower() if isinstance(mode_value, str) else None
        if mode in {"viewport", "full", "full_page", "fullpage", "element"}:
            prepared["mode"] = "full" if mode in {"full_page", "fullpage"} else mode
        elif _coerce_bool(raw.get("full_page")):
            prepared["mode"] = "full"
        else:
            prepared["mode"] = "viewport"
        selector_source = raw.get("selector")
        if selector_source is None and prepared["mode"] == "element":
            selector_source = raw.get("target")
        selector = _build_selector(selector_source)
        if selector:
            prepared["selector"] = selector
            prepared["mode"] = "element"
        file_name = raw.get("file_name") or raw.get("filename")
        if file_name:
            prepared["file_name"] = str(file_name)
        return prepared

    if action_name in {"extract_text", "extract"}:
        selector = _build_selector(raw.get("selector") or raw.get("target"))
        if selector is None:
            return None
        attr_value = raw.get("attr") or raw.get("attribute")
        attr_text = str(attr_value).strip().lower() if attr_value else "text"
        attr_mapping = {
            "text": "text",
            "inner_text": "text",
            "innertext": "text",
            "value": "value",
            "href": "href",
            "html": "html",
            "inner_html": "html",
            "innerhtml": "html",
        }
        attr = attr_mapping.get(attr_text, "text")
        return {"action": "extract", "selector": selector, "attr": attr}

    if action_name == "assert":
        selector = _build_selector(raw.get("selector") or raw.get("target"))
        if selector is None:
            return None
        state = _normalize_assert_state(raw.get("state"))
        return {"action": "assert", "selector": selector, "state": state}

    if action_name == "switch_tab":
        target_data = raw.get("target") or raw.get("tab") or {}
        if not isinstance(target_data, dict):
            target_data = {"strategy": "index", "value": target_data}
        strategy = str(
            target_data.get("strategy")
            or target_data.get("by")
            or target_data.get("type")
            or ""
        ).strip().lower()
        if not strategy:
            if "index" in target_data:
                strategy = "index"
            elif "url" in target_data:
                strategy = "url"
            elif "title" in target_data:
                strategy = "title"
            elif "direction" in target_data:
                strategy = str(target_data["direction"]).strip().lower()
        if strategy in {"previous", "next", "latest"}:
            target_payload = {"strategy": strategy}
        else:
            if strategy not in {"index", "url", "title"}:
                strategy = "index"
            value = target_data.get("value")
            if strategy == "index":
                if value is None:
                    value = target_data.get("index")
                value = _coerce_int(value)
                if value is None:
                    value = 0
            elif strategy == "url":
                if value is None:
                    value = target_data.get("url")
                if value is not None:
                    value = str(value)
            elif strategy == "title":
                if value is None:
                    value = target_data.get("title")
                if value is not None:
                    value = str(value)
            target_payload = {"strategy": strategy}
            if value is not None:
                target_payload["value"] = value
        return {"action": "switch_tab", "target": target_payload}

    if action_name == "focus_iframe":
        target_data = raw.get("target") or raw.get("frame") or {}
        if not isinstance(target_data, dict):
            target_data = {"strategy": "index", "value": target_data}
        strategy = str(
            target_data.get("strategy")
            or target_data.get("by")
            or target_data.get("type")
            or ""
        ).strip().lower()
        if not strategy:
            if "index" in target_data:
                strategy = "index"
            elif "name" in target_data:
                strategy = "name"
            elif "url" in target_data:
                strategy = "url"
            elif "selector" in target_data or "target" in target_data:
                strategy = "element"
            elif str(target_data).lower() in {"parent", "root"}:
                strategy = str(target_data).lower()
        if strategy in {"parent", "root"}:
            target_payload = {"strategy": strategy}
        else:
            if strategy not in {"index", "name", "url", "element"}:
                strategy = "index"
            value = target_data.get("value")
            if strategy == "index":
                if value is None:
                    value = target_data.get("index")
                value = _coerce_int(value)
                if value is None:
                    value = 0
            elif strategy == "name":
                if value is None:
                    value = target_data.get("name")
                if value is not None:
                    value = str(value)
            elif strategy == "url":
                if value is None:
                    value = target_data.get("url")
                if value is not None:
                    value = str(value)
            elif strategy == "element":
                selector = _build_selector(target_data.get("selector") or target_data.get("target"))
                value = selector if selector else None
            target_payload = {"strategy": strategy}
            if value is not None:
                target_payload["value"] = value
        return {"action": "focus_iframe", "target": target_payload}

    return None


def normalize_actions(llm_response):
    """Parse LLM output into typed DSL actions."""
    if not llm_response:
        return []

    actions = llm_response.get("actions", [])
    if not isinstance(actions, list):
        return []

    parsed: List[ActionBase] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        prepared = _prepare_action(action)
        if not prepared:
            continue
        try:
            parsed_action = registry.parse_action(prepared)
        except Exception as exc:
            log.debug("Failed to parse action %s: %s", prepared.get("action"), exc)
            continue
        parsed.append(parsed_action)
    return parsed




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


def update_last_history_url(url=None, history_entry_id=None):
    """Update the most recent conversation entry with the current page URL."""
    try:
        hist = load_hist()
        if not hist:
            log.debug("No conversation history found to update with URL")
            return

        entry_index = history_entry_id
        if entry_index is None:
            entry_index = len(hist) - 1

        if entry_index is None or entry_index < 0 or entry_index >= len(hist):
            log.debug(
                "Conversation history entry %s not available for URL update",
                entry_index,
            )
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
            hist[entry_index]["url"] = current_url
            save_hist(hist)
            log.debug(
                "Updated conversation history URL for entry %s to: %s",
                entry_index,
                current_url,
            )
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
    pending_catalog_messages = consume_pending_prompt_messages() if is_catalog_enabled() else []
    if pending_catalog_messages:
        combined = "\n".join(pending_catalog_messages)
        log.debug("Catalog advisory added to prompt: %s", combined)
        err_msg = "\n".join(filter(None, [err_msg, combined]))

    catalog_prompt_text = ""
    catalog_data: Dict[str, Any] = {"abbreviated": [], "metadata": {}, "catalog_version": None}
    expected_catalog_version = None
    if is_catalog_enabled():
        try:
            refresh_catalog = should_refresh_for_prompt()
            if refresh_catalog:
                log.debug("Forcing element catalog refresh before prompting")
            catalog_info = get_catalog_for_prompt(refresh=refresh_catalog)
            catalog_prompt_text = catalog_info.get("prompt_text", "")
            catalog_data = catalog_info.get("catalog", {}) or {}
            expected_catalog_version = catalog_data.get("catalog_version") or get_catalog_expected_version()
            record_prompt_version(expected_catalog_version)
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
    history_entry_id = append_history_entry(cmd, res, current_url)
    
    # Extract and normalize actions from LLM response
    actions = normalize_actions(res)
    legacy_actions = [action.legacy_payload() for action in actions]
    uses_catalog_indices = (
        bool(legacy_actions)
        and is_catalog_enabled()
        and actions_use_catalog_indices(legacy_actions)
    )
    if uses_catalog_indices:
        log.debug("Planned actions rely on catalog indices")

    # If there are actions, start async Playwright execution immediately (optimized)
    task_id = None
    if actions:
        try:
            # Use pre-initialized executor for immediate execution
            executor = get_preinitialized_async_executor()
            task_id = executor.create_task(history_entry_id=history_entry_id)

            # Start Playwright execution in parallel (immediate submission)
            plan_payload = {"actions": [action.payload(by_alias=True) for action in actions]}
            payload_extra: Dict[str, Any] = {"plan": plan_payload, "run_id": task_id}
            if is_catalog_enabled() and expected_catalog_version:
                payload_extra["expected_catalog_version"] = expected_catalog_version
            success = executor.submit_playwright_execution(
                task_id,
                execute_dsl,
                legacy_actions,
                payload=payload_extra,
            )
            
            if success:
                # Start parallel data fetching immediately (no delay)
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
            update_last_history_url(history_entry_id=status.get("history_entry_id"))

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
        res = requests.get(_vnc_api_url("/stop-request"), timeout=10)
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
        res = requests.post(_vnc_api_url("/stop-response"), json=data, timeout=10)
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
        res = requests.get(_vnc_api_url("/screenshot"), timeout=300)
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
