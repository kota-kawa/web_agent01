"""Structured logging utilities for automation runs."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(slots=True)
class LogPaths:
    base: Path
    shots: Path
    events: Path


class StructuredLogger:
    """Writes JSONL events for each automation step."""

    def __init__(self, run_id: str, paths: LogPaths) -> None:
        self.run_id = run_id
        self.paths = paths
        self._step = 0
        self._events_file = paths.events.open("a", encoding="utf-8")

    def next_step_index(self) -> int:
        return self._step + 1

    def log_event(
        self,
        *,
        action: Dict[str, Any],
        resolved_selector: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
        warnings: Optional[list[str]] = None,
        error: Optional[str] = None,
        retry_count: int = 0,
        dom_digest_sha: Optional[str] = None,
        screenshot_path: Optional[Path] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        self._step += 1
        payload = {
            "ts": time.time(),
            "run_id": self.run_id,
            "step": self._step,
            "action": action,
            "resolved_selector": resolved_selector,
            "result": result,
            "warnings": warnings or [],
            "error": error,
            "retry_count": retry_count,
            "dom_digest_sha": dom_digest_sha,
            "screenshot_path": str(screenshot_path) if screenshot_path else None,
            "metadata": metadata or {},
        }
        self._events_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._events_file.flush()
        return self._step

    def close(self) -> None:
        try:
            self._events_file.close()
        except Exception:
            pass


def prepare_log_paths(run_id: str, base_dir: Path) -> LogPaths:
    base_dir.mkdir(parents=True, exist_ok=True)
    shots_dir = base_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    events_file = base_dir / "events.jsonl"
    return LogPaths(base=base_dir, shots=shots_dir, events=events_file)
