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
    is_new: bool = False

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
            is_new=bool(data.get("isNew", False)),
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
        indent = "\t" * min(len(self.ancestors), 3)
        prefix = "*" if self.is_new else ""
        base = f"{indent}{prefix}[{self.index:03d}] <{self.tag}>{' ' + label if label else ''}"
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
class BrowserTab:
    id: str
    url: str
    title: str = ""
    is_active: bool = False

    @classmethod
    def from_json(cls, data: Dict) -> "BrowserTab":
        return cls(
            id=str(data.get("id", "")),
            url=str(data.get("url", "")),
            title=str(data.get("title", "")),
            is_active=bool(data.get("isActive", False)),
        )


@dataclass
class DOMSnapshot:
    title: str = ""
    url: str = ""
    elements: List[DOMElementSummary] = field(default_factory=list)
    summary_lines: List[str] = field(default_factory=list)
    error: Optional[str] = None
    tabs: List[BrowserTab] = field(default_factory=list)
    new_element_count: int = 0

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
        tabs_data = data.get("tabs") or []
        tabs = [BrowserTab.from_json(t) for t in tabs_data if isinstance(t, dict)]
        return cls(
            title=str(data.get("title", "")),
            url=str(data.get("url", "")),
            elements=elements,
            summary_lines=summary_lines,
            error=str(data.get("error")) if data.get("error") else None,
            tabs=tabs,
            new_element_count=int(data.get("newElements", 0) or 0),
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
            tab_lines: List[str] = []
            if self.tabs:
                tab_lines.append("Open Tabs:")
                for tab in self.tabs:
                    mark = "*" if tab.is_active else "-"
                    title = tab.title or "(no title)"
                    tab_lines.append(f"  {mark} {tab.id}: {title} | {tab.url}")
                tab_lines.append("")
            elems = self.elements[: limit or len(self.elements)]
            body_lines = tab_lines + [el.to_prompt_line() for el in elems]
        if self.new_element_count and "summary" not in header:
            header.append(f"New interactive elements: {self.new_element_count}")
        return "\n".join(header + body_lines)
