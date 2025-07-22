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
