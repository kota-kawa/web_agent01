from __future__ import annotations

"""Runtime patches that smooth integration with ``browser_use``."""

import asyncio
import logging
import os
import shutil
import sys
from typing import Iterable, Type

_patch_applied = False

_DEFAULT_BROWSER_LAUNCH_TIMEOUT = 120.0


def _safe_parse_timeout(
    raw_value: str | None,
    *,
    env_name: str,
    logger: logging.Logger,
) -> float | None:
    """Parse a timeout value from environment configuration."""

    if raw_value is None or raw_value == "":
        return None

    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid value for %s: %s – ignoring", env_name, raw_value
        )
        return None

    if parsed <= 0:
        logger.warning(
            "Non-positive value for %s: %s – ignoring", env_name, raw_value
        )
        return None

    return parsed


def _gather_launch_timeout_configuration(
    logger: logging.Logger,
) -> tuple[float, dict[str, float | None]]:
    """Determine the desired browser launch timeout."""

    env_names = (
        "BROWSER_USE_LAUNCH_TIMEOUT",
        "TIMEOUT_BrowserLaunchEvent",
        "TIMEOUT_BrowserStartEvent",
    )

    parsed_values: dict[str, float | None] = {}
    highest: float | None = None

    for name in env_names:
        parsed = _safe_parse_timeout(os.getenv(name), env_name=name, logger=logger)
        parsed_values[name] = parsed
        if parsed is not None:
            highest = parsed if highest is None else max(highest, parsed)

    if highest is None:
        highest = _DEFAULT_BROWSER_LAUNCH_TIMEOUT
        logger.debug(
            "No browser launch timeout overrides found; using default %.1fs",
            highest,
        )
    elif highest < _DEFAULT_BROWSER_LAUNCH_TIMEOUT:
        logger.info(
            "Configured browser launch timeout %.1fs below safe minimum %.1fs; "
            "using minimum instead",
            highest,
            _DEFAULT_BROWSER_LAUNCH_TIMEOUT,
        )
        highest = _DEFAULT_BROWSER_LAUNCH_TIMEOUT
    else:
        logger.debug(
            "Using configured browser launch timeout %.1fs", highest
        )

    return highest, parsed_values


def _ensure_env_timeout(
    env_name: str,
    minimum: float,
    parsed_values: dict[str, float | None],
    logger: logging.Logger,
) -> None:
    """Ensure the environment timeout matches the required minimum."""

    current = parsed_values.get(env_name)
    if current is not None and current >= minimum:
        return

    previous_display = "unset" if current is None else f"{current:.1f}s"
    os.environ[env_name] = str(minimum)
    logger.info(
        "Setting %s to %.1fs (previously %s)",
        env_name,
        minimum,
        previous_display,
    )


def _ensure_event_timeout(
    event_cls: Type,
    minimum: float,
    logger: logging.Logger,
) -> None:
    """Update ``event_timeout`` on ``browser_use`` event classes if needed."""

    field = getattr(event_cls, "model_fields", {}).get("event_timeout")
    if field is None:
        return

    current_default = field.default
    try:
        current_value = float(current_default) if current_default is not None else None
    except (TypeError, ValueError):
        current_value = None

    if current_value is not None and current_value >= minimum:
        return

    previous_display = "unset" if current_value is None else f"{current_value:.1f}s"
    field.default = minimum
    setattr(event_cls, "event_timeout", minimum)
    try:
        event_cls.model_rebuild(force=True)
    except Exception as exc:  # pragma: no cover - defensive logging only
        logger.debug(
            "Failed to rebuild %s after updating timeout: %s",
            event_cls.__name__,
            exc,
        )
    else:
        logger.info(
            "Updated %s.event_timeout to %.1fs (previously %s)",
            event_cls.__name__,
            minimum,
            previous_display,
        )


async def _run_subprocess(command: Iterable[str]) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120.0)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise
    return process.returncode, stdout, stderr


