from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DOMRect:
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    @classmethod
    def from_json(cls, data: Dict | None) -> "DOMRect":
        if not isinstance(data, dict):
            return cls()
        try:
            return cls(
                x=int(data.get("x", 0)),
                y=int(data.get("y", 0)),
                width=int(data.get("width", 0)),
                height=int(data.get("height", 0)),
            )
        except Exception:
            return cls()


@dataclass
class DOMElementSummary:
    index: int
    tag: str
    text: str
    attributes: Dict[str, str] = field(default_factory=dict)
    rect: DOMRect = field(default_factory=DOMRect)
    ancestors: List[str] = field(default_factory=list)
    is_interactive: bool = False
    is_scrollable: bool = False

    @classmethod
    def from_json(cls, data: Dict) -> "DOMElementSummary":
        attrs_raw = data.get("attributes")
        attrs: Dict[str, str] = {}
        if isinstance(attrs_raw, dict):
            for key, value in attrs_raw.items():
                if key == "data" and isinstance(value, dict):
                    for dkey, dval in value.items():
                        if dval is not None:
                            attrs[f"data-{dkey}"] = str(dval)
                elif value is not None:
                    attrs[str(key)] = str(value)
        ancestors = [str(a) for a in data.get("ancestors", []) if a]
        return cls(
            index=int(data.get("index", 0)),
            tag=str(data.get("tag", "")),
            text=str(data.get("text", "")),
            attributes=attrs,
            rect=DOMRect.from_json(data.get("rect")),
            ancestors=ancestors,
            is_interactive=bool(data.get("isInteractive", False)),
            is_scrollable=bool(data.get("isScrollable", False)),
        )

    def short_attributes(self) -> str:
        keys = [
            "id",
            "name",
            "role",
            "type",
            "value",
            "placeholder",
            "aria-label",
            "href",
            "title",
        ]
        parts = [f"{k}={self.attributes[k]}" for k in keys if self.attributes.get(k)]
        if not parts:
            data_keys = [k for k in self.attributes if k.startswith("data-")]
            parts.extend(f"{k}={self.attributes[k]}" for k in data_keys[:2])
        return ", ".join(parts)

    def to_prompt_line(self) -> str:
        info = self.short_attributes()
        label = self.text.strip()
        if len(label) > 80:
            label = f"{label[:80]}…"
        base = f"[{self.index:03d}] <{self.tag}>{' ' + label if label else ''}"
        if info:
            base += f" | {info}"
        if self.is_scrollable:
            base += " | scrollable"
        if self.ancestors:
            base += " | parents: " + " > ".join(self.ancestors[:3])
        base += (
            f" | ({self.rect.x},{self.rect.y}) {self.rect.width}x{self.rect.height}"
        )
        return base


@dataclass
class DOMSnapshot:
    title: str = ""
    url: str = ""
    elements: List[DOMElementSummary] = field(default_factory=list)
    summary_lines: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @classmethod
    def from_json(cls, data: Dict) -> "DOMSnapshot":
        elements_data = data.get("elements") or []
        elements = [
            DOMElementSummary.from_json(el)
            for el in elements_data
            if isinstance(el, dict)
        ]
        summary_raw = data.get("summary")
        if isinstance(summary_raw, str):
            summary_lines = [line for line in summary_raw.splitlines() if line.strip()]
        elif isinstance(summary_raw, list):
            summary_lines = [str(line) for line in summary_raw]
        else:
            summary_lines = []
        return cls(
            title=str(data.get("title", "")),
            url=str(data.get("url", "")),
            elements=elements,
            summary_lines=summary_lines,
            error=str(data.get("error")) if data.get("error") else None,
        )

    def to_text(self, limit: int | None = None) -> str:
        header = []
        if self.url:
            header.append(f"URL: {self.url}")
        if self.title:
            header.append(f"Title: {self.title}")
        if self.error:
            header.append(f"Warning: {self.error}")

        body_lines: List[str]
        if self.summary_lines:
            body_lines = self.summary_lines[: limit or len(self.summary_lines)]
        else:
            elems = self.elements[: limit or len(self.elements)]
            body_lines = [el.to_prompt_line() for el in elems]
        return "\n".join(header + body_lines)
