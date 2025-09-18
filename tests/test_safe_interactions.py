import asyncio
from typing import Any, Dict, List, Optional

from vnc.safe_interactions import safe_click, safe_fill


class MockLocator:
    def __init__(
        self,
        identifier: str,
        *,
        record: List[tuple[str, str, Dict[str, Any]]],
        metadata: Optional[Dict[str, Any]] = None,
        visible: bool = True,
        enabled: bool = True,
        children: Optional[List["MockLocator"]] = None,
    ) -> None:
        self.identifier = identifier
        self._record = record
        self.metadata = metadata or {}
        self.visible = visible
        self.enabled = enabled
        self.children = children or []
        self.value = ""
        for child in self.children:
            child._record = record

    def _log(self, action: str, **details: Any) -> None:
        self._record.append((self.identifier, action, details))

    def nth(self, index: int) -> "MockLocator":
        self._log("nth", index=index)
        if self.children:
            if 0 <= index < len(self.children):
                return self.children[index]
            raise IndexError(f"nth index {index} out of range for {self.identifier}")
        if index == 0:
            return self
        raise IndexError(f"nth index {index} out of range for {self.identifier}")

    async def wait_for(self, *, state: Optional[str] = None, timeout: Optional[int] = None) -> None:
        self._log("wait_for", state=state, timeout=timeout)

    async def scroll_into_view_if_needed(self, *, timeout: Optional[int] = None) -> None:
        self._log("scroll_into_view_if_needed", timeout=timeout)

    async def is_enabled(self) -> bool:
        self._log("is_enabled")
        return self.enabled

    async def is_visible(self) -> bool:
        self._log("is_visible")
        return self.visible

    async def hover(self, **kwargs: Any) -> None:
        self._log("hover", **kwargs)

    async def click(self, **kwargs: Any) -> None:
        self._log("click", **kwargs)

    async def fill(self, value: str, **kwargs: Any) -> None:
        self._log("fill", value=value, **kwargs)
        self.value = value

    async def input_value(self) -> str:
        self._log("input_value")
        return self.value

    async def press(self, key: str, **kwargs: Any) -> None:
        self._log("press", key=key, **kwargs)

    async def type(self, text: str, **kwargs: Any) -> None:
        self._log("type", text=text, **kwargs)
        self.value = text

    async def evaluate(self, script: str, *args: Any) -> Any:
        self._log("evaluate", script=script, args=args)
        if args:
            # JavaScript fallback write.
            self.value = args[0]
            return None
        if "tagName" in script or "contenteditable" in script:
            return self.metadata
        return None


class DummyPage:
    pass


def _editable_metadata() -> Dict[str, Any]:
    return {
        "tag": "input",
        "type": "text",
        "role": "textbox",
        "disabled": False,
        "readOnly": False,
        "contentEditable": False,
    }


def test_safe_click_uses_nth_locator() -> None:
    record: List[tuple[str, str, Dict[str, Any]]] = []
    children = [
        MockLocator(f"button-{idx}", record=record)
        for idx in range(3)
    ]
    root = MockLocator("button-root", record=record, children=children)

    asyncio.run(safe_click(DummyPage(), root.nth(2)))

    click_targets = [name for name, action, _ in record if action == "click"]
    hover_targets = [name for name, action, _ in record if action == "hover"]
    wait_targets = {name for name, action, _ in record if action == "wait_for"}

    assert click_targets == ["button-2"]
    assert hover_targets == ["button-2"]
    assert wait_targets == {"button-2"}


def test_safe_fill_uses_nth_locator() -> None:
    record: List[tuple[str, str, Dict[str, Any]]] = []
    children = [
        MockLocator(f"input-{idx}", record=record, metadata=_editable_metadata())
        for idx in range(3)
    ]
    root = MockLocator("input-root", record=record, children=children)
    target = root.nth(1)

    asyncio.run(safe_fill(DummyPage(), target, "typed text"))

    fill_targets = [name for name, action, _ in record if action == "fill"]
    click_targets = [name for name, action, _ in record if action == "click"]
    wait_targets = {name for name, action, _ in record if action == "wait_for"}

    assert fill_targets == ["input-1", "input-1"]  # clear then fill with value
    assert click_targets == ["input-1"]
    assert wait_targets == {"input-1"}
    assert target.value == "typed text"
