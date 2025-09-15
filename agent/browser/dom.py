from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

DOM_SNAPSHOT_SCRIPT = """
(() => {
  function computeXPath(el) {
    if (el === document.body) return '/html/body';
    let xpath = '';
    while (el && el.nodeType === Node.ELEMENT_NODE) {
      let index = 1;
      let sibling = el.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === el.tagName) index++;
        sibling = sibling.previousElementSibling;
      }
      xpath = '/' + el.tagName.toLowerCase() + '[' + index + ']' + xpath;
      el = el.parentElement;
    }
    return xpath;
  }

  function isVisible(el) {
    if (!(el instanceof Element)) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function isInteractive(el) {
    const interactiveTags = ['a','button','input','select','textarea','option','summary'];
    const interactiveRoles = ['button','link','textbox','checkbox','radio','menuitem','tab','switch','combobox'];
    if (interactiveTags.includes(el.tagName.toLowerCase())) return true;
    const role = el.getAttribute('role');
    if (role && interactiveRoles.includes(role)) return true;
    if (el.tabIndex >= 0) return true;
    if (el.isContentEditable) return true;
    return false;
  }

  let counter = 1;
  function serialize(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent.trim();
      if (!text) return null;
      return {nodeType: 'text', text};
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    const visible = isVisible(node);
    const interactive = visible && isInteractive(node);
    const attrs = {};
    for (const attr of Array.from(node.attributes)) {
      attrs[attr.name] = attr.value;
    }
    const children = Array.from(node.childNodes).map(serialize).filter(Boolean);
    const result = {
      tagName: node.tagName.toLowerCase(),
      attributes: attrs,
      xpath: computeXPath(node),
      isVisible: visible,
      isInteractive: interactive,
      isTopElement: interactive,
      highlightIndex: interactive ? counter++ : undefined,
      children,
    };
    return result;
  }

  return serialize(document.body);
})()
"""


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
    def from_page(cls, page) -> "DOMElementNode":
        """Retrieve DOM information directly from a Playwright page.

        The DOM tree along with visibility and interactivity flags is computed
        inside the browser to avoid Python-side heuristics.
        """
        dom_dict = page.evaluate(DOM_SNAPSHOT_SCRIPT)
        return cls.from_json(dom_dict)

    # Backwards compatible alias
    from_html = from_page

    def to_lines(
        self, depth: int = 0, max_lines: int | None = None, _lines=None
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

    def to_text(self, max_lines: int | None = None) -> str:
        return "\n".join(self.to_lines(max_lines=max_lines))
