"""Action helpers used by the controller."""

from __future__ import annotations

from typing import Dict

from automation.dsl import models


def _legacy_payload(action: models.ActionBase) -> Dict:
    """Convert a typed action model into the legacy controller payload."""

    return action.legacy_payload()


def click(target: str) -> Dict:
    return _legacy_payload(models.ClickAction(selector=target))


def click_text(text: str) -> Dict:
    # Legacy helper retained for compatibility with existing prompts.
    return {"action": "click_text", "text": text, "target": text}


def navigate(url: str) -> Dict:
    return _legacy_payload(models.NavigateAction(url=url))


def type_text(target: str, value: str) -> Dict:
    return _legacy_payload(models.TypeAction(selector=target, text=value))


def wait(ms: int = 500, retry: int | None = None) -> Dict:
    action = models.WaitAction(timeout_ms=ms)
    payload = action.legacy_payload()
    if retry is not None:
        payload["retry"] = retry
    return payload


def wait_for_selector(target: str, ms: int = 3000) -> Dict:
    cond = models.WaitForSelector(selector=target)
    action = models.WaitAction(for_=cond, timeout_ms=ms)
    payload = action.legacy_payload()
    payload.setdefault("target", target)
    return payload


def go_back() -> Dict:
    return {"action": "go_back"}


def go_forward() -> Dict:
    return {"action": "go_forward"}


def hover(target: str) -> Dict:
    return _legacy_payload(models.HoverAction(selector=target))


def select_option(target: str, value: str) -> Dict:
    return _legacy_payload(models.SelectAction(selector=target, value_or_label=value))


def press_key(key: str, target: str | None = None) -> Dict:
    action = models.PressKeyAction(keys=[key])
    payload = action.legacy_payload()
    if target:
        payload["target"] = target
    return payload


def extract_text(target: str) -> Dict:
    return _legacy_payload(models.ExtractAction(selector=target))


def eval_js(script: str) -> Dict:
    """Execute JavaScript in the page and store the result.

    Use this when built-in actions cannot express a complex operation or when
    page state must be inspected via DOM APIs.  The returned value is recorded
    by the automation server and can be fetched with :func:`get_eval_results`.
    """
    return _legacy_payload(models.EvalJsAction(script=script))


def stop(reason: str, message: str = "") -> Dict:
    """Stop execution and wait for user input.
    
    Use this when the LLM needs user confirmation, advice, or intervention.
    Examples: captcha solving, date/price confirmations, repeated failures.
    
    Args:
        reason: Type of stop (e.g., "captcha", "confirmation", "repeated_failures")
        message: Optional message to display to the user
    """
    return _legacy_payload(models.StopAction(reason=reason, message=message))


def click_blank_area() -> Dict:
    """Click on a blank area of the page to close popups.
    
    This action finds an empty area on the page and clicks it, which is useful
    for closing popups/modals that don't require specific element selectors.
    
    Returns:
        Dictionary representing a blank area click action
    """
    return _legacy_payload(models.ClickBlankAreaAction())


def close_popup() -> Dict:
    """Close popups by clicking on blank areas.

    Uses popup detection and blank area clicking to close modals/overlays
    without needing to target specific close buttons or elements.

    Returns:
        Dictionary representing a popup close action
    """
    return _legacy_payload(models.ClosePopupAction())


def refresh_catalog() -> Dict:
    """Refresh the element catalog prior to issuing index-based commands."""
    return _legacy_payload(models.RefreshCatalogAction())


def scroll_to_text(text: str) -> Dict:
    """Scroll to the area containing the specified text snippet."""
    return _legacy_payload(models.ScrollToTextAction(text=text))
