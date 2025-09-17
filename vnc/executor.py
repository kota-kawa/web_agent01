"""Deterministic execution pipeline for typed DSL actions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import Error as PlaywrightError, Frame, Page

from automation.dsl import (
    ClickAction,
    ExtractAction,
    FocusIframeAction,
    NavigateAction,
    PressKeyAction,
    RunRequest,
    ScrollAction,
    ScreenshotAction,
    SelectAction,
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
from .selector_resolver import SelectorResolver, StableNodeStore
from .structured_logging import LogPaths, StructuredLogger, prepare_log_paths


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ActionOutcome:
    ok: bool
    details: Dict[str, Any]
    resolved: Optional[ResolvedNode] = None
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        payload = {"ok": self.ok, "details": self.details}
        if self.warnings:
            payload["warnings"] = self.warnings
        if self.error:
            payload["error"] = self.error
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
        if isinstance(action, (ClickAction, TypeAction, SelectAction, ScreenshotAction, ExtractAction, AssertAction)):
            await self._resolve(action)
        if isinstance(action, ScrollAction) and getattr(action, "to", None):
            target = action.to
            if hasattr(target, "selector") and target.selector is not None:
                await self._resolve_selector(target.selector)

    async def execute(self, action: ActionBase) -> ActionOutcome:
        if isinstance(action, NavigateAction):
            return await self._navigate(action)
        if isinstance(action, ClickAction):
            return await self._click(action)
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
        await self.context.page.goto(action.url, wait_until="domcontentloaded", timeout=self.context.config.navigation_timeout_ms)
        return ActionOutcome(ok=True, details={"url": action.url})

    async def _click(self, action: ClickAction) -> ActionOutcome:
        resolved = await self._resolve(action)
        element = resolved.element
        await element.click(button=action.button, click_count=action.click_count, delay=action.delay_ms)
        return ActionOutcome(ok=True, details={"stable_id": resolved.stable_id}, resolved=resolved)

    async def _type(self, action: TypeAction) -> ActionOutcome:
        resolved = await self._resolve(action)
        element = resolved.element
        if action.clear:
            await element.fill("")
        await element.fill(action.text)
        try:
            current_value = await element.input_value()
        except Exception:
            current_value = await element.evaluate("el => el.value || el.textContent || ''")
        if current_value.strip() != action.text.strip():
            raise ExecutionError("Input verification failed", code="INPUT_MISMATCH", details={"expected": action.text, "actual": current_value})
        if action.press_enter:
            await self.context.current_frame.press("body", "Enter")
        return ActionOutcome(ok=True, details={"text": action.text}, resolved=resolved)

    async def _select(self, action: SelectAction) -> ActionOutcome:
        resolved = await self._resolve(action)
        element = resolved.element
        await element.select_option(value=action.value_or_label)
        return ActionOutcome(ok=True, details={"value": action.value_or_label}, resolved=resolved)

    async def _press_key(self, action: PressKeyAction) -> ActionOutcome:
        keys_combo = " ".join(action.keys)
        if action.scope == "active_element":
            await self.context.current_frame.press("body", keys_combo)
        else:
            await self.context.page.keyboard.press(keys_combo)
        return ActionOutcome(ok=True, details={"keys": action.keys})

    async def _wait(self, action: WaitAction) -> ActionOutcome:
        condition = action.for_
        frame = self.context.current_frame
        if condition is None:
            await frame.wait_for_timeout(action.timeout_ms)
            return ActionOutcome(ok=True, details={"waited_ms": action.timeout_ms})
        if isinstance(condition, WaitForTimeout):
            await frame.wait_for_timeout(condition.timeout_ms)
            return ActionOutcome(ok=True, details={"waited_ms": condition.timeout_ms})
        if isinstance(condition, WaitForState):
            await self.context.page.wait_for_load_state(condition.state, timeout=action.timeout_ms)
            return ActionOutcome(ok=True, details={"state": condition.state})
        if isinstance(condition, WaitForSelector):
            resolved = await self._resolve_selector(condition.selector)
            locator = frame.locator(resolved.dom_path)
            state = condition.state
            await locator.first.wait_for(state=state, timeout=action.timeout_ms)
            return ActionOutcome(ok=True, details={"selector": resolved.dom_path, "state": state}, resolved=resolved)
        raise ExecutionError("Unsupported wait condition", code="VALIDATION")

    async def _scroll(self, action: ScrollAction) -> ActionOutcome:
        frame = self.context.current_frame
        if isinstance(action.to, int):
            await frame.evaluate("(offset) => window.scrollBy(0, offset)", action.to)
            details = {"offset": action.to}
        elif isinstance(action.to, str):
            if action.to == "top":
                await frame.evaluate("() => window.scrollTo({top: 0, behavior: 'smooth'})")
            elif action.to == "bottom":
                await frame.evaluate("() => window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
            details = {"position": action.to}
        elif action.to:
            target = action.to
            if hasattr(target, "selector") and target.selector is not None:
                resolved = await self._resolve_selector(target.selector)
                element = resolved.element
                await element.scroll_into_view_if_needed()
                details = {"stable_id": resolved.stable_id}
            else:
                details = {"target": str(target)}
        return ActionOutcome(ok=True, details=details)

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
            element = resolved.element
            selected = await element.content_frame()
        if not selected:
            raise ExecutionError("Iframe target not found", code="TARGET_NOT_FOUND")
        self.context.push_frame(selected)
        return ActionOutcome(ok=True, details={"frame": target.strategy})

    async def _screenshot(self, action: ScreenshotAction) -> ActionOutcome:
        mode = action.mode
        screenshot_path = self.context.paths.shots / f"manual_{int(time.time()*1000)}.png"
        timeout = self.context.config.screenshot_timeout_ms
        try:
            if mode == "viewport":
                await self.context.page.screenshot(
                    path=str(screenshot_path),
                    timeout=timeout,
                    animations="disabled",
                )
            elif mode == "full":
                await self.context.page.screenshot(
                    path=str(screenshot_path),
                    full_page=True,
                    timeout=timeout,
                    animations="disabled",
                )
            elif mode == "element" and action.selector:
                resolved = await self._resolve(action)
                await resolved.element.screenshot(path=str(screenshot_path), timeout=timeout)
                return ActionOutcome(ok=True, details={"path": str(screenshot_path)}, resolved=resolved)
        except PlaywrightError as exc:
            raise ExecutionError("Screenshot capture failed", code="SCREENSHOT_ERROR", details={"error": str(exc)}) from exc
        return ActionOutcome(ok=True, details={"path": str(screenshot_path)})

    async def _extract(self, action: ExtractAction) -> ActionOutcome:
        resolved = await self._resolve(action)
        element = resolved.element
        attr = action.attr
        if attr == "text":
            value = await element.inner_text()
        elif attr == "html":
            value = await element.inner_html()
        else:
            value = await element.get_attribute(attr)
        self.context._last_result = {"value": value}
        return ActionOutcome(ok=True, details={"value": value}, resolved=resolved)

    async def _assert(self, action: AssertAction) -> ActionOutcome:
        resolved = await self._resolve(action)
        element = resolved.element
        state = action.state
        timeout = self.context.config.wait_timeout_ms
        if state == "visible":
            await element.wait_for_element_state("visible", timeout=timeout)
        elif state == "hidden":
            await element.wait_for_element_state("hidden", timeout=timeout)
        elif state == "detached":
            await element.wait_for_element_state("detached", timeout=timeout)
        elif state == "attached":
            await element.wait_for_element_state("stable", timeout=timeout)
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
        self._validate(plan)
        await self._dry_run(performer, plan)
        results: List[Dict[str, Any]] = []
        try:
            for action in plan:
                result = await self._execute_with_retry(performer, action)
                results.append(result.as_dict())
        finally:
            logger.close()
        success = all(r.get("ok") for r in results)
        warnings: List[str] = []
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
            return RunRequest.model_validate(payload)
        actions = payload.get("actions", [])
        plan = []
        for action in actions:
            try:
                plan.append(registry.parse_action(action))
            except Exception:
                continue
        return RunRequest.model_validate({"run_id": payload.get("run_id", f"run-{int(time.time())}"), "plan": plan})

    def _validate(self, plan: List[ActionBase]) -> None:
        for idx, action in enumerate(plan):
            if isinstance(action, ClickAction):
                subsequent = plan[idx + 1 : idx + 3]
                if not any(isinstance(a, WaitAction) for a in subsequent):
                    raise ExecutionError("Click action must be followed by wait", code="VALIDATION_ERROR")

    async def _dry_run(self, performer: ActionPerformer, plan: List[ActionBase]) -> None:
        for action in plan:
            try:
                await performer.dry_run(action)
            except ExecutionError:
                raise
            except Exception as exc:
                raise ExecutionError(str(exc), code="DRY_RUN_FAIL") from exc

    async def _execute_with_retry(self, performer: ActionPerformer, action: ActionBase) -> ActionOutcome:
        attempt = 0
        warnings: List[str] = []
        backoff = self.config.retry_backoff_base
        while True:
            try:
                outcome = await performer.execute(action)
                outcome.warnings = warnings or outcome.warnings
                await self._log_action(performer.context, action, outcome)
                return outcome
            except ExecutionError as exc:
                attempt += 1
                warnings.append(f"Execution error {exc.code}: {exc}")
                if attempt >= self.config.max_retries:
                    failure = ActionOutcome(ok=False, details={}, warnings=warnings, error=str(exc))
                    self._write_error_report(performer.context, action, warnings, str(exc))
                    await self._log_action(performer.context, action, failure)
                    return failure
            except PlaywrightError as exc:
                attempt += 1
                warnings.append(f"Playwright error: {exc}")
                if attempt >= self.config.max_retries:
                    failure = ActionOutcome(ok=False, details={}, warnings=warnings, error=str(exc))
                    self._write_error_report(performer.context, action, warnings, str(exc))
                    await self._log_action(performer.context, action, failure)
                    return failure
            await asyncio.sleep(min(self.config.retry_backoff_max, backoff + random.random()))
            backoff *= 2

    async def _log_action(self, context: ActionContext, action: ActionBase, outcome: ActionOutcome) -> None:
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
        screenshot_recorded: Optional[Path] = screenshot_path
        metadata: Dict[str, Any] = {}
        warnings = list(outcome.warnings)
        try:
            await context.page.screenshot(
                path=str(screenshot_path),
                timeout=context.config.screenshot_timeout_ms,
                animations="disabled",
            )
        except PlaywrightError as exc:
            message = f"Screenshot capture failed: {exc}"
            logger.warning(message)
            warnings.append(message)
            metadata["screenshot_error"] = message
            screenshot_recorded = None
        except Exception as exc:
            message = f"Screenshot capture unexpected failure: {exc}"
            logger.warning(message)
            warnings.append(message)
            metadata["screenshot_error"] = message
            screenshot_recorded = None
        outcome.warnings = warnings
        step = context.logger.log_event(
            action=action.payload(),
            resolved_selector=resolved_payload,
            result=outcome.details,
            warnings=warnings,
            error=outcome.error,
            dom_digest_sha=dom_digest,
            screenshot_path=screenshot_recorded,
            metadata=metadata or None,
        )
        outcome.details.setdefault("step", step)
        if screenshot_recorded:
            outcome.details.setdefault("screenshot", str(screenshot_recorded))

    def _write_error_report(
        self,
        context: ActionContext,
        action: ActionBase,
        warnings: List[str],
        error: str,
    ) -> None:
        report = {
            "action": action.payload(),
            "warnings": warnings,
            "error": error,
            "timestamp": time.time(),
        }
        report_path = context.paths.base / "error_report.json"
        try:
            with report_path.open("w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass
