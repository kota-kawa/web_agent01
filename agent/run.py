"""Command line helper for starting browser-use sessions."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict

import requests

DEFAULT_SERVER = "http://localhost:7000"


def load_task(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("task file must contain a JSON object")
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run automation tasks via the browser-use session API"
    )
    parser.add_argument("--task", required=True, help="Path to task JSON file")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="Automation server base URL")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds while waiting for completion",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    task_path = Path(args.task)
    if not task_path.exists():
        parser.error(f"Task file {task_path} does not exist")

    try:
        payload = load_task(task_path)
    except Exception as exc:  # pragma: no cover - defensive
        parser.error(f"Failed to parse task file: {exc}")

    command = str(payload.get("command", "")).strip()
    if not command:
        parser.error("Task file must include a 'command' field")

    request_payload: Dict[str, Any] = {"command": command}
    if "model" in payload:
        request_payload["model"] = payload["model"]
    if "max_steps" in payload:
        request_payload["max_steps"] = payload["max_steps"]

    try:
        response = requests.post(
            f"{args.server}/browser-use/session", json=request_payload, timeout=30
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        parser.error(f"Failed to start session: {exc}")

    data = response.json()
    session_id = data.get("session_id")
    if not session_id:
        parser.error("automation server response missing session identifier")

    status_url = f"{args.server}/browser-use/session/{session_id}"

    while True:
        try:
            status_resp = requests.get(status_url, timeout=30)
            status_resp.raise_for_status()
        except requests.RequestException as exc:
            parser.error(f"Failed to poll session status: {exc}")

        status_data = status_resp.json()
        print(json.dumps(status_data, indent=2, ensure_ascii=False))

        state = str(status_data.get("status", "")).lower()
        if state in {"completed", "failed", "cancelled"}:
            break
        time.sleep(max(args.interval, 0.1))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
