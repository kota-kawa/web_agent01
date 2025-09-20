"""Deterministic execution pipeline for typed DSL actions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import Error as PlaywrightError, Frame, Page

from automation.dsl import (
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
    RunRequest,
    ScrollAction,
    ScrollToTextAction,
    ScreenshotAction,
    SelectAction,
    StopAction,
    SwitchTabAction,
    TypeAction,
    WaitAction,
    WaitCondition,
    WaitForSelector,
    WaitForState,
    WaitForTimeout,
    registry,
    AssertAction,
)
from automation.dsl.models import ActionBase
from automation.dsl.resolution import ResolvedNode

from .config import RunConfig, ensure_run_directories, load_config
from .page_actions import (
    click_blank_area as perform_click_blank_area,
    close_popup as perform_close_popup,
    eval_js as run_eval_js,
    scroll_to_text as perform_scroll_to_text,
)
from .page_stability import stabilize_page, wait_for_page_ready
from .safe_interactions import prepare_locator, safe_click, safe_fill, safe_hover, safe_press, safe_select
from .selector_resolver import SelectorResolver, StableNodeStore
from .structured_logging import LogPaths, StructuredLogger, prepare_log_paths


@dataclass(slots=True)
class ActionOutcome:
    ok: bool
    details: Dict[str, Any]
    resolved: Optional[ResolvedNode] = None
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    error_code: Optional[str] = None
    error_details: Dict[str, Any] = field(default_factory=dict)
    attempts: int = 1

    def as_dict(self) -> Dict[str, Any]:
        payload = {"ok": self.ok, "details": self.details}
        if self.warnings:
            payload["warnings"] = self.warnings
        if self.error:
            payload["error"] = self.error
        if self.error_code:
            payload["error_code"] = self.error_code
        if self.error_details:
            payload["error_details"] = self.error_details
        if not self.ok or self.attempts > 1:
            payload["attempts"] = self.attempts
        return payload


class ExecutionError(Exception):
    def __init__(self, message: str, *, code: str = "EXECUTION_ERROR", details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class ActionContext:
    def __init__(self, page: Page, config: RunConfig, logger: StructuredLogger, paths: LogPaths, store: StableNodeStore):
        self.page = page
        self.config = config
        self.logger = logger
        self.paths = paths
        self.store = store
        self.frame_stack: List[Frame] = [page.main_frame]
        self._last_result: Dict[str, Any] = {}
        self.stop_requested: bool = False

    @property
    def current_frame(self) -> Frame:
        return self.frame_stack[-1]

    def push_frame(self, frame: Frame) -> None:
        self.frame_stack.append(frame)

    def pop_frame(self) -> None:
        if len(self.frame_stack) > 1:
            self.frame_stack.pop()

    def dom_digest(self, node: ResolvedNode) -> str:
        raw = f"{node.dom_path}|{node.text_digest}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ActionPerformer:
    def __init__(self, context: ActionContext) -> None:
        self.context = context

    async def dry_run(self, action: ActionBase) -> None:
        selector_actions = (
            ClickAction,
            HoverAction,
            TypeAction,
            SelectAction,
            ScreenshotAction,
            ExtractAction,
            AssertAction,
        )
        if isinstance(action, selector_actions):
            await self._resolve(action)
        if isinstance(action, ScrollAction) and getattr(action, "to", None):
            target = action.to
            if hasattr(target, "selector") and target.selector is not None:
                await self._resolve_selector(target.selector)
        if isinstance(action, WaitAction) and isinstance(action.for_, WaitForSelector):
            await self._resolve_selector(action.for_.selector)

    async def execute(self, action: ActionBase) -> ActionOutcome:
        timeout = self.context.config.action_timeout_ms
        await stabilize_page(self.context.page, timeout=timeout)
        outcome = await self._dispatch(action)
        await stabilize_page(self.context.page, timeout=timeout)
        return outcome

    async def _dispatch(self, action: ActionBase) -> ActionOutcome:
        if isinstance(action, NavigateAction):
            return await self._navigate(action)
        if isinstance(action, ClickAction):
            return await self._click(action)
        if isinstance(action, HoverAction):
            return await self._hover(action)
        if isinstance(action, TypeAction):
            return await self._type(action)
        if isinstance(action, SelectAction):
            return await self._select(action)
        if isinstance(action, PressKeyAction):
            return await self._press_key(action)
        if isinstance(action, WaitAction):
            return await self._wait(action)
        if isinstance(action, ScrollAction):
            return await self._scroll(action)
        if isinstance(action, ScrollToTextAction):
            return await self._scroll_to_text(action)
        if isinstance(action, RefreshCatalogAction):
            return await self._refresh_catalog(action)
        if isinstance(action, ClickBlankAreaAction):
            return await self._click_blank_area(action)
        if isinstance(action, ClosePopupAction):
            return await self._close_popup(action)
        if isinstance(action, EvalJsAction):
            return await self._eval_js(action)
        if isinstance(action, StopAction):
            return await self._stop(action)
        if isinstance(action, SwitchTabAction):
            return await self._switch_tab(action)
        if isinstance(action, FocusIframeAction):
            return await self._focus_iframe(action)
        if isinstance(action, ScreenshotAction):
            return await self._screenshot(action)
        if isinstance(action, ExtractAction):
            return await self._extract(action)
        if isinstance(action, AssertAction):
            return await self._assert(action)
        raise ExecutionError(f"Unsupported action {action.action_name}")

    async def _resolve(self, action: ActionBase) -> ResolvedNode:
        selector = getattr(action, "selector", None)
        if selector is None:
            raise ExecutionError("Action does not define a selector", code="VALIDATION")
        return await self._resolve_selector(selector)

    async def _resolve_selector(self, selector) -> ResolvedNode:
        resolver = SelectorResolver(self.context.current_frame, self.context.store)
        return await resolver.resolve(selector)

    async def _navigate(self, action: NavigateAction) -> ActionOutcome:
        page = self.context.page
        await page.goto(action.url, wait_until="domcontentloaded", timeout=self.context.config.navigation_timeout_ms)
        warnings = await wait_for_page_ready(page, timeout=self.context.config.wait_timeout_ms)
        details: Dict[str, Any] = {"url": action.url}
        if action.wait_for:
            wait_details, resolved = await self._handle_wait_condition(action.wait_for, self.context.config.wait_timeout_ms)
            details.update(wait_details)
            return ActionOutcome(ok=True, details=details, warnings=warnings, resolved=resolved)
        return ActionOutcome(ok=True, details=details, warnings=warnings)

    async def _click(self, action: ClickAction) -> ActionOutcome:
        resolved = await self._resolve(action)
        if resolved.locator is None:
            raise ExecutionError("Resolved locator is unavailable", code="LOCATOR")
        await safe_click(
            self.context.page,
            resolved.locator,
            timeout=self.context.config.action_timeout_ms,
            button=action.button,
            click_count=action.click_count,
            delay_ms=action.delay_ms,
        )
        details = {
            "stable_id": resolved.stable_id,
            "button": action.button,
            "click_count": action.click_count,
        }
        if action.delay_ms is not None:
            details["delay_ms"] = action.delay_ms
        return ActionOutcome(ok=True, details=details, resolved=resolved)

    async def _hover(self, action: HoverAction) -> ActionOutcome:
        resolved = await self._resolve(action)
        if resolved.locator is None:
            raise ExecutionError("Resolved locator is unavailable", code="LOCATOR")
        await safe_hover(self.context.page, resolved.locator, timeout=self.context.config.action_timeout_ms)
        return ActionOutcome(ok=True, details={"stable_id": resolved.stable_id}, resolved=resolved)

    async def _type(self, action: TypeAction) -> ActionOutcome:
        resolved = await self._resolve(action)
        if resolved.locator is None:
            raise ExecutionError("Resolved locator is unavailable", code="LOCATOR")
        await safe_fill(
            self.context.page,
            resolved.locator,
            action.text,
            timeout=self.context.config.action_timeout_ms,
            original_target=str(action.selector.as_legacy()),
        )
        if action.press_enter:
            await safe_press(self.context.page, resolved.locator, "Enter", timeout=self.context.config.action_timeout_ms)
        details = {"text": action.text, "stable_id": resolved.stable_id}
        if action.press_enter:
            details["press_enter"] = True
        if action.clear:
            details["cleared"] = True
        return ActionOutcome(ok=True, details=details, resolved=resolved)

    async def _select(self, action: SelectAction) -> ActionOutcome:
        resolved = await self._resolve(action)
        if resolved.locator is None:
            raise ExecutionError("Resolved locator is unavailable", code="LOCATOR")
        await safe_select(
            self.context.page,
            resolved.locator,
            action.value_or_label,
            timeout=self.context.config.action_timeout_ms,
        )
        return ActionOutcome(ok=True, details={"value": action.value_or_label, "stable_id": resolved.stable_id}, resolved=resolved)

    async def _press_key(self, action: PressKeyAction) -> ActionOutcome:
        key_combo = "+".join(action.keys)
        details = {"keys": action.keys, "scope": action.scope}
        try:
            if action.scope == "active_element":
                try:
                    await self.context.current_frame.press(":focus", key_combo)
                    details["method"] = "focus"
                except Exception:
                    await self.context.page.keyboard.press(key_combo)
                    details["method"] = "page_fallback"
            else:
                await self.context.page.keyboard.press(key_combo)
                details["method"] = "page"
        except Exception as exc:
            raise ExecutionError(str(exc), code="PRESS_KEY_FAILED") from exc
        return ActionOutcome(ok=True, details=details)

    async def _wait(self, action: WaitAction) -> ActionOutcome:
        if action.for_ is None:
            await self.context.current_frame.wait_for_timeout(action.timeout_ms)
            return ActionOutcome(ok=True, details={"waited_ms": action.timeout_ms})
        details, resolved = await self._handle_wait_condition(action.for_, action.timeout_ms)
        return ActionOutcome(ok=True, details=details, resolved=resolved)

    async def _handle_wait_condition(
        self,
        condition: WaitCondition,
        timeout_ms: int,
    ) -> tuple[Dict[str, Any], Optional[ResolvedNode]]:
        frame = self.context.current_frame
        if isinstance(condition, WaitForTimeout):
            await frame.wait_for_timeout(condition.timeout_ms)
            return {"waited_ms": condition.timeout_ms}, None
        if isinstance(condition, WaitForState):
            await self.context.page.wait_for_load_state(condition.state, timeout=timeout_ms)
            return {"state": condition.state}, None
        if isinstance(condition, WaitForSelector):
            resolved = await self._resolve_selector(condition.selector)
            if resolved.locator is None:
                raise ExecutionError("Resolved locator is unavailable", code="LOCATOR")
            await resolved.locator.wait_for(state=condition.state, timeout=timeout_ms)
            return {"selector": resolved.dom_path, "state": condition.state}, resolved
        raise ExecutionError("Unsupported wait condition", code="VALIDATION")

    async def _scroll(self, action: ScrollAction) -> ActionOutcome:
        frame = self.context.current_frame
        container_resolved: Optional[ResolvedNode] = None
        container_locator = None
        if action.container is not None:
            container_resolved = await self._resolve_selector(action.container)
            container_locator = container_resolved.locator

        details: Dict[str, Any] = {}
        resolved: Optional[ResolvedNode] = None

        async def scroll_by(amount: int) -> None:
            if container_locator is not None:
                await container_locator.evaluate("(el, offset) => el.scrollBy(0, offset)", amount)
            else:
                await frame.evaluate("(offset) => window.scrollBy(0, offset)", amount)

        target = action.to
        if isinstance(target, int):
            await scroll_by(target)
            details["offset"] = target
        elif isinstance(target, str):
            details["position"] = target
            if container_locator is not None:
                if target == "top":
                    await container_locator.evaluate("el => el.scrollTo({top: 0, behavior: 'smooth'})")
                elif target == "bottom":
                    await container_locator.evaluate("el => el.scrollTo({top: el.scrollHeight, behavior: 'smooth'})")
            else:
                if target == "top":
                    await frame.evaluate("() => window.scrollTo({top: 0, behavior: 'smooth'})")
                elif target == "bottom":
                    await frame.evaluate("() => window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
        elif target is not None:
            target_container = getattr(target, "container", None)
            if target_container is not None:
                container_resolved = await self._resolve_selector(target_container)
                container_locator = container_resolved.locator
            target_selector = getattr(target, "selector", None)
            if target_selector is not None:
                resolved = await self._resolve_selector(target_selector)
                if resolved.locator is None:
                    raise ExecutionError("Resolved locator is unavailable", code="LOCATOR")
                align = getattr(target, "align", "center")
                behavior = getattr(target, "behavior", "smooth")
                await resolved.locator.evaluate(
                    "(el, opts) => el.scrollIntoView({behavior: opts.behavior, block: opts.align, inline: opts.align})",
                    {"behavior": behavior, "align": align},
                )
                details["target"] = resolved.dom_path
        elif action.direction:
            offset = 400 if action.direction == "down" else -400
            await scroll_by(offset)
            details["direction"] = action.direction
            details["offset"] = offset
        elif container_locator is not None:
            await container_locator.scroll_into_view_if_needed()
            if container_resolved:
                details["container"] = container_resolved.dom_path
        else:
            details["message"] = "Scroll action had no effect"

        resolved = resolved or container_resolved
        return ActionOutcome(ok=True, details=details, resolved=resolved)

    async def _scroll_to_text(self, action: ScrollToTextAction) -> ActionOutcome:
        result = await perform_scroll_to_text(self.context.page, action.text)
        if not result.get("success"):
            raise ExecutionError(
                f"Text '{action.text}' not found on page",
                code="ELEMENT_NOT_FOUND",
                details={"text": action.text, "reason": result.get("reason", "not_found")},
            )
        details = {"text": result.get("text", action.text)}
        snippet = result.get("snippet")
        if snippet:
            details["snippet"] = snippet
        return ActionOutcome(ok=True, details=details)

    async def _refresh_catalog(self, action: RefreshCatalogAction) -> ActionOutcome:
        warning = "INFO:auto:refresh_catalog is not supported in typed executor"
        return ActionOutcome(ok=True, details={"action": action.action_name}, warnings=[warning])

    async def _click_blank_area(self, action: ClickBlankAreaAction) -> ActionOutcome:
        result = await perform_click_blank_area(self.context.page)
        warnings: List[str] = []
        if result.get("fallback"):
            warnings.append("INFO:auto:Used fallback coordinates for blank area click")
        return ActionOutcome(ok=bool(result.get("success", False)), details=result, warnings=warnings)

    async def _close_popup(self, action: ClosePopupAction) -> ActionOutcome:
        result = await perform_close_popup(self.context.page)
        warnings: List[str] = []
        if result.get("found") and result.get("clicked"):
            warnings.append(
                "INFO:auto:Closed {count} popup(s) by clicking outside at ({x}, {y})".format(
                    count=result.get("popupCount", 0),
                    x=result.get("x"),
                    y=result.get("y"),
                )
            )
        elif result.get("found") and not result.get("clicked"):
            warnings.append("WARNING:auto:Popup detected but could not find safe click area")
        else:
            warnings.append("INFO:auto:No popups detected to close")
        return ActionOutcome(ok=True, details=result, warnings=warnings)

    async def _eval_js(self, action: EvalJsAction) -> ActionOutcome:
        result = await run_eval_js(self.context.page, action.script)
        self.context._last_result = {"value": result}
        return ActionOutcome(ok=True, details={"result": result})

    async def _stop(self, action: StopAction) -> ActionOutcome:
        self.context.stop_requested = True
        details = {"reason": action.reason, "message": action.message, "stop": True}
        warnings = [f"STOP:auto:Execution paused - {action.reason}: {action.message}"] if action.message else [
            f"STOP:auto:Execution paused - {action.reason}"
        ]
        return ActionOutcome(ok=True, details=details, warnings=warnings)

    async def _switch_tab(self, action: SwitchTabAction) -> ActionOutcome:
        context = self.context.page.context
        pages = context.pages
        target = action.target
        selected: Optional[Page] = None
        if target.strategy == "index":
            index = int(target.value or 0)
            if 0 <= index < len(pages):
                selected = pages[index]
        elif target.strategy == "latest":
            selected = pages[-1]
        elif target.strategy == "previous":
            current_index = pages.index(self.context.page)
            selected = pages[max(0, current_index - 1)]
        elif target.strategy == "next":
            current_index = pages.index(self.context.page)
            if current_index + 1 < len(pages):
                selected = pages[current_index + 1]
        elif target.strategy == "url":
            for page in pages:
                if page.url.startswith(str(target.value)):
                    selected = page
                    break
        elif target.strategy == "title":
            for page in pages:
                title = await page.title()
                if target.value and target.value.lower() in title.lower():
                    selected = page
                    break
        if not selected:
            raise ExecutionError("Tab target not found", code="TARGET_NOT_FOUND")
        self.context.page = selected
        self.context.frame_stack = [selected.main_frame]
        return ActionOutcome(ok=True, details={"target": target.strategy})

    async def _focus_iframe(self, action: FocusIframeAction) -> ActionOutcome:
        frame = self.context.current_frame
        target = action.target
        selected: Optional[Frame] = None
        if target.strategy == "parent":
            if len(self.context.frame_stack) > 1:
                self.context.pop_frame()
                selected = self.context.current_frame
        elif target.strategy == "root":
            self.context.frame_stack = [self.context.page.main_frame]
            selected = self.context.current_frame
        elif target.strategy == "index":
            idx = int(target.value or 0)
            children = frame.child_frames
            if 0 <= idx < len(children):
                selected = children[idx]
        elif target.strategy == "name":
            for child in frame.child_frames:
                if child.name == target.value:
                    selected = child
                    break
        elif target.strategy == "url":
            for child in frame.child_frames:
                if target.value and target.value in child.url:
                    selected = child
                    break
        elif target.strategy == "element" and target.value is not None:
            resolved = await self._resolve_selector(target.value)
            if resolved.element is None:
                raise ExecutionError("Resolved element is unavailable", code="LOCATOR")
            selected = await resolved.element.content_frame()
        if not selected:
            raise ExecutionError("Iframe target not found", code="TARGET_NOT_FOUND")
        self.context.push_frame(selected)
        return ActionOutcome(ok=True, details={"frame": target.strategy})

    async def _screenshot(self, action: ScreenshotAction) -> ActionOutcome:
        frame = self.context.current_frame
        mode = action.mode
        screenshot_path = self.context.paths.shots / f"manual_{int(time.time()*1000)}.png"
        if mode == "viewport":
            await self.context.page.screenshot(path=str(screenshot_path))
        elif mode == "full":
            await self.context.page.screenshot(path=str(screenshot_path), full_page=True)
        elif mode == "element" and action.selector:
            resolved = await self._resolve(action)
            if resolved.locator is None:
                raise ExecutionError("Resolved locator is unavailable", code="LOCATOR")
            await resolved.locator.screenshot(path=str(screenshot_path))
            return ActionOutcome(ok=True, details={"path": str(screenshot_path)}, resolved=resolved)
        return ActionOutcome(ok=True, details={"path": str(screenshot_path)})

    async def _extract(self, action: ExtractAction) -> ActionOutcome:
        resolved = await self._resolve(action)
        if resolved.locator is None:
            raise ExecutionError("Resolved locator is unavailable", code="LOCATOR")
        attr = action.attr
        if attr == "text":
            value = await resolved.locator.inner_text()
        elif attr == "html":
            value = await resolved.locator.inner_html()
        else:
            value = await resolved.locator.get_attribute(attr)
        self.context._last_result = {"value": value}
        return ActionOutcome(ok=True, details={"value": value}, resolved=resolved)

    async def _assert(self, action: AssertAction) -> ActionOutcome:
        resolved = await self._resolve(action)
        if resolved.locator is None:
            raise ExecutionError("Resolved locator is unavailable", code="LOCATOR")
        state = action.state
        timeout = self.context.config.wait_timeout_ms
        await resolved.locator.wait_for(state=state, timeout=timeout)
        return ActionOutcome(ok=True, details={"asserted": state}, resolved=resolved)


class RunExecutor:
    def __init__(self, page: Page, config: Optional[RunConfig] = None) -> None:
        self.page = page
        self.config = config or load_config()
        self.store = StableNodeStore()

    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request = self._parse_payload(payload)
        dirs = ensure_run_directories(request.run_id, self.config)
        log_paths = prepare_log_paths(request.run_id, dirs["base"])
        logger = StructuredLogger(request.run_id, log_paths)
        context = ActionContext(self.page, self.config, logger, log_paths, self.store)
        performer = ActionPerformer(context)
        plan = request.plan.actions
        validation_warnings = self._validate(plan)
        await self._dry_run(performer, plan)
        results: List[Dict[str, Any]] = []
        try:
            for action in plan:
                result = await self._execute_with_retry(performer, action)
                results.append(result.as_dict())
                if performer.context.stop_requested:
                    break
        finally:
            logger.close()
        success = all(r.get("ok") for r in results)
        warnings: List[str] = []
        warnings.extend(validation_warnings)
        for entry in results:
            warnings.extend(entry.get("warnings", []))
        html = ""
        try:
            html = await self.page.content()
        except Exception:
            html = ""
        return {
            "success": success,
            "results": results,
            "warnings": warnings,
            "html": html,
            "run_id": request.run_id,
            "log_path": str(log_paths.events),
        }

    def _parse_payload(self, payload: Dict[str, Any]) -> RunRequest:
        if "plan" in payload:
            plan_value = payload.get("plan")
            sanitized: Dict[str, Any] = {
                "run_id": payload.get("run_id") or f"run-{int(time.time())}",
                "plan": plan_value,
            }
            if "config" in payload:
                sanitized["config"] = payload["config"]
            if "metadata" in payload:
                sanitized["metadata"] = payload["metadata"]
            return RunRequest.model_validate(sanitized)
        actions = payload.get("actions", [])
        plan = []
        for action in actions:
            try:
                plan.append(registry.parse_action(action))
            except Exception:
                continue
        return RunRequest.model_validate({"run_id": payload.get("run_id", f"run-{int(time.time())}"), "plan": plan})

    def _validate(self, plan: List[ActionBase]) -> List[str]:
        warnings: List[str] = []
        for idx, action in enumerate(plan):
            if not isinstance(action, ClickAction):
                continue
            subsequent = plan[idx + 1 : idx + 3]
            if any(isinstance(a, WaitAction) for a in subsequent):
                continue
            safe_successor = False
            for candidate in subsequent:
                if isinstance(candidate, AssertAction):
                    safe_successor = True
                    break
                if isinstance(candidate, NavigateAction) and candidate.wait_for is not None:
                    safe_successor = True
                    break
            if not safe_successor:
                warnings.append(
                    f"WARNING:auto:Click action at position {idx} is not followed by an explicit wait"
                )
        return warnings

    async def _dry_run(self, performer: ActionPerformer, plan: List[ActionBase]) -> None:
        for action in plan:
            try:
                await performer.dry_run(action)
            except ExecutionError:
                raise
            except Exception as exc:
                raise ExecutionError(str(exc), code="DRY_RUN_FAIL") from exc

    async def _execute_with_retry(self, performer: ActionPerformer, action: ActionBase) -> ActionOutcome:
        attempt = 1
        warnings: List[str] = []
        backoff = self.config.retry_backoff_base
        max_retries = max(1, self.config.max_retries)
        while attempt <= max_retries:
            try:
                outcome = await performer.execute(action)
                if warnings:
                    outcome.warnings = [*warnings, *outcome.warnings]
                outcome.attempts = attempt
                await self._log_action(
                    performer.context,
                    action,
                    outcome,
                    retry_count=attempt - 1,
                )
                return outcome
            except ExecutionError as exc:
                warnings.append(f"Execution error {exc.code}: {exc}")
                should_retry = self._should_retry_execution_error(exc)
                if not should_retry or attempt == max_retries:
                    detail_payload = dict(exc.details or {})
                    failure_details = self._build_failure_details(attempt, exc.code, detail_payload)
                    failure = ActionOutcome(
                        ok=False,
                        details=failure_details,
                        warnings=warnings,
                        error=str(exc),
                        error_code=exc.code,
                        error_details=detail_payload,
                        attempts=attempt,
                    )
                    self._write_error_report(
                        performer.context,
                        action,
                        warnings,
                        str(exc),
                        error_code=exc.code,
                        error_details=detail_payload,
                        attempts=attempt,
                    )
                    await self._log_action(
                        performer.context,
                        action,
                        failure,
                        retry_count=attempt - 1,
                    )
                    return failure
            except PlaywrightError as exc:
                warnings.append(f"Playwright error: {exc}")
                if attempt == max_retries:
                    details = {"message": str(exc), "name": exc.__class__.__name__}
                    failure_details = self._build_failure_details(attempt, "PLAYWRIGHT_ERROR", details)
                    failure = ActionOutcome(
                        ok=False,
                        details=failure_details,
                        warnings=warnings,
                        error=str(exc),
                        error_code="PLAYWRIGHT_ERROR",
                        error_details=details,
                        attempts=attempt,
                    )
                    self._write_error_report(
                        performer.context,
                        action,
                        warnings,
                        str(exc),
                        error_code="PLAYWRIGHT_ERROR",
                        error_details=details,
                        attempts=attempt,
                    )
                    await self._log_action(
                        performer.context,
                        action,
                        failure,
                        retry_count=attempt - 1,
                    )
                    return failure
            await asyncio.sleep(min(self.config.retry_backoff_max, backoff + random.random()))
            backoff = min(backoff * 2, self.config.retry_backoff_max)
            attempt += 1

    def _should_retry_execution_error(self, exc: ExecutionError) -> bool:
        non_retryable = {
            "VALIDATION",
            "LOCATOR",
            "TARGET_NOT_FOUND",
            "ELEMENT_NOT_FOUND",
            "DRY_RUN_FAIL",
            "PRESS_KEY_FAILED",
        }
        return exc.code not in non_retryable

    def _build_failure_details(
        self,
        attempts: int,
        error_code: Optional[str],
        error_details: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"attempts": attempts}
        if error_code:
            payload["error_code"] = error_code
        if isinstance(error_details, dict):
            payload["error_details"] = dict(error_details)
        elif error_details is None:
            payload["error_details"] = {}
        else:
            payload["error_details"] = {"value": error_details}
        return payload

    async def _log_action(
        self,
        context: ActionContext,
        action: ActionBase,
        outcome: ActionOutcome,
        *,
        retry_count: int = 0,
    ) -> None:
        resolved = outcome.resolved
        resolved_payload = None
        dom_digest = None
        if resolved:
            dom_digest = context.dom_digest(resolved)
            resolved_payload = {
                "stable_id": resolved.stable_id,
                "score": resolved.score,
                "dom_path": resolved.dom_path,
                "strategy": resolved.strategy,
            }
        next_step = context.logger.next_step_index()
        screenshot_path = context.paths.shots / f"step_{next_step:04d}.png"
        await context.page.screenshot(path=str(screenshot_path))
        step = context.logger.log_event(
            action=action.payload(),
            resolved_selector=resolved_payload,
            result=outcome.details,
            warnings=outcome.warnings,
            error=outcome.error,
            retry_count=retry_count,
            dom_digest_sha=dom_digest,
            screenshot_path=screenshot_path,
        )
        outcome.details.setdefault("step", step)
        outcome.details.setdefault("screenshot", str(screenshot_path))

    def _write_error_report(
        self,
        context: ActionContext,
        action: ActionBase,
        warnings: List[str],
        error: str,
        *,
        error_code: Optional[str] = None,
        error_details: Optional[Dict[str, Any]] = None,
        attempts: int = 1,
    ) -> None:
        report = {
            "action": action.payload(),
            "warnings": warnings,
            "error": error,
            "error_code": error_code,
            "error_details": error_details or {},
            "attempts": attempts,
            "timestamp": time.time(),
        }
        report_path = context.paths.base / "error_report.json"
        try:
            with report_path.open("w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass
