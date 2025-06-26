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


def wait(ms: int = 500) -> Dict:
    return {"action": "wait", "ms": ms}
