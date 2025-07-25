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

    @classmethod
    def from_html(cls, html: str) -> "DOMElementNode":
        """Parse raw HTML into a simplified DOMElementNode tree."""
        from bs4 import BeautifulSoup, NavigableString, Tag

        soup = BeautifulSoup(html, "html.parser")
        # Remove <script> and <style> tags entirely to keep the DOM concise
        for t in soup.find_all(["script", "style"]):
            t.decompose()

        counter = 1
        interactive_tags = {
            "a",
            "button",
            "input",
            "select",
            "textarea",
            "option",
        }

        def get_xpath(el: Tag) -> str:
            parts = []
            while el and isinstance(el, Tag):
                idx = 1
                sib = el.previous_sibling
                while sib:
                    if isinstance(sib, Tag) and sib.name == el.name:
                        idx += 1
                    sib = sib.previous_sibling
                parts.append(f"{el.name}[{idx}]")
                el = el.parent
            return "/" + "/".join(reversed(parts))

        def traverse(node) -> Optional[DOMElementNode]:
            nonlocal counter
            if isinstance(node, NavigableString):
                text = str(node).strip()
                if not text:
                    return None
                return cls(tagName="#text", text=text)
            if not isinstance(node, Tag):
                return None
            if node.name in {"script", "style"}:
                return None

            children = [c for c in (traverse(ch) for ch in node.children) if c]
            attrs = {
                k: (" ".join(v) if isinstance(v, list) else str(v))
                for k, v in node.attrs.items()
            }

            xpath = get_xpath(node)
            interactive = node.name in interactive_tags
            hidx = counter if interactive else None
            if interactive:
                counter += 1

            return cls(
                tagName=node.name,
                attributes=attrs,
                xpath=xpath,
                isVisible=True,
                isInteractive=interactive,
                isTopElement=interactive,
                highlightIndex=hidx,
                children=children,
            )

        root = soup.body or soup
        return traverse(root)

    def to_lines(
        self, depth: int = 0, max_lines: int | None = 200, _lines=None
    ) -> List[str]:
        """Return indented text representation of the DOM tree."""
        if _lines is None:
            _lines = []
        if max_lines is not None and len(_lines) >= max_lines:
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
            if max_lines is not None and len(_lines) >= max_lines:
                break
            ch.to_lines(depth + 1, max_lines, _lines)
        return _lines

    def to_text(self, max_lines: int | None = 200) -> str:
        return "\n".join(self.to_lines(max_lines=max_lines))
