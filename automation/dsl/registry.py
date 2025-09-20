"""Typed action registry built on top of pydantic models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Type, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .models import (
    ActionBase,
    ClickAction,
    HoverAction,
    ExtractAction,
    EvalJsAction,
    FocusIframeAction,
    NavigateAction,
    RefreshCatalogAction,
    PressKeyAction,
    ScrollAction,
    ScrollToTextAction,
    ScreenshotAction,
    SelectAction,
    SwitchTabAction,
    SearchAction,
    SubmitFormAction,
    ClickBlankAreaAction,
    ClosePopupAction,
    StopAction,
    TypeAction,
    WaitAction,
    AssertAction,
)


@dataclass(slots=True)
class ActionSpec:
    name: str
    model: Type[ActionBase]
    version: int = 1
    deprecated: bool = False
    description: str | None = None

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "deprecated": self.deprecated,
            "description": self.description or "",
        }


A = TypeVar("A", bound=ActionBase)


class ActionRegistry:
    """Central registry holding strongly typed action definitions."""

    def __init__(self) -> None:
        self._actions: Dict[str, ActionSpec] = {}
        self._adapter: Optional[TypeAdapter[Any]] = None

    def register(
        self,
        model: Type[A],
        *,
        name: Optional[str] = None,
        version: int = 1,
        deprecated: bool = False,
        description: str | None = None,
    ) -> Type[A]:
        if not issubclass(model, ActionBase):
            raise TypeError("model must subclass ActionBase")
        action_name = name or getattr(model, "__action_name__", None) or model.__name__
        model.__action_name__ = action_name
        model.__version__ = version
        model.__deprecated__ = deprecated
        spec = ActionSpec(
            name=action_name,
            model=model,
            version=version,
            deprecated=deprecated,
            description=description,
        )
        self._actions[action_name] = spec
        self._adapter = None
        return model

    def get(self, name: str) -> ActionSpec:
        try:
            return self._actions[name]
        except KeyError as exc:
            raise KeyError(f"Unknown action '{name}'") from exc

    def __contains__(self, name: str) -> bool:  # pragma: no cover - trivial
        return name in self._actions

    def __iter__(self) -> Iterator[ActionSpec]:  # pragma: no cover - trivial
        return iter(self._actions.values())

    def _ensure_adapter(self) -> TypeAdapter[Any]:
        if self._adapter is None:
            if not self._actions:
                raise RuntimeError("No actions registered")
            action_types = tuple(spec.model for spec in self._actions.values())
            union = action_types[0]
            for model in action_types[1:]:
                union = union | model  # type: ignore[operator]
            self._adapter = TypeAdapter(union)
        return self._adapter

    def parse_action(self, data: Any) -> ActionBase:
        adapter = self._ensure_adapter()
        return adapter.validate_python(data)

    def parse_json(self, data: str) -> ActionBase:
        adapter = self._ensure_adapter()
        return adapter.validate_json(data)

    def schema(self) -> Dict[str, Any]:
        return {name: spec.to_metadata() for name, spec in self._actions.items()}


registry = ActionRegistry()

# Register builtin actions with default version metadata.
registry.register(NavigateAction, version=1)
registry.register(ClickAction, version=1)
registry.register(HoverAction, version=1)
registry.register(TypeAction, version=1)
registry.register(SearchAction, version=1)
registry.register(SelectAction, version=1)
registry.register(PressKeyAction, version=1)
registry.register(WaitAction, version=1)
registry.register(SubmitFormAction, version=1)
registry.register(ScrollAction, version=1)
registry.register(ScrollToTextAction, version=1)
registry.register(SwitchTabAction, version=1)
registry.register(FocusIframeAction, version=1)
registry.register(RefreshCatalogAction, version=1)
registry.register(EvalJsAction, version=1)
registry.register(ClickBlankAreaAction, version=1)
registry.register(ClosePopupAction, version=1)
registry.register(StopAction, version=1)
registry.register(ScreenshotAction, version=1)
registry.register(ExtractAction, version=1)
registry.register(AssertAction, version=1)


class RunPlan(BaseModel):
    """Batch of actions validated via the registry."""

    model_config = ConfigDict(extra="forbid")

    actions: List[ActionBase] = Field(default_factory=list, alias="actions")

    @model_validator(mode="before")
    @classmethod
    def _coerce_actions(cls, value: Any) -> Any:
        if isinstance(value, list):
            return {"actions": value}
        if isinstance(value, dict) and "actions" in value:
            actions = value["actions"]
            parsed = []
            for act in actions:
                if isinstance(act, ActionBase):
                    parsed.append(act)
                else:
                    parsed.append(registry.parse_action(act))
            new_value = dict(value)
            new_value["actions"] = parsed
            return new_value
        return value


class RunRequest(BaseModel):
    """Top level DSL payload."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(alias="run_id")
    plan: RunPlan = Field(alias="plan")
    config: Dict[str, Any] = Field(default_factory=dict, alias="config")
    metadata: Dict[str, Any] = Field(default_factory=dict, alias="metadata")

    @model_validator(mode="before")
    @classmethod
    def _coerce_plan(cls, value: Any) -> Any:
        if isinstance(value, dict) and "plan" in value:
            plan = value["plan"]
            if isinstance(plan, list):
                value = dict(value)
                value["plan"] = {"actions": plan}
        return value

    def to_payload(self) -> Dict[str, Any]:
        data = self.model_dump(by_alias=True)
        data["plan"] = [action.payload() for action in self.plan.actions]
        return data

    def to_legacy_payload(self) -> Dict[str, Any]:
        data = self.model_dump(by_alias=True)
        data["plan"] = [action.legacy_payload() for action in self.plan.actions]
        return data
