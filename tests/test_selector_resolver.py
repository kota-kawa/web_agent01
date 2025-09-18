import asyncio

from automation.dsl import Selector
from automation.dsl.resolution import ResolutionAttempt
from vnc.selector_resolver import SelectorResolver


class FakeCandidateLocator:
    def __init__(self, index: int) -> None:
        self.index = index

    async def element_handle(self):
        return object()


class FakeLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    async def count(self) -> int:
        return self._count

    def nth(self, index: int) -> FakeCandidateLocator:
        return FakeCandidateLocator(index)


class FakePage:
    def __init__(self, locator: FakeLocator) -> None:
        self._locator = locator

    def get_by_text(self, text: str, exact: bool = False) -> FakeLocator:
        return self._locator


def test_resolver_prefers_candidate_matching_high_index(monkeypatch):
    locator = FakeLocator(count=10)
    page = FakePage(locator)
    resolver = SelectorResolver(page)
    selector = Selector(text="Target", index=8, priority=["text"])

    async def fake_build_attempt(
        self,
        locator,
        element,
        selector,
        *,
        strategy,
        ordinal=None,
        ref_metrics=None,
    ) -> ResolutionAttempt:
        ord_value = ordinal if ordinal is not None else -1
        return ResolutionAttempt(
            selector=selector,
            locator=locator,
            element=element,
            dom_path=f"path-{ord_value}",
            text_digest=f"text-{ord_value}",
            strategy=strategy,
            score=1.0 if ord_value == selector.index else 0.0,
            metadata={"ordinal": ord_value},
        )

    monkeypatch.setattr(SelectorResolver, "_build_attempt", fake_build_attempt)

    resolved = asyncio.run(resolver.resolve(selector))

    assert resolved.metadata["ordinal"] == selector.index


def test_collect_from_locator_includes_index_outside_limit(monkeypatch):
    locator = FakeLocator(count=10)
    resolver = SelectorResolver(page=None)  # page is unused in this test
    selector = Selector(css="#button", index=7)

    async def fake_build_attempt(
        self,
        locator,
        element,
        selector,
        *,
        strategy,
        ordinal=None,
        ref_metrics=None,
    ) -> ResolutionAttempt:
        ord_value = ordinal if ordinal is not None else -1
        return ResolutionAttempt(
            selector=selector,
            locator=locator,
            element=element,
            dom_path=f"/div[{ord_value}]",
            text_digest=f"candidate-{ord_value}",
            strategy=strategy,
            score=float(ord_value),
            metadata={"ordinal": ord_value},
        )

    monkeypatch.setattr(SelectorResolver, "_build_attempt", fake_build_attempt)

    attempts = asyncio.run(resolver._collect_from_locator(locator, selector, "css"))

    ordinals = sorted(attempt.metadata["ordinal"] for attempt in attempts)
    assert selector.index in ordinals
    assert ordinals.count(selector.index) == 1
