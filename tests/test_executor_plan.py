from unittest.mock import MagicMock

from automation.dsl import ClickAction, Selector, WaitAction, WaitForSelector

from vnc.config import RunConfig
from vnc.executor import RunExecutor


def _make_executor(post_wait: int = 650) -> RunExecutor:
    config = RunConfig(post_interaction_wait_ms=post_wait)
    page = MagicMock()
    return RunExecutor(page, config=config)


def test_augment_plan_inserts_wait_after_index_click() -> None:
    executor = _make_executor(post_wait=900)
    plan = [ClickAction(selector=Selector(index=4))]

    augmented, notes = executor._augment_plan(plan)

    assert len(augmented) == 2
    assert isinstance(augmented[1], WaitAction)
    assert isinstance(augmented[1].for_, WaitForSelector)
    assert augmented[1].for_.selector.index == 4
    assert augmented[1].timeout_ms == min(
        executor.config.wait_timeout_ms, executor.config.post_interaction_wait_ms
    )
    assert notes and "Implicit wait" in notes[0]


def test_augment_plan_respects_existing_wait() -> None:
    executor = _make_executor()
    plan = [
        ClickAction(selector=Selector(index=0)),
        WaitAction(timeout_ms=250),
    ]

    augmented, notes = executor._augment_plan(plan)

    assert len(augmented) == len(plan)
    for original, augmented_action in zip(plan, augmented):
        assert augmented_action is original
    assert notes == []


def test_augment_plan_skips_non_index_click() -> None:
    executor = _make_executor()
    plan = [ClickAction(selector=Selector(css="#submit"))]

    augmented, notes = executor._augment_plan(plan)

    assert len(augmented) == 1
    assert isinstance(augmented[0], ClickAction)
    assert notes == []
