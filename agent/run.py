"""Command line runner for automation tasks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

import requests

DEFAULT_SERVER = "http://localhost:7000"


def load_task(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run automation tasks via the DSL executor")
    parser.add_argument("--task", required=True, help="Path to task JSON file")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="Automation server base URL")
    parser.add_argument("--out", default="runs", help="Directory where runs are stored on the server")
    parser.add_argument("--headful", action="store_true", help="Request non-headless execution")
    parser.add_argument("--stream", action="store_true", help="Print streaming events if available")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    task_path = Path(args.task)
    if not task_path.exists():
        parser.error(f"Task file {task_path} does not exist")

    payload = load_task(task_path)
    payload.setdefault("config", {})
    payload["config"].setdefault("log_root", args.out)
    if args.headful:
        payload["config"]["headless"] = False

    try:
        response = requests.post(f"{args.server}/execute-dsl", json=payload, timeout=120)
        response.raise_for_status()
    except requests.RequestException as exc:
        parser.error(f"Failed to submit task: {exc}")

    data = response.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))

    if args.stream:
        run_id = data.get("run_id")
        if not run_id:
            print("No run_id returned; cannot stream events", file=sys.stderr)
            return 0
        try:
            events_resp = requests.get(f"{args.server}/events/{run_id}", timeout=10)
            if events_resp.status_code == 200:
                print(events_resp.text)
            else:
                print("Streaming endpoint unavailable", file=sys.stderr)
        except requests.RequestException:
            print("Streaming endpoint unavailable", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
