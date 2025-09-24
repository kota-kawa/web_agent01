import pytest

from agent import element_catalog


@pytest.fixture(autouse=True)
def _reset_catalog_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(element_catalog, "_cached_catalog", None)
    monkeypatch.setattr(element_catalog, "_catalog_dirty", False)
    monkeypatch.setattr(element_catalog, "_pending_prompt_messages", [])


def test_handle_execution_feedback_marks_dirty_on_detached(monkeypatch: pytest.MonkeyPatch) -> None:
    reasons: list[str | None] = []
    monkeypatch.setattr(element_catalog, "mark_catalog_dirty", reasons.append)

    queued: list[str] = []
    monkeypatch.setattr(element_catalog, "_queue_prompt_message", queued.append)

    actions = [{"action": "click", "target": "index=31"}]
    result = {
        "warnings": ["WARNING:auto:click operation failed - Node is detached from document"],
    }

    element_catalog.handle_execution_feedback(actions, result)

    assert reasons, "catalog should be marked dirty on detached node warnings"
    assert any("stale" in (reason or "") for reason in reasons)
    assert queued and queued[0].startswith("CATALOG_OUTDATED")


def test_handle_execution_feedback_ignores_non_index_for_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    dirty_calls: list[str | None] = []
    monkeypatch.setattr(element_catalog, "mark_catalog_dirty", dirty_calls.append)

    queued: list[str] = []
    monkeypatch.setattr(element_catalog, "_queue_prompt_message", queued.append)

    result = {
        "results": [
            {
                "warnings": [
                    "WARNING:auto:click operation failed - Could not compute box model. Retry recommended"
                ]
            }
        ]
    }

    element_catalog.handle_execution_feedback([{"action": "click", "target": "css=#submit"}], result)

    assert dirty_calls, "dirty flag should be set even when prompt update is skipped"
    assert not queued, "prompt message should not be queued when indices were not used"
