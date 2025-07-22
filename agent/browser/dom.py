from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DOMElementNode:
    tagName: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    text: Optional[str] = None
    xpath: str = ""
    isVisible: bool = False
    isInteractive: bool = False
    isTopElement: bool = False
    highlightIndex: Optional[int] = None
    children: List["DOMElementNode"] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict) -> "DOMElementNode":
        if data is None:
            return None
        if data.get("nodeType") == "text":
            return cls(tagName="#text", text=data.get("text"))
        children = [cls.from_json(c) for c in data.get("children", []) if c]
        return cls(
            tagName=data.get("tagName", ""),
            attributes=data.get("attributes", {}),
            text=data.get("text"),
            xpath=data.get("xpath", ""),
            isVisible=data.get("isVisible", False),
            isInteractive=data.get("isInteractive", False),
            isTopElement=data.get("isTopElement", False),
            highlightIndex=data.get("highlightIndex"),
            children=children,
        )

    def to_lines(self, depth: int = 0, max_lines: int = 200, _lines=None) -> List[str]:
        """Return indented text representation of the DOM tree."""
        if _lines is None:
            _lines = []
        if len(_lines) >= max_lines:
            return _lines
        indent = "  " * depth
        if self.tagName == "#text":
            if self.text and self.text.strip():
                _lines.append(f"{indent}{self.text.strip()}")
            return _lines
        attr = " ".join(f"{k}={v}" for k, v in self.attributes.items() if v)
        idx = f" [{self.highlightIndex}]" if self.highlightIndex is not None else ""
        line = f"{indent}<{self.tagName}{(' ' + attr) if attr else ''}>{idx}"
        _lines.append(line)
        for ch in self.children:
            if len(_lines) >= max_lines:
                break
            ch.to_lines(depth + 1, max_lines, _lines)
        return _lines

    def to_text(self, max_lines: int = 200) -> str:
        return "\n".join(self.to_lines(max_lines=max_lines))
