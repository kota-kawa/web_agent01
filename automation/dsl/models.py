"""Typed DSL models for automation actions."""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Literal, Optional, Sequence, Tuple, Union

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

SelectorPriority = Literal[
    "css",
    "xpath",
    "role",
    "text",
    "aria_label",
    "near_text",
    "index",
    "stable_id",
]


class Selector(BaseModel):
    """Composite selector description with optional legacy support."""

    model_config = ConfigDict(extra="forbid")

    css: Optional[str] = None
    xpath: Optional[str] = None
    text: Optional[str] = None
    role: Optional[str] = None
    index: Optional[int] = None
    near_text: Optional[str] = Field(default=None, alias="near_text")
    aria_label: Optional[str] = Field(default=None, alias="aria_label")
    priority: Optional[List[SelectorPriority]] = None
    stable_id: Optional[str] = Field(default=None, alias="stable_id")
    legacy_value: Optional[str] = Field(default=None, alias="__legacy_value__", exclude=True, repr=False)

    _DEFAULT_PRIORITY: ClassVar[Tuple[SelectorPriority, ...]] = (
        "stable_id",
        "css",
        "role",
        "text",
        "aria_label",
        "xpath",
        "near_text",
        "index",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_from_legacy(cls, value: Any) -> Any:
        if isinstance(value, Selector):
            return value
        if isinstance(value, str):
            return {"css": value, "__legacy_value__": value}
        if value is None:
            return value
        if isinstance(value, dict):
            # Support legacy "target" style dictionaries by copying known keys only.
            allowed_keys = {
                "css",
                "xpath",
                "text",
                "role",
                "index",
                "near_text",
                "aria_label",
                "priority",
                "stable_id",
            }
            return {k: v for k, v in value.items() if k in allowed_keys}
        return value

    @field_validator("index")
    @classmethod
    def _validate_index(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return value
        if value < 0:
            raise ValueError("index must be >= 0")
        return value

    @field_validator("priority")
    @classmethod
    def _validate_priority(
        cls, value: Optional[Sequence[SelectorPriority]]
    ) -> Optional[List[SelectorPriority]]:
        if value is None:
            return None
        if isinstance(value, str):
            value = [value]  # type: ignore[list-item]
        ordered: List[SelectorPriority] = []
        seen = set()
        for item in value:
            if item in seen:
                continue
            ordered.append(item)
            seen.add(item)
        return ordered

    def effective_priority(self) -> Tuple[SelectorPriority, ...]:
        if self.priority:
            return tuple(self.priority)
        return self._DEFAULT_PRIORITY

    def is_simple(self) -> bool:
        return (
            self.css is not None
            and self.legacy_value is not None
            and all(
                getattr(self, field) is None
                for field in ("xpath", "text", "role", "near_text", "aria_label", "priority", "stable_id")
            )
            and self.index is None
        )

    def as_legacy(self) -> Union[str, Dict[str, Any]]:
        if self.is_simple():
            return self.legacy_value or self.css or ""
        data = self.model_dump(by_alias=True, exclude_none=True)
        data.pop("__legacy_value__", None)
        return data


SelectorLike = Union[Selector, str]


class WaitForState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["load", "domcontentloaded", "networkidle"]


class WaitForSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selector: Selector = Field(alias="selector", validation_alias=AliasChoices("selector", "target"))
    state: Literal["attached", "detached", "visible", "hidden"] = "visible"


class WaitForTimeout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_ms: int = Field(default=1000, ge=0, alias="timeout_ms", validation_alias=AliasChoices("timeout_ms", "ms"))


WaitCondition = Union[WaitForState, WaitForSelector, WaitForTimeout]


class ScrollTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selector: Optional[Selector] = Field(default=None, alias="selector", validation_alias=AliasChoices("selector", "target"))
    container: Optional[Selector] = None
    axis: Literal["vertical", "horizontal", "both"] = "vertical"
    align: Literal["start", "center", "end", "nearest"] = "center"
    behavior: Literal["auto", "instant", "smooth"] = "smooth"


class TabTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["index", "url", "title", "previous", "next", "latest"] = "index"
    value: Optional[Union[int, str]] = None

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: Optional[Union[int, str]], info) -> Optional[Union[int, str]]:
        if info.data.get("strategy") == "index" and value is None:
            return 0
        return value


class FrameTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["index", "name", "url", "element", "parent", "root"] = "index"
    value: Optional[Union[int, str, Selector]] = None


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    details: Dict[str, Any] = Field(default_factory=dict)


class ActionBase(BaseModel):
    """Base class for all DSL actions."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", arbitrary_types_allowed=True)

    __action_name__: ClassVar[str]
    __version__: ClassVar[int] = 1
    __deprecated__: ClassVar[bool] = False

    def payload(self, *, by_alias: bool = True) -> Dict[str, Any]:
        data = self.model_dump(by_alias=by_alias, exclude_none=True)
        if by_alias:
            data.setdefault("type", self.__action_name__)
        else:
            data.setdefault("type", self.__action_name__)
        return data

    def legacy_payload(self) -> Dict[str, Any]:
        data = self.payload(by_alias=True)
        action_name = data.pop("type", self.__action_name__)
        data["action"] = action_name

        if hasattr(self, "selector"):
            selector_obj = getattr(self, "selector")
            if isinstance(selector_obj, Selector):
                data.pop("selector", None)
                data["target"] = selector_obj.as_legacy()
        # Action specific legacy adjustments
        if action_name == "navigate" and "url" in data and "target" not in data:
            data["target"] = data["url"]
        elif action_name == "type":
            value = data.pop("text", "")
            data["value"] = value
        elif action_name == "select":
            value = data.pop("value_or_label", "")
            data["value"] = value
        elif action_name == "press_key":
            keys = data.pop("keys", [])
            if keys:
                if len(keys) == 1:
                    data["key"] = keys[0]
                else:
                    data["key"] = "+".join(keys)
        elif action_name == "wait":
            timeout = data.pop("timeout_ms", None)
            if timeout is not None:
                data["ms"] = timeout
            condition = getattr(self, "for_", None)
            if condition is None:
                data.pop("for", None)
            else:
                data.pop("for", None)
                if isinstance(condition, WaitForState):
                    data["until"] = condition.state
                elif isinstance(condition, WaitForSelector):
                    data["until"] = "selector"
                    data["target"] = condition.selector.as_legacy()
                    data["state"] = condition.state
                elif isinstance(condition, WaitForTimeout):
                    data["ms"] = condition.timeout_ms
        elif action_name == "scroll":
            if "to" in data:
                to_value = data.pop("to")
                if isinstance(to_value, int):
                    data["amount"] = to_value
                else:
                    data["target"] = to_value
        return data

    @property
    def action_name(self) -> str:
        return self.__action_name__


class NavigateAction(ActionBase):
    __action_name__ = "navigate"

    type: Literal["navigate"] = Field(
        default="navigate",
        alias="type",
        validation_alias=AliasChoices("type", "action"),
    )
    url: str = Field(alias="url", validation_alias=AliasChoices("url", "target"))
    wait_for: Optional[WaitCondition] = Field(default=None, alias="wait_for")


class ClickAction(ActionBase):
    __action_name__ = "click"

    type: Literal["click"] = Field(
        default="click",
        alias="type",
        validation_alias=AliasChoices("type", "action"),
    )
    selector: Selector = Field(alias="selector", validation_alias=AliasChoices("selector", "target"))
    button: Literal["left", "right", "middle"] = "left"
    click_count: int = Field(default=1, ge=1, alias="click_count")
    delay_ms: Optional[int] = Field(default=None, ge=0, alias="delay_ms")


class TypeAction(ActionBase):
    __action_name__ = "type"

    type: Literal["type"] = Field(
        default="type",
        alias="type",
        validation_alias=AliasChoices("type", "action"),
    )
    selector: Selector = Field(alias="selector", validation_alias=AliasChoices("selector", "target"))
    text: str = Field(alias="text", validation_alias=AliasChoices("text", "value"))
    press_enter: bool = Field(default=False, alias="press_enter")
    clear: bool = Field(default=False, alias="clear")


class SelectAction(ActionBase):
    __action_name__ = "select"

    type: Literal["select"] = Field(
        default="select",
        alias="type",
        validation_alias=AliasChoices("type", "action"),
    )
    selector: Selector = Field(alias="selector", validation_alias=AliasChoices("selector", "target"))
    value_or_label: str = Field(alias="value_or_label", validation_alias=AliasChoices("value_or_label", "value"))


class PressKeyAction(ActionBase):
    __action_name__ = "press_key"

    type: Literal["press_key"] = Field(
        default="press_key",
        alias="type",
        validation_alias=AliasChoices("type", "action"),
    )
    keys: List[str] = Field(alias="keys", validation_alias=AliasChoices("keys", "key", "hotkeys"))
    scope: Literal["active_element", "page"] = Field(
        default="active_element",
        alias="scope",
        validation_alias=AliasChoices("scope", "target_scope"),
    )

    @field_validator("keys")
    @classmethod
    def _ensure_non_empty(cls, value: Sequence[str]) -> List[str]:
        if not value:
            raise ValueError("keys must contain at least one key")
        return [str(v) for v in value]


class WaitAction(ActionBase):
    __action_name__ = "wait"

    type: Literal["wait"] = Field(
        default="wait",
        alias="type",
        validation_alias=AliasChoices("type", "action"),
    )
    for_: Optional[WaitCondition] = Field(
        default=None,
        alias="for",
        validation_alias=AliasChoices("for", "condition", "until"),
    )
    timeout_ms: int = Field(
        default=10000,
        ge=0,
        alias="timeout_ms",
        validation_alias=AliasChoices("timeout_ms", "ms"),
    )


class ScrollAction(ActionBase):
    __action_name__ = "scroll"

    type: Literal["scroll"] = Field(
        default="scroll",
        alias="type",
        validation_alias=AliasChoices("type", "action"),
    )
    to: Optional[Union[ScrollTarget, Literal["top", "bottom"], int]] = Field(
        default=None,
        alias="to",
        validation_alias=AliasChoices("to", "amount"),
    )
    direction: Optional[Literal["up", "down"]] = Field(default=None, alias="direction")
    container: Optional[Selector] = Field(default=None, alias="container")


class SwitchTabAction(ActionBase):
    __action_name__ = "switch_tab"

    type: Literal["switch_tab"] = Field(
        default="switch_tab",
        alias="type",
        validation_alias=AliasChoices("type", "action"),
    )
    target: TabTarget = Field(alias="target", validation_alias=AliasChoices("target", "tab"))


class FocusIframeAction(ActionBase):
    __action_name__ = "focus_iframe"

    type: Literal["focus_iframe"] = Field(
        default="focus_iframe",
        alias="type",
        validation_alias=AliasChoices("type", "action"),
    )
    target: FrameTarget = Field(alias="target", validation_alias=AliasChoices("target", "frame"))


class ScreenshotAction(ActionBase):
    __action_name__ = "screenshot"

    type: Literal["screenshot"] = Field(
        default="screenshot",
        alias="type",
        validation_alias=AliasChoices("type", "action"),
    )
    mode: Literal["viewport", "full", "element"] = Field(default="viewport", alias="mode")
    selector: Optional[Selector] = Field(default=None, alias="selector")
    file_name: Optional[str] = Field(default=None, alias="file_name")


class ExtractAction(ActionBase):
    __action_name__ = "extract"

    type: Literal["extract"] = Field(
        default="extract",
        alias="type",
        validation_alias=AliasChoices("type", "action"),
    )
    selector: Selector = Field(alias="selector", validation_alias=AliasChoices("selector", "target"))
    attr: Literal["text", "value", "href", "html"] = Field(default="text", alias="attr")


class AssertAction(ActionBase):
    __action_name__ = "assert"

    type: Literal["assert"] = Field(
        default="assert",
        alias="type",
        validation_alias=AliasChoices("type", "action"),
    )
    selector: Selector = Field(alias="selector", validation_alias=AliasChoices("selector", "target"))
    state: Literal["visible", "hidden", "attached", "detached"] = Field(default="visible", alias="state")


ActionTypes = Union[
    NavigateAction,
    ClickAction,
    TypeAction,
    SelectAction,
    PressKeyAction,
    WaitAction,
    ScrollAction,
    SwitchTabAction,
    FocusIframeAction,
    ScreenshotAction,
    ExtractAction,
    AssertAction,
]
