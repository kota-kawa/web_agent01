"""Shared interaction helpers for robust element manipulation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

from playwright.async_api import Locator, Page

log = logging.getLogger(__name__)

DEFAULT_ACTION_TIMEOUT = 10_000

_TEXT_INPUT_SELECTOR = (
    "input:not([type='hidden']):not([disabled]):not([readonly]), "
    "textarea:not([disabled]):not([readonly]), "
    "[contenteditable='true']"
)
_TEXT_INPUT_SELECTOR = "".join(_TEXT_INPUT_SELECTOR)


async def prepare_locator(page: Page, locator: Locator, timeout: Optional[int] = None) -> Locator:
    """Ensure the locator points to an interactable element."""

    del page  # Only required for a consistent signature; retained for future use.

    timeout = timeout if timeout is not None else DEFAULT_ACTION_TIMEOUT
    target = locator
    await target.wait_for(state="attached", timeout=timeout)
    await target.scroll_into_view_if_needed(timeout=timeout)
    await target.wait_for(state="visible", timeout=timeout)
    if not await target.is_enabled():
        raise Exception("Element is not enabled for interaction")
    return target


async def safe_click(
    page: Page,
    locator: Locator,
    *,
    force: bool = False,
    timeout: Optional[int] = None,
    button: str = "left",
    click_count: int = 1,
    delay_ms: Optional[int] = None,
) -> None:
    """Click an element using multiple fallback strategies."""

    timeout = timeout if timeout is not None else DEFAULT_ACTION_TIMEOUT
    target = await prepare_locator(page, locator, timeout)

    try:
        await target.hover(timeout=timeout)
        await asyncio.sleep(0.1)
        await target.click(
            timeout=timeout,
            force=force,
            button=button,
            click_count=click_count,
            delay=delay_ms,
        )
    except Exception as exc:
        if not force:
            log.warning("Click retry with force due to: %s", exc)
            try:
                await target.click(
                    timeout=timeout,
                    force=True,
                    button=button,
                    click_count=click_count,
                    delay=delay_ms,
                )
            except Exception as force_error:
                try:
                    await target.evaluate("el => el.click()")
                except Exception as js_error:
                    raise Exception(
                        "Click failed - Original: {orig}, Force: {force}, JS: {js}".format(
                            orig=exc, force=force_error, js=js_error
                        )
                    )
        else:
            raise


async def safe_fill(
    page: Page,
    locator: Locator,
    value: str,
    *,
    timeout: Optional[int] = None,
    original_target: str = "",
) -> None:
    """Fill text into an input using robust discovery and fallbacks."""

    timeout = timeout if timeout is not None else DEFAULT_ACTION_TIMEOUT
    retry_error: Optional[Exception] = None
    target = locator

    try:
        metadata = await _describe_element_for_typing(target)
        if not _element_is_text_editable(metadata):
            fallback_loc, fallback_reason = await _find_text_input_fallback(page, target)
            if fallback_loc is not None:
                log.info(
                    "Resolved non-editable target '%s' to nearby input using %s fallback (%s)",
                    original_target or "<unknown>",
                    fallback_reason or "unknown",
                    _summarize_element(await _describe_element_for_typing(fallback_loc)),
                )
                target = fallback_loc
                metadata = await _describe_element_for_typing(target)
                if not _element_is_text_editable(metadata):
                    summary = _summarize_element(metadata)
                    raise Exception(
                        "Resolved element is still not text-editable ("
                        + summary
                        + "). Try a more specific input selector."
                    )
            else:
                summary = _summarize_element(metadata)
                raise Exception(
                    "Target is not text-editable (" + summary + ") "
                    "Use a selector that points to an input/textarea element or click the link instead."
                )

        await target.wait_for(state="attached", timeout=timeout)

        element_visible = True
        try:
            element_visible = await target.is_visible()
        except Exception:
            element_visible = True

        if not element_visible:
            try:
                await target.wait_for(state="visible", timeout=min(1000, timeout))
                element_visible = True
            except Exception:
                element_visible = False

        if not element_visible:
            raise Exception("Element not visible for direct fill interaction")

        interactable = await prepare_locator(page, target, timeout)

        await interactable.click(timeout=timeout)
        await interactable.fill("", timeout=timeout)
        await interactable.fill(value, timeout=timeout)

        current_val = await interactable.input_value()
        if current_val != value:
            await interactable.click(timeout=timeout)
            await interactable.press("Control+a")
            await interactable.type(value, delay=50)

    except Exception as exc:
        log.warning("Fill retry with alternative method due to: %s", exc)
        try:
            element_visible = await target.is_visible()
        except Exception:
            element_visible = False

        if element_visible:
            try:
                interactable = await prepare_locator(page, target, timeout)
                await interactable.click(timeout=timeout)
                await interactable.fill("", timeout=timeout)
                await interactable.fill(value, timeout=timeout)
                current_val = await interactable.input_value()
                if current_val != value:
                    await interactable.click(timeout=timeout)
                    await interactable.press("Control+a")
                    await interactable.type(value, delay=50)
                return
            except Exception as alternative_error:
                retry_error = alternative_error

        try:
            await target.evaluate(
                """
                (el, value) => {
                    if (!el) {
                        return;
                    }
                    const proto = Object.getPrototypeOf(el);
                    const descriptor = proto && Object.getOwnPropertyDescriptor(proto, 'value');
                    if (descriptor && descriptor.set) {
                        descriptor.set.call(el, value);
                    } else {
                        el.value = value;
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
                """,
                value,
            )
        except Exception as js_error:
            extra = f", Retry: {retry_error}" if retry_error else ""
            raise Exception(
                f"Fill failed - Original: {exc}{extra}, JS: {js_error}"
            )


async def safe_hover(page: Page, locator: Locator, *, timeout: Optional[int] = None) -> None:
    """Hover over an element with fallbacks for tricky cases."""

    timeout = timeout if timeout is not None else DEFAULT_ACTION_TIMEOUT
    target = await prepare_locator(page, locator, timeout)

    try:
        await target.hover(timeout=timeout)
    except Exception as exc:
        log.warning("Hover retry with alternative methods due to: %s", exc)
        try:
            await target.hover(timeout=timeout, force=True)
            log.info("Hover fallback successful: force hover")
            return
        except Exception as force_error:
            try:
                await target.evaluate(
                    """
                    el => {
                        el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true, cancelable: true}));
                        el.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true, cancelable: true}));
                    }
                    """
                )
                log.info("Hover fallback successful: JavaScript events")
                return
            except Exception as js_error:
                try:
                    box = await target.bounding_box()
                    if box:
                        await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        log.info("Hover fallback successful: position-based hover")
                        return
                except Exception:
                    pass
                log.error(
                    "All hover fallback methods failed - Original: %s, Force: %s, JS: %s",
                    exc,
                    force_error,
                    js_error,
                )
                raise Exception(
                    f"Hover failed - Original: {exc}, Force: {force_error}, JS: {js_error}"
                )


async def safe_select(page: Page, locator: Locator, value: str, *, timeout: Optional[int] = None) -> None:
    """Select an option using resilient strategies."""

    timeout = timeout if timeout is not None else DEFAULT_ACTION_TIMEOUT
    target = await prepare_locator(page, locator, timeout)

    try:
        await target.select_option(value, timeout=timeout)
    except Exception as exc:
        log.warning("Select retry with alternative methods due to: %s", exc)
        try:
            await target.select_option(label=value, timeout=timeout)
            log.info("Select fallback successful: label-based selection for '%s'", value)
            return
        except Exception as label_error:
            try:
                await target.evaluate(
                    f"""
                    el => {{
                        for (let option of el.options) {{
                            if (option.value === '{value}' || option.text === '{value}') {{
                                option.selected = true;
                                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                                return;
                            }}
                        }}
                        for (let option of el.options) {{
                            if (option.value.includes('{value}') || option.text.includes('{value}')) {{
                                option.selected = true;
                                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                                return;
                            }}
                        }}
                        throw new Error('No matching option found for: {value}');
                    }}
                    """
                )
                log.info("Select fallback successful: JavaScript selection for '%s'", value)
                return
            except Exception as js_error:
                try:
                    await target.click(timeout=timeout)
                    await asyncio.sleep(0.2)
                    option_loc = page.locator(
                        f"option[value='{value}'], option:has-text('{value}')"
                    )
                    await option_loc.first.click(timeout=timeout)
                    log.info("Select fallback successful: click-based selection for '%s'", value)
                    return
                except Exception as click_error:
                    log.error(
                        "All select fallback methods failed - Original: %s, Label: %s, JS: %s, Click: %s",
                        exc,
                        label_error,
                        js_error,
                        click_error,
                    )
                    raise Exception(
                        "Select failed - Original: {orig}, Label: {label}, JS: {js}, Click: {click}".format(
                            orig=exc, label=label_error, js=js_error, click=click_error
                        )
                    )


async def safe_press(page: Page, locator: Locator, key: str, *, timeout: Optional[int] = None) -> None:
    """Press a key on an element with several fallbacks."""

    timeout = timeout if timeout is not None else DEFAULT_ACTION_TIMEOUT
    target = await prepare_locator(page, locator, timeout)

    try:
        await target.press(key, timeout=timeout)
    except Exception as exc:
        log.warning("Key press retry with alternative methods due to: %s", exc)
        try:
            await target.focus(timeout=timeout)
            await asyncio.sleep(0.1)
            await target.press(key, timeout=timeout)
            log.info("Key press fallback successful: focus+press for '%s'", key)
            return
        except Exception as focus_error:
            try:
                await page.keyboard.press(key)
                log.info("Key press fallback successful: page-level press for '%s'", key)
                return
            except Exception as page_error:
                try:
                    key_code = _get_key_code(key)
                    await target.evaluate(
                        f"""
                        el => {{
                            el.focus();
                            const event = new KeyboardEvent('keydown', {{
                                key: '{key}',
                                keyCode: {key_code},
                                bubbles: true,
                                cancelable: true
                            }});
                            el.dispatchEvent(event);
                            const eventUp = new KeyboardEvent('keyup', {{
                                key: '{key}',
                                keyCode: {key_code},
                                bubbles: true,
                                cancelable: true
                            }});
                            el.dispatchEvent(eventUp);
                        }}
                        """
                    )
                    log.info("Key press fallback successful: JavaScript events for '%s'", key)
                    return
                except Exception as js_error:
                    log.error(
                        "All key press fallback methods failed - Original: %s, Focus: %s, Page: %s, JS: %s",
                        exc,
                        focus_error,
                        page_error,
                        js_error,
                    )
                    raise Exception(
                        "Key press failed - Original: {orig}, Focus: {focus}, Page: {page_err}, JS: {js}".format(
                            orig=exc,
                            focus=focus_error,
                            page_err=page_error,
                            js=js_error,
                        )
                    )


async def _describe_element_for_typing(locator: Locator) -> Dict[str, Any]:
    try:
        target = locator.nth(0)
        info = await target.evaluate(
            """
            (el) => {
                const tag = el.tagName ? el.tagName.toLowerCase() : '';
                const type = (el.type || '').toLowerCase ? (el.type || '').toLowerCase() : '';
                const role = (el.getAttribute('role') || '').toLowerCase();
                return {
                    tag,
                    type,
                    role,
                    name: el.getAttribute('name') || '',
                    id: el.id || '',
                    placeholder: el.getAttribute('placeholder') || '',
                    disabled: !!el.disabled,
                    readOnly: !!el.readOnly,
                    contentEditable:
                        el.isContentEditable ||
                        (el.getAttribute('contenteditable') || '').toLowerCase() === 'true'
                };
            }
            """
        )
        if not isinstance(info, dict):
            return {}
        info["tag"] = (info.get("tag") or "").lower()
        info["type"] = (info.get("type") or "").lower()
        info["role"] = (info.get("role") or "").lower()
        return info
    except Exception as exc:
        log.debug("Failed to describe element for typing: %s", exc)
        return {}


def _element_is_text_editable(info: Dict[str, Any]) -> bool:
    if not info:
        return False
    if info.get("contentEditable"):
        return True
    role = info.get("role") or ""
    if role in {"textbox", "searchbox", "combobox"}:
        return True
    if info.get("disabled") or info.get("readOnly"):
        return False
    tag = info.get("tag") or ""
    if tag == "textarea":
        return True
    if tag == "input":
        input_type = info.get("type") or ""
        allowed_types = {
            "",
            "text",
            "search",
            "email",
            "password",
            "number",
            "tel",
            "url",
            "date",
            "datetime-local",
        }
        return input_type in allowed_types
    return False


async def _find_text_input_fallback(page: Page, locator: Locator) -> Tuple[Optional[Locator], Optional[str]]:
    try:
        target = locator.nth(0)
    except Exception:
        return None, None

    try:
        descendants = target.locator(_TEXT_INPUT_SELECTOR)
        if await descendants.count():
            candidate = descendants.first
            info = await _describe_element_for_typing(candidate)
            if _element_is_text_editable(info):
                return candidate, "descendant"
    except Exception as exc:
        log.debug("Text input descendant search failed: %s", exc)

    try:
        label_for = await target.get_attribute("for")
    except Exception:
        label_for = None

    if label_for:
        candidate = page.locator(f"#{label_for}")
        try:
            if await candidate.count():
                info = await _describe_element_for_typing(candidate)
                if _element_is_text_editable(info):
                    return candidate, "label_for"
        except Exception as exc:
            log.debug("label_for fallback failed for %s: %s", label_for, exc)

    for attr in ("aria-controls", "aria-labelledby", "aria-describedby"):
        try:
            attr_value = await target.get_attribute(attr)
        except Exception:
            attr_value = None
        if not attr_value:
            continue
        for candidate_id in attr_value.split():
            candidate_id = candidate_id.strip()
            if not candidate_id:
                continue
            candidate = page.locator(f"#{candidate_id}")
            try:
                if await candidate.count():
                    info = await _describe_element_for_typing(candidate)
                    if _element_is_text_editable(info):
                        return candidate, attr
            except Exception as exc:
                log.debug("%s fallback failed for %s: %s", attr, candidate_id, exc)

    sibling_queries = [
        "xpath=following::input[not(@type='hidden') and not(@disabled) and not(@readonly)][1]",
        "xpath=following::textarea[not(@disabled) and not(@readonly)][1]",
    ]
    for query in sibling_queries:
        try:
            sibling = target.locator(query)
            if await sibling.count():
                info = await _describe_element_for_typing(sibling)
                if _element_is_text_editable(info):
                    return sibling, "sibling"
        except Exception as exc:
            log.debug("Sibling search failed for query %s: %s", query, exc)

    current = target
    for depth in range(3):
        try:
            current = current.locator("xpath=..").nth(0)
        except Exception:
            break
        try:
            candidate = current.locator(_TEXT_INPUT_SELECTOR)
            if await candidate.count():
                info = await _describe_element_for_typing(candidate)
                if _element_is_text_editable(info):
                    return candidate, f"ancestor_depth_{depth + 1}"
        except Exception as exc:
            log.debug("Ancestor search failed at depth %d: %s", depth + 1, exc)

    return None, None


def _summarize_element(info: Dict[str, Any]) -> str:
    parts: list[str] = []
    tag = info.get("tag")
    if tag:
        parts.append(tag)
    typ = info.get("type")
    if typ:
        parts.append(f"type={typ}")
    if info.get("contentEditable"):
        parts.append("contenteditable=true")
    if info.get("disabled"):
        parts.append("disabled=true")
    if info.get("readOnly"):
        parts.append("readOnly=true")
    identifier = info.get("name") or info.get("id") or info.get("placeholder")
    if identifier:
        trimmed = identifier.strip()
        if len(trimmed) > 40:
            trimmed = trimmed[:37] + "..."
        parts.append(f"identifier={trimmed}")
    return ", ".join(parts) if parts else "unknown element"


def _get_key_code(key: str) -> int:
    key_codes = {
        'Enter': 13, 'Return': 13, 'Tab': 9, 'Escape': 27,
        'Space': 32, 'Backspace': 8, 'Delete': 46,
        'ArrowUp': 38, 'ArrowDown': 40, 'ArrowLeft': 37, 'ArrowRight': 39,
        'F1': 112, 'F2': 113, 'F3': 114, 'F4': 115, 'F5': 116,
        'F6': 117, 'F7': 118, 'F8': 119, 'F9': 120, 'F10': 121, 'F11': 122, 'F12': 123
    }
    if len(key) == 1:
        return ord(key.upper())
    return key_codes.get(key, 0)

