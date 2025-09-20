from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation.dsl import NavigateAction
from vnc.executor import RunExecutor


def test_parse_payload_ignores_extra_fields() -> None:
    executor = RunExecutor(page=MagicMock())
    payload = {
        "run_id": "test-run",
        "plan": {
            "actions": [
                {
                    "type": "navigate",
                    "url": "https://example.com",
                }
            ]
        },
        "actions": [
            {
                "type": "click",
                "selector": {"css": "button"},
            }
        ],
        "expected_catalog_version": "1.0.0",
    }

    request = executor._parse_payload(payload)

    assert request.run_id == "test-run"
    assert len(request.plan.actions) == 1
    action = request.plan.actions[0]
    assert isinstance(action, NavigateAction)
    assert action.url == "https://example.com"
