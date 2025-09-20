"""Utility watchers that monitor Playwright page events for automatic recovery."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from playwright.async_api import Dialog, Error as PlaywrightError, Page


class PageWatchdog:
    """Attach Playwright event listeners to react to unexpected browser events."""

    def __init__(
        self,
        page: Page,
        *,
        default_dialog_action: str = "accept",
        prompt_text: Optional[str] = None,
    ) -> None:
        self.page = page
        self.default_dialog_action = default_dialog_action
        self.prompt_text = prompt_text
        self.dialog_events: List[Dict[str, Any]] = []
        self.page_errors: List[Dict[str, Any]] = []
        self.recoveries: List[Dict[str, Any]] = []
        self._listeners: List[Tuple[str, Callable[..., Any]]] = []
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._register_async("dialog", self._handle_dialog)
        self._register("pageerror", self._handle_page_error)
        self._register("crash", self._handle_crash)

    def stop(self) -> None:
        if not self._started:
            return
        for event, handler in self._listeners:
            try:
                self.page.off(event, handler)
            except Exception:
                pass
        self._listeners.clear()
        self._started = False

    def record_recovery(
        self,
        *,
        source: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        level: str = "INFO",
        emit_warning: bool = True,
    ) -> None:
        self.recoveries.append(
            {
                "timestamp": time.time(),
                "source": source,
                "message": message,
                "details": details or {},
                "level": level.upper(),
                "emit_warning": emit_warning,
            }
        )

    def collect_warnings(self) -> List[str]:
        warnings: List[str] = []
        for event in self.dialog_events:
            summary = event.get("summary")
            level = event.get("level", "INFO")
            if summary:
                warnings.append(f"{level}:auto:{summary}")
        for event in self.page_errors:
            summary = event.get("summary")
            level = event.get("level", "WARNING")
            if summary:
                warnings.append(f"{level}:auto:{summary}")
        for event in self.recoveries:
            if not event.get("emit_warning", True):
                continue
            summary = event.get("message")
            level = event.get("level", "INFO")
            if summary:
                warnings.append(f"{level}:auto:{summary}")
        return warnings

    def snapshot(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.dialog_events:
            data["dialogs"] = list(self.dialog_events)
        if self.page_errors:
            data["page_errors"] = list(self.page_errors)
        if self.recoveries:
            data["recoveries"] = list(self.recoveries)
        return data

    def _register(self, event: str, handler: Callable[..., Any]) -> None:
        self.page.on(event, handler)
        self._listeners.append((event, handler))

    def _register_async(self, event: str, handler: Callable[..., Any]) -> None:
        async def _wrapper(*args: Any, **kwargs: Any) -> None:
            await handler(*args, **kwargs)

        self.page.on(event, _wrapper)
        self._listeners.append((event, _wrapper))

    def _dialog_strategy(self, dialog: Dialog) -> str:
        if dialog.type == "beforeunload":
            return "accept"
        if dialog.type in {"alert", "confirm", "prompt"}:
            return self.default_dialog_action
        return self.default_dialog_action

    async def _handle_dialog(self, dialog: Dialog) -> None:
        action = self._dialog_strategy(dialog)
        event: Dict[str, Any] = {
            "timestamp": time.time(),
            "type": dialog.type,
            "message": dialog.message,
            "default_value": dialog.default_value,
            "action": action,
        }
        try:
            if action == "accept":
                if dialog.type == "prompt" and self.prompt_text is not None:
                    await dialog.accept(self.prompt_text)
                    event["accepted_value"] = self.prompt_text
                else:
                    await dialog.accept()
                event["status"] = "accepted"
                event["level"] = "INFO"
                event["summary"] = f"{dialog.type} dialog automatically accepted"
            elif action == "dismiss":
                await dialog.dismiss()
                event["status"] = "dismissed"
                event["level"] = "INFO"
                event["summary"] = f"{dialog.type} dialog automatically dismissed"
            else:
                event["status"] = "ignored"
                event["level"] = "WARNING"
                event["summary"] = f"{dialog.type} dialog shown without handler"
        except PlaywrightError as exc:
            event["status"] = "error"
            event["level"] = "WARNING"
            event["error"] = str(exc)
            event["summary"] = f"Failed to {action or 'handle'} {dialog.type} dialog: {exc}"
        except Exception as exc:  # pragma: no cover - safety net
            event["status"] = "error"
            event["level"] = "WARNING"
            event["error"] = str(exc)
            event["summary"] = f"Failed to {action or 'handle'} {dialog.type} dialog: {exc}"
        self.dialog_events.append(event)

    def _handle_page_error(self, error: PlaywrightError) -> None:
        message = getattr(error, "message", None) or str(error)
        self.page_errors.append(
            {
                "timestamp": time.time(),
                "message": message,
                "level": "WARNING",
                "summary": f"Page error captured: {message}",
            }
        )

    def _handle_crash(self) -> None:
        self.page_errors.append(
            {
                "timestamp": time.time(),
                "message": "Page crashed",
                "level": "ERROR",
                "summary": "Page crashed unexpectedly",
            }
        )
