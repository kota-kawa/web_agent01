"""Configuration loader for the automation runtime."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict


DEFAULTS: Dict[str, Any] = {
    "action_timeout_ms": 10000,
    "navigation_timeout_ms": 30000,
    "wait_timeout_ms": 10000,
    "max_retries": 3,
    "retry_backoff_base": 0.5,
    "retry_backoff_max": 5.0,
    "log_root": "runs",
    "headless": True,
    "screenshot_mode": "viewport",
}


@dataclass(slots=True)
class RunConfig:
    action_timeout_ms: int = DEFAULTS["action_timeout_ms"]
    navigation_timeout_ms: int = DEFAULTS["navigation_timeout_ms"]
    wait_timeout_ms: int = DEFAULTS["wait_timeout_ms"]
    max_retries: int = DEFAULTS["max_retries"]
    retry_backoff_base: float = DEFAULTS["retry_backoff_base"]
    retry_backoff_max: float = DEFAULTS["retry_backoff_max"]
    log_root: Path = field(default_factory=lambda: Path(DEFAULTS["log_root"]))
    headless: bool = DEFAULTS["headless"]
    screenshot_mode: str = DEFAULTS["screenshot_mode"]

    @classmethod
    def from_mapping(cls, mapping: Dict[str, Any]) -> "RunConfig":
        data = dict(DEFAULTS)
        data.update(mapping)
        data["log_root"] = Path(data.get("log_root", DEFAULTS["log_root"]))
        return cls(
            action_timeout_ms=int(data["action_timeout_ms"]),
            navigation_timeout_ms=int(data["navigation_timeout_ms"]),
            wait_timeout_ms=int(data["wait_timeout_ms"]),
            max_retries=int(data["max_retries"]),
            retry_backoff_base=float(data["retry_backoff_base"]),
            retry_backoff_max=float(data["retry_backoff_max"]),
            log_root=Path(data["log_root"]),
            headless=bool(str(data["headless"]).lower() in {"true", "1", "yes"}),
            screenshot_mode=str(data["screenshot_mode"]),
        )


def _load_toml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(config_path: Path | None = None) -> RunConfig:
    """Load configuration from environment, optional TOML file, and defaults."""

    env_map: Dict[str, Any] = {}
    for key, value in os.environ.items():
        if key.startswith("AGENT_"):
            env_map[key[6:].lower()] = value

    file_map: Dict[str, Any] = {}
    path = config_path or Path("config.toml")
    if path.exists():
        file_map = _load_toml(path).get("agent", {})

    merged = {**file_map, **env_map}
    return RunConfig.from_mapping(merged)


def ensure_run_directories(run_id: str, config: RunConfig) -> Dict[str, Path]:
    base = config.log_root / run_id
    shots = base / "shots"
    base.mkdir(parents=True, exist_ok=True)
    shots.mkdir(parents=True, exist_ok=True)
    return {"base": base, "shots": shots}
