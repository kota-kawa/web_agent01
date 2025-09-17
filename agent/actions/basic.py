"""Action helpers used by the controller."""

from typing import Dict


def click(target: str) -> Dict:
    return {"action": "click", "target": target}


def click_text(text: str) -> Dict:
    return {"action": "click_text", "text": text, "target": text}


def navigate(url: str) -> Dict:
    return {"action": "navigate", "target": url}


def type_text(target: str, value: str) -> Dict:
    return {"action": "type", "target": target, "value": value}


def wait(ms: int = 500, retry: int | None = None) -> Dict:
    act = {"action": "wait", "ms": ms}
    if retry is not None:
        act["retry"] = retry
    return act


def wait_for_selector(target: str, ms: int = 3000) -> Dict:
    return {"action": "wait_for_selector", "target": target, "ms": ms}


def go_back() -> Dict:
    return {"action": "go_back"}


def go_forward() -> Dict:
    return {"action": "go_forward"}


def hover(target: str) -> Dict:
    return {"action": "hover", "target": target}


def select_option(target: str, value: str) -> Dict:
    return {"action": "select_option", "target": target, "value": value}


def press_key(key: str, target: str | None = None) -> Dict:
    act = {"action": "press_key", "key": key}
    if target:
        act["target"] = target
    return act


def extract_text(target: str) -> Dict:
    return {"action": "extract_text", "target": target}


def eval_js(script: str) -> Dict:
    """Execute JavaScript in the page and store the result.

    Use this when built-in actions cannot express a complex operation or when
    page state must be inspected via DOM APIs.  The returned value is recorded
    by the automation server and can be fetched with :func:`get_eval_results`.
    """
    return {"action": "eval_js", "script": script}


def stop(reason: str, message: str = "") -> Dict:
    """Stop execution and wait for user input.
    
    Use this when the LLM needs user confirmation, advice, or intervention.
    Examples: captcha solving, date/price confirmations, repeated failures.
    
    Args:
        reason: Type of stop (e.g., "captcha", "confirmation", "repeated_failures")
        message: Optional message to display to the user
    """
    return {"action": "stop", "reason": reason, "message": message}


def click_blank_area() -> Dict:
    """Click on a blank area of the page to close popups.
    
    This action finds an empty area on the page and clicks it, which is useful
    for closing popups/modals that don't require specific element selectors.
    
    Returns:
        Dictionary representing a blank area click action
    """
    return {"action": "click_blank_area"}


def close_popup() -> Dict:
    """Close popups by clicking on blank areas.

    Uses popup detection and blank area clicking to close modals/overlays
    without needing to target specific close buttons or elements.

    Returns:
        Dictionary representing a popup close action
    """
    return {"action": "close_popup"}


def refresh_catalog() -> Dict:
    """Refresh the element catalog prior to issuing index-based commands."""
    return {"action": "refresh_catalog"}


def scroll_to_text(text: str) -> Dict:
    """Scroll to the area containing the specified text snippet."""
    return {"action": "scroll_to_text", "target": text}
