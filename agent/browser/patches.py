from __future__ import annotations

"""Runtime patches that smooth integration with ``browser_use``."""

import asyncio
import logging
import shutil
import sys
from typing import Iterable

_patch_applied = False


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
        from browser_use.browser.watchdogs.local_browser_watchdog import (  # type: ignore import
            LocalBrowserWatchdog,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Could not import LocalBrowserWatchdog for patching: %s", exc)
        return

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
