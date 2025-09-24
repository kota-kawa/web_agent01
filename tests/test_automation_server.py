from __future__ import annotations

import pytest

from vnc import automation_server


def _make_client(monkeypatch: pytest.MonkeyPatch, status: str, captured: dict[str, str]):
    class DummyManager:
        def add_instruction(self, session_id: str, instruction: str) -> str:
            captured["session_id"] = session_id
            captured["instruction"] = instruction
            return status

    manager = DummyManager()
    monkeypatch.setattr(automation_server, "_get_browser_use_manager", lambda: manager)
    return automation_server.app.test_client()


def test_add_instruction_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    client = _make_client(monkeypatch, "accepted", captured)

    response = client.post(
        "/browser-use/session/demo/instruction",
        json={"instruction": "次の作業を続けて"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"status": "accepted"}
    assert captured == {"session_id": "demo", "instruction": "次の作業を続けて"}


@pytest.mark.parametrize(
    "status, expected_code, expected_payload",
    [
        ("not_found", 404, {"error": "session not found"}),
        (
            "not_running",
            409,
            {
                "error": "セッションは既に完了または停止しています。",
                "status": "not_running",
            },
        ),
        ("invalid", 400, {"error": "instruction empty"}),
    ],
)
def test_add_instruction_error_responses(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_code: int,
    expected_payload: dict[str, str],
) -> None:
    captured: dict[str, str] = {}
    client = _make_client(monkeypatch, status, captured)

    response = client.post(
        "/browser-use/session/demo/instruction",
        json={"command": "  フォールバック  "},
    )

    assert response.status_code == expected_code
    assert response.get_json() == expected_payload
    assert captured == {"session_id": "demo", "instruction": "フォールバック"}


def test_add_instruction_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    client = _make_client(monkeypatch, "accepted", captured)

    response = client.post(
        "/browser-use/session/demo/instruction",
        json={"instruction": "   "},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "instruction empty"}
    assert captured == {}
