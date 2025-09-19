"""High level automation service orchestrating DSL execution.

The original project this kata is inspired by exposes a rather involved
automation controller.  To make the codebase maintainable and testable we
encapsulate the orchestration logic inside a small service object that mirrors
that architecture.  The service parses DSL payloads, delegates individual
actions to the browser adapter and keeps track of auxiliary state such as the
latest HTML, extracted snippets, evaluation results and structured events.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from automation.dsl.models import (
    ActionBase,
    AssertAction,
    ClickAction,
    ClickBlankAreaAction,
    ClosePopupAction,
    EvalJsAction,
    ExtractAction,
    FocusIframeAction,
    HoverAction,
    NavigateAction,
    PressKeyAction,
    RefreshCatalogAction,
    ScrollAction,
    ScrollTarget,
    ScrollToTextAction,
    ScreenshotAction,
    SelectAction,
    Selector,
    StopAction,
    SwitchTabAction,
    TypeAction,
    WaitAction,
    WaitCondition,
    WaitForSelector,
    WaitForState,
    WaitForTimeout,
)
from automation.dsl.registry import RunPlan, RunRequest, registry
from vnc.browser_use_adapter import BrowserUseAdapter


@dataclass(slots=True)
class ActionExecution:
    """Result information for a single action."""

    name: str
    ok: bool
    details: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        payload = {
            "name": self.name,
            "ok": self.ok,
            "details": self.details,
        }
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(slots=True)
class RunSummary:
    """Structured payload returned by :class:`AutomationService`."""

    run_id: str
    correlation_id: str
    success: bool
    html: str
    url: str
    results: List[ActionExecution]
    warnings: List[str]
    observation: Dict[str, Any]
    error: Optional[Dict[str, Any]] = None
    is_done: bool = True
    complete: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "correlation_id": self.correlation_id,
            "success": self.success,
            "html": self.html,
            "url": self.url,
            "results": [entry.as_dict() for entry in self.results],
            "warnings": list(self.warnings),
            "observation": dict(self.observation),
            "error": self.error,
            "is_done": self.is_done,
            "complete": self.complete,
        }


class AutomationService:
    """Wrapper responsible for executing DSL payloads using a browser adapter."""

    def __init__(self, adapter: Optional[BrowserUseAdapter] = None, *, headless: bool = True) -> None:
        self.adapter = adapter or BrowserUseAdapter()
        self._headless = headless
        self._initialized = False
        self._extracted_items: List[str] = []
        self._eval_results: List[Any] = []
        self._stop_request: Optional[Dict[str, Any]] = None
        self._last_stop_response: Optional[str] = None
        self._events: Dict[str, List[Dict[str, Any]]] = {}
        self._last_html: str = ""
        self._last_url: str = "about:blank"
        self._last_screenshot: Optional[bytes] = None
        self._catalog_version_counter = 0
        self._catalog_version: Optional[str] = None
        self._catalog_entries: List[Dict[str, Any]] = []
        self._observed_selectors: Set[str] = set()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def execute_plan_async(self, payload: Dict[str, Any]) -> RunSummary:
        """Validate the supplied payload and execute the described plan."""

        request = self._parse_payload(payload)
        await self._ensure_initialized()

        correlation_id = payload.get("correlation_id") or uuid.uuid4().hex[:8]
        selectors_used: Set[str] = set()
        run_events: List[Dict[str, Any]] = []
        run_results: List[ActionExecution] = []
        run_warnings: List[str] = []
        extracted: List[str] = []
        eval_results: List[Any] = []
        stop_triggered = False

        for index, action in enumerate(request.plan.actions):
            execution = await self._perform_action(action, selectors_used, extracted, eval_results)
            run_results.append(execution)
            run_warnings.extend(execution.warnings)
            run_events.append(
                {
                    "index": index,
                    "timestamp": time.time(),
                    "action": action.payload(),
                    "result": execution.details,
                    "ok": execution.ok,
                    "warnings": execution.warnings,
                    "error": execution.error,
                }
            )
            if isinstance(action, StopAction) and execution.ok:
                stop_triggered = True
                break

        html = await self.adapter.get_page_content()
        url = await self.adapter.get_url()

        self._last_html = html
        self._last_url = url
        self._observed_selectors.update(selectors_used)
        self._events[request.run_id] = run_events
        self._extracted_items.extend(extracted)
        self._eval_results.extend(eval_results)
        if self._catalog_version is None or selectors_used:
            self._update_catalog(selectors_used)

        success = all(result.ok for result in run_results) if run_results else True
        error_payload = self._build_error_payload(run_results)

        observation = {
            "url": url,
            "catalog_version": self._catalog_version,
            "selectors": sorted(selectors_used),
        }

        summary = RunSummary(
            run_id=request.run_id,
            correlation_id=correlation_id,
            success=success,
            html=html,
            url=url,
            results=run_results,
            warnings=run_warnings,
            observation=observation,
            error=error_payload,
            is_done=not stop_triggered,
            complete=not stop_triggered,
        )
        return summary

    def execute_plan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronous convenience wrapper around :meth:`execute_plan_async`."""

        return asyncio.run(self.execute_plan_async(payload)).as_dict()

    async def get_html_async(self) -> str:
        await self._ensure_initialized()
        return await self.adapter.get_page_content()

    def get_html(self) -> str:
        return asyncio.run(self.get_html_async())

    async def get_url_async(self) -> str:
        await self._ensure_initialized()
        return await self.adapter.get_url()

    def get_url(self) -> str:
        return asyncio.run(self.get_url_async())

    async def get_screenshot_async(self, *, full_page: bool = False) -> bytes:
        await self._ensure_initialized()
        screenshot = await self.adapter.screenshot(full_page=full_page)
        if screenshot:
            self._last_screenshot = screenshot
        return screenshot

    def get_screenshot(self, *, full_page: bool = False) -> bytes:
        return asyncio.run(self.get_screenshot_async(full_page=full_page))

    def get_elements(self) -> List[Dict[str, Any]]:
        entries = []
        for index, selector in enumerate(sorted(self._observed_selectors)):
            entries.append({"index": index, "selector": selector})
        return entries

    def get_catalog(self, *, refresh: bool = False) -> Dict[str, Any]:
        if refresh:
            self._update_catalog(self._observed_selectors)
        return {
            "abbreviated": self._catalog_entries[: min(len(self._catalog_entries), 25)],
            "full": list(self._catalog_entries),
            "catalog_version": self._catalog_version,
            "index_mode_enabled": bool(self._catalog_entries),
            "metadata": {"url": self._last_url or "about:blank"},
        }

    def get_extracted(self) -> List[str]:
        return list(self._extracted_items)

    def get_eval_results(self) -> List[Any]:
        return list(self._eval_results)

    def get_events(self, run_id: str) -> Optional[str]:
        events = self._events.get(run_id)
        if not events:
            return None
        return "\n".join(json.dumps(entry, ensure_ascii=False) for entry in events)

    def get_stop_request(self) -> Optional[Dict[str, Any]]:
        return self._stop_request

    def clear_stop_request(self) -> None:
        self._stop_request = None

    def record_stop_response(self, response: str) -> None:
        self._last_stop_response = response
        self._stop_request = None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _parse_payload(self, payload: Dict[str, Any]) -> RunRequest:
        if "plan" in payload:
            request = RunRequest.model_validate(payload)
            return request

        actions: Sequence[Any] = payload.get("actions", [])
        parsed_actions: List[ActionBase] = []
        for entry in actions:
            if isinstance(entry, ActionBase):
                parsed_actions.append(entry)
            else:
                parsed_actions.append(registry.parse_action(entry))
        run_id = payload.get("run_id") or f"run-{uuid.uuid4().hex[:8]}"
        plan = RunPlan(actions=parsed_actions)
        return RunRequest(run_id=run_id, plan=plan, config=payload.get("config", {}), metadata=payload.get("metadata", {}))

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        initializer = getattr(self.adapter, "initialize", None)
        if callable(initializer):
            result = initializer(headless=self._headless)
            if asyncio.iscoroutine(result):
                result = await result
            if result is False:
                raise RuntimeError("Failed to initialise browser adapter")
        self._initialized = True

    async def _perform_action(
        self,
        action: ActionBase,
        selectors: Set[str],
        extracted: List[str],
        eval_results: List[Any],
    ) -> ActionExecution:
        if isinstance(action, NavigateAction):
            return await self._perform_navigate(action)
        if isinstance(action, ClickAction):
            return await self._perform_click(action, selectors)
        if isinstance(action, HoverAction):
            return await self._perform_hover(action, selectors)
        if isinstance(action, TypeAction):
            return await self._perform_type(action, selectors)
        if isinstance(action, SelectAction):
            return await self._perform_select(action, selectors)
        if isinstance(action, PressKeyAction):
            return await self._perform_press(action)
        if isinstance(action, WaitAction):
            return await self._perform_wait(action, selectors)
        if isinstance(action, ScrollAction):
            return await self._perform_scroll(action, selectors)
        if isinstance(action, ScrollToTextAction):
            return await self._perform_scroll_to_text(action)
        if isinstance(action, SwitchTabAction):
            return self._unsupported_action(action, "Tab switching is not supported in the lightweight adapter")
        if isinstance(action, FocusIframeAction):
            return self._unsupported_action(action, "Iframe focus is not supported in the lightweight adapter")
        if isinstance(action, RefreshCatalogAction):
            return self._perform_refresh_catalog(action)
        if isinstance(action, EvalJsAction):
            return await self._perform_eval(action, eval_results)
        if isinstance(action, ClickBlankAreaAction):
            return await self._perform_click_blank(action)
        if isinstance(action, ClosePopupAction):
            return self._unsupported_action(action, "Popup closing requires custom logic")
        if isinstance(action, StopAction):
            return self._perform_stop(action)
        if isinstance(action, ScreenshotAction):
            return await self._perform_screenshot(action)
        if isinstance(action, ExtractAction):
            return await self._perform_extract(action, selectors, extracted)
        if isinstance(action, AssertAction):
            return await self._perform_assert(action, selectors)
        return self._unsupported_action(action, "Unsupported action type")

    async def _perform_navigate(self, action: NavigateAction) -> ActionExecution:
        wait_until = "load"
        wait_details: Dict[str, Any] = {}
        if isinstance(action.wait_for, WaitForState):
            wait_until = action.wait_for.state
        result = await self.adapter.navigate(action.url, wait_until=wait_until)
        ok = result.success
        if isinstance(action.wait_for, WaitForTimeout):
            await asyncio.sleep(action.wait_for.timeout_ms / 1000)
            wait_details = {"type": "timeout", "timeout_ms": action.wait_for.timeout_ms}
        elif isinstance(action.wait_for, WaitForSelector):
            selector = self._selector_to_locator(action.wait_for.selector)
            if selector is None:
                return ActionExecution(
                    name=action.action_name,
                    ok=False,
                    details=result.to_dict(),
                    warnings=["WARNING:auto:Wait condition missing selector"],
                    error="Missing selector for wait condition",
                )
            wait_result = await self.adapter.wait_for_selector(selector, state=action.wait_for.state)
            ok = ok and wait_result.success
            wait_details = wait_result.to_dict()
        details = result.to_dict()
        if wait_details:
            details["wait"] = wait_details
        return ActionExecution(name=action.action_name, ok=ok, details=details, error=None if ok else details.get("error"))

    async def _perform_click(self, action: ClickAction, selectors: Set[str]) -> ActionExecution:
        selector = self._selector_to_locator(action.selector)
        if selector is None:
            return ActionExecution(
                name=action.action_name,
                ok=False,
                details={},
                warnings=["WARNING:auto:Click action missing selector"],
                error="Missing selector",
            )
        selectors.add(selector)
        result = await self.adapter.click(
            selector,
            button=action.button,
            click_count=action.click_count,
            delay_ms=action.delay_ms,
        )
        warnings = []
        error_message = None
        if not result.success:
            error_message = result.to_dict().get("error") or "click failed"
            warnings.append(f"WARNING:auto:{error_message}")
        return ActionExecution(
            name=action.action_name,
            ok=result.success,
            details=result.to_dict(),
            warnings=warnings,
            error=error_message,
        )

    async def _perform_hover(self, action: HoverAction, selectors: Set[str]) -> ActionExecution:
        selector = self._selector_to_locator(action.selector)
        if selector is None:
            return ActionExecution(
                name=action.action_name,
                ok=False,
                details={},
                warnings=["WARNING:auto:Hover action missing selector"],
                error="Missing selector",
            )
        selectors.add(selector)
        result = await self.adapter.hover(selector)
        return ActionExecution(
            name=action.action_name,
            ok=result.success,
            details=result.to_dict(),
            error=None if result.success else result.to_dict().get("error"),
        )

    async def _perform_type(self, action: TypeAction, selectors: Set[str]) -> ActionExecution:
        selector = self._selector_to_locator(action.selector)
        if selector is None:
            return ActionExecution(
                name=action.action_name,
                ok=False,
                details={},
                warnings=["WARNING:auto:Type action missing selector"],
                error="Missing selector",
            )
        selectors.add(selector)
        result = await self.adapter.fill(selector, action.text, clear=action.clear)
        ok = result.success
        details = result.to_dict()
        if action.press_enter and ok:
            press_result = await self.adapter.press_key("Enter")
            ok = ok and press_result.success
            details["press_enter"] = press_result.to_dict()
        return ActionExecution(
            name=action.action_name,
            ok=ok,
            details=details,
            error=None if ok else details.get("error"),
        )

    async def _perform_select(self, action: SelectAction, selectors: Set[str]) -> ActionExecution:
        selector = self._selector_to_locator(action.selector)
        if selector is None:
            return ActionExecution(
                name=action.action_name,
                ok=False,
                details={},
                warnings=["WARNING:auto:Select action missing selector"],
                error="Missing selector",
            )
        selectors.add(selector)
        result = await self.adapter.select_option(selector, action.value_or_label)
        return ActionExecution(
            name=action.action_name,
            ok=result.success,
            details=result.to_dict(),
            error=None if result.success else result.to_dict().get("error"),
        )

    async def _perform_press(self, action: PressKeyAction) -> ActionExecution:
        ok = True
        details: Dict[str, Any] = {"keys": []}
        for key in action.keys:
            press_result = await self.adapter.press_key(key)
            ok = ok and press_result.success
            details["keys"].append(press_result.to_dict())
        return ActionExecution(
            name=action.action_name,
            ok=ok,
            details=details,
            error=None if ok else "One or more key presses failed",
        )

    async def _perform_wait(self, action: WaitAction, selectors: Set[str]) -> ActionExecution:
        details: Dict[str, Any] = {"timeout_ms": action.timeout_ms}
        ok = True
        if action.for_ is None:
            await asyncio.sleep(action.timeout_ms / 1000)
        elif isinstance(action.for_, WaitForTimeout):
            await asyncio.sleep(action.for_.timeout_ms / 1000)
            details["condition"] = {"type": "timeout", "timeout_ms": action.for_.timeout_ms}
        elif isinstance(action.for_, WaitForSelector):
            selector = self._selector_to_locator(action.for_.selector)
            if selector is None:
                return ActionExecution(
                    name=action.action_name,
                    ok=False,
                    details=details,
                    warnings=["WARNING:auto:Wait for selector missing selector"],
                    error="Missing selector",
                )
            selectors.add(selector)
            wait_result = await self.adapter.wait_for_selector(selector, state=action.for_.state, timeout=action.timeout_ms)
            ok = wait_result.success
            details["condition"] = wait_result.to_dict()
        elif isinstance(action.for_, WaitForState):
            details["condition"] = {"type": "state", "state": action.for_.state}
            await asyncio.sleep(0)
        return ActionExecution(
            name=action.action_name,
            ok=ok,
            details=details,
            error=None if ok else details.get("condition", {}).get("error"),
        )

    async def _perform_scroll(self, action: ScrollAction, selectors: Set[str]) -> ActionExecution:
        details: Dict[str, Any] = {}
        ok = True
        if isinstance(action.to, int):
            result = await self.adapter.scroll_by(0, action.to)
            details = result.to_dict()
            ok = result.success
        elif isinstance(action.to, str):
            script = "window.scrollTo(0, 0);" if action.to == "top" else "window.scrollTo(0, document.body.scrollHeight);"
            await self.adapter.evaluate(script)
            details = {"script": script}
        elif isinstance(action.to, ScrollTarget):
            selector = self._selector_to_locator(action.to.selector) if action.to.selector else None
            if selector:
                selectors.add(selector)
                script = f"(() => {{ const el = document.querySelector({json.dumps(selector)}); if (el) {{ el.scrollIntoView({{behavior: 'smooth', block: 'center'}}); return true; }} return false; }})()"
                result = await self.adapter.evaluate(script)
                ok = bool(result)
                details = {"scrolled": ok, "selector": selector}
            else:
                details = {"warning": "No selector specified for scroll target"}
                ok = False
        else:
            direction = action.direction or "down"
            delta = 400 if direction == "down" else -400
            result = await self.adapter.scroll_by(0, delta)
            ok = result.success
            details = result.to_dict()
        return ActionExecution(
            name=action.action_name,
            ok=ok,
            details=details,
            error=None if ok else "Scroll action failed",
            warnings=[] if ok else ["WARNING:auto:Scroll action did not complete successfully"],
        )

    async def _perform_scroll_to_text(self, action: ScrollToTextAction) -> ActionExecution:
        script = (
            "(() => {\n"
            "  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);\n"
            f"  const target = {json.dumps(action.text)}.toLowerCase();\n"
            "  while (walker.nextNode()) {\n"
            "    const value = walker.currentNode.textContent || '';\n"
            "    if (value.toLowerCase().includes(target)) {\n"
            "      walker.currentNode.parentElement?.scrollIntoView({behavior: 'smooth', block: 'center'});\n"
            "      return true;\n"
            "    }\n"
            "  }\n"
            "  return false;\n"
            "})()"
        )
        result = await self.adapter.evaluate(script)
        ok = bool(result)
        warnings = [] if ok else [f"WARNING:auto:Text '{action.text}' not found during scroll"]
        return ActionExecution(
            name=action.action_name,
            ok=ok,
            details={"script": "scroll_to_text", "text": action.text, "found": ok},
            warnings=warnings,
            error=None if ok else "Scroll to text failed",
        )

    def _perform_refresh_catalog(self, action: RefreshCatalogAction) -> ActionExecution:
        self._update_catalog(self._observed_selectors, force_new_version=True)
        return ActionExecution(
            name=action.action_name,
            ok=True,
            details={"catalog_version": self._catalog_version},
        )

    async def _perform_eval(self, action: EvalJsAction, eval_results: List[Any]) -> ActionExecution:
        result = await self.adapter.evaluate(action.script)
        eval_results.append(result)
        return ActionExecution(
            name=action.action_name,
            ok=True,
            details={"result": result},
        )

    async def _perform_click_blank(self, action: ClickBlankAreaAction) -> ActionExecution:
        result = await self.adapter.click("body")
        return ActionExecution(
            name=action.action_name,
            ok=result.success,
            details=result.to_dict(),
            error=None if result.success else result.to_dict().get("error"),
        )

    def _perform_stop(self, action: StopAction) -> ActionExecution:
        self._stop_request = {"reason": action.reason, "message": action.message}
        return ActionExecution(
            name=action.action_name,
            ok=True,
            details={"reason": action.reason, "message": action.message},
        )

    async def _perform_screenshot(self, action: ScreenshotAction) -> ActionExecution:
        full_page = action.mode != "viewport"
        screenshot = await self.adapter.screenshot(full_page=full_page)
        self._last_screenshot = screenshot
        encoded = base64.b64encode(screenshot).decode("ascii")
        details = {"mode": action.mode, "bytes": encoded}
        if action.file_name:
            details["file_name"] = action.file_name
        return ActionExecution(name=action.action_name, ok=True, details=details)

    async def _perform_extract(
        self,
        action: ExtractAction,
        selectors: Set[str],
        extracted: List[str],
    ) -> ActionExecution:
        selector = self._selector_to_locator(action.selector)
        if selector is None:
            return ActionExecution(
                name=action.action_name,
                ok=False,
                details={},
                warnings=["WARNING:auto:Extract action missing selector"],
                error="Missing selector",
            )
        selectors.add(selector)
        result = await self.adapter.extract(selector, attr=action.attr)
        if result.success:
            value = result.details.get("value")
            if value is not None:
                extracted.append(str(value))
        return ActionExecution(
            name=action.action_name,
            ok=result.success,
            details=result.to_dict(),
            error=None if result.success else result.to_dict().get("error"),
        )

    async def _perform_assert(self, action: AssertAction, selectors: Set[str]) -> ActionExecution:
        selector = self._selector_to_locator(action.selector)
        if selector is None:
            return ActionExecution(
                name=action.action_name,
                ok=False,
                details={},
                warnings=["WARNING:auto:Assert action missing selector"],
                error="Missing selector",
            )
        selectors.add(selector)
        state = "visible" if action.state == "visible" else "hidden" if action.state == "hidden" else action.state
        result = await self.adapter.wait_for_selector(selector, state=state)
        ok = result.success if action.state in {"visible", "attached"} else not result.success
        warnings = [] if ok else [f"WARNING:auto:Assertion failed for selector {selector}"]
        return ActionExecution(
            name=action.action_name,
            ok=ok,
            details=result.to_dict(),
            warnings=warnings,
            error=None if ok else result.to_dict().get("error", "Assertion failed"),
        )

    def _unsupported_action(self, action: ActionBase, reason: str) -> ActionExecution:
        return ActionExecution(
            name=action.action_name,
            ok=False,
            details={"reason": reason},
            warnings=[f"WARNING:auto:{reason}"],
            error=reason,
        )

    def _selector_to_locator(self, selector: Optional[Selector | str]) -> Optional[str]:
        if selector is None:
            return None
        if isinstance(selector, str):
            return selector.strip() or None
        if selector.css:
            return selector.css
        if selector.xpath:
            return f"xpath={selector.xpath}"
        if selector.stable_id:
            return f"[data-testid='{selector.stable_id}']"
        if selector.text:
            return f"text={selector.text}"
        if selector.role and selector.text:
            return f"role={selector.role} >> text={selector.text}"
        if selector.role:
            return f"role={selector.role}"
        if selector.aria_label:
            return f"aria/{selector.aria_label}"
        if selector.legacy_value:
            value = selector.legacy_value
            return value.strip() if isinstance(value, str) else None
        return None

    def _update_catalog(self, selectors: Iterable[str], *, force_new_version: bool = False) -> None:
        if force_new_version or selectors:
            self._catalog_version_counter += 1
            self._catalog_version = f"catalog-{self._catalog_version_counter:04d}"
        combined = set(self._observed_selectors)
        combined.update(selectors)
        self._catalog_entries = [
            {"index": index, "selector": selector}
            for index, selector in enumerate(sorted(combined))
        ]

    def _build_error_payload(self, results: Sequence[ActionExecution]) -> Optional[Dict[str, Any]]:
        for entry in results:
            if not entry.ok:
                message = entry.error or f"Action {entry.name} failed"
                return {
                    "code": "ACTION_FAILED",
                    "message": message,
                    "details": entry.details,
                }
        return None
