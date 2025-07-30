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
    """Execute arbitrary JavaScript in the page context."""
    return {"action": "eval_js", "script": script}