def apply_browser_use_patches(logger: logging.Logger | None = None) -> None:
    global _patch_applied
    if _patch_applied:
        return

    log = logger or logging.getLogger(__name__)
    try:
        from browser_use.browser.events import BrowserLaunchEvent, BrowserStartEvent
        from browser_use.browser.watchdogs.local_browser_watchdog import (  # type: ignore import
            LocalBrowserWatchdog,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Could not import browser_use components for patching: %s", exc)
        return

    launch_timeout, parsed_timeouts = _gather_launch_timeout_configuration(log)
    for env_name in ("TIMEOUT_BrowserStartEvent", "TIMEOUT_BrowserLaunchEvent"):
        _ensure_env_timeout(env_name, launch_timeout, parsed_timeouts, log)

    for event_cls in (BrowserStartEvent, BrowserLaunchEvent):
        _ensure_event_timeout(event_cls, launch_timeout, log)

    if not getattr(LocalBrowserWatchdog._wait_for_cdp_url, "_agent_patch", False):  # type: ignore[attr-defined]
        original_wait_for_cdp_url = LocalBrowserWatchdog._wait_for_cdp_url

        async def _wait_for_cdp_url(port: int, timeout: float = 30) -> str:
            effective_timeout = timeout if timeout and timeout > 0 else launch_timeout
            if effective_timeout < launch_timeout:
                effective_timeout = launch_timeout
            return await original_wait_for_cdp_url(port, timeout=effective_timeout)

        _wait_for_cdp_url._agent_patch = True  # type: ignore[attr-defined]
        LocalBrowserWatchdog._wait_for_cdp_url = staticmethod(_wait_for_cdp_url)
        log.info(
            "Patched LocalBrowserWatchdog to wait up to %.1fs for CDP to be ready",
            launch_timeout,
        )

    if getattr(LocalBrowserWatchdog._install_browser_with_playwright, "_agent_patch", False):  # type: ignore[attr-defined]
        _patch_applied = True
        return

    async def _install_browser_with_playwright(self):  # type: ignore[override]
        existing_path = self._find_installed_browser_path()
        if existing_path:
            return existing_path

        commands = []
        playwright_cli = shutil.which("playwright")
        if playwright_cli:
            commands.append([playwright_cli, "install", "chrome", "--with-deps"])
            commands.append([playwright_cli, "install", "chrome"])
        commands.append([sys.executable, "-m", "playwright", "install", "chrome", "--with-deps"])
        commands.append([sys.executable, "-m", "playwright", "install", "chrome"])

        last_stdout = b""
        last_stderr = b""
        attempted: list[str] = []
        for command in commands:
            cmd_display = " ".join(command)
            if cmd_display in attempted:
                continue
            attempted.append(cmd_display)
            try:
                returncode, stdout, stderr = await _run_subprocess(command)
            except FileNotFoundError:
                self.logger.debug("Playwright helper not found for command: %s", cmd_display)
                continue
            except asyncio.TimeoutError:
                self.logger.warning(
                    "Timed out waiting for Playwright command: %s", cmd_display
                )
                continue

            if returncode == 0:
                browser_path = self._find_installed_browser_path()
                if browser_path:
                    self.logger.debug(
                        "Playwright install succeeded with command: %s", cmd_display
                    )
                    return browser_path
            last_stdout, last_stderr = stdout, stderr

        raise RuntimeError(
            "Failed to install browser using Playwright commands. "
            f"Tried: {attempted}. "
            f"stdout={last_stdout.decode(errors='ignore')} stderr={last_stderr.decode(errors='ignore')}"
        )

    _install_browser_with_playwright._agent_patch = True  # type: ignore[attr-defined]
    LocalBrowserWatchdog._install_browser_with_playwright = _install_browser_with_playwright
    _patch_applied = True

    log.info("Patched LocalBrowserWatchdog to use python -m playwright when uvx is unavailable")
