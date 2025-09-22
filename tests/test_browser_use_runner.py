from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - defensive for test environment
    sys.path.insert(0, str(ROOT))

from agent.browser_use_runner import BrowserUseSession


class DummyStructured:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def model_dump(self) -> dict[str, object]:
        return self._data


class DummyHistory:
    def __init__(self, structured: DummyStructured | None) -> None:
        self._structured = structured

    @property
    def structured_output(self) -> DummyStructured | None:  # pragma: no cover - simple attribute access
        return self._structured

    def is_successful(self) -> bool:
        return True

    def errors(self) -> list[str | None]:
        return [None, "err"]

    def final_result(self) -> str:
        return "done"

    def urls(self) -> list[str | None]:
        return [None, "https://example.com"]

    def number_of_steps(self) -> int:
        return 2

    def total_duration_seconds(self) -> float:
        return 1.5


def test_finalise_result_handles_none_structured_output() -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)
    history = DummyHistory(structured=None)

    session._finalise_result(history)

    assert session.result is not None
    assert session.result["success"] is True
    assert session.result["errors"] == ["err"]
    assert "structured_output" not in session.result


def test_finalise_result_includes_structured_output_when_present() -> None:
    session = BrowserUseSession(command="cmd", model_name="model", max_steps=1)
    history = DummyHistory(structured=DummyStructured({"foo": "bar"}))

    session._finalise_result(history)

    assert session.result is not None
    assert session.result["structured_output"] == {"foo": "bar"}
