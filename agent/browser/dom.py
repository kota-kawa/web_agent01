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

  // Exclude unnecessary tags that don't contribute to visual layout
  const EXCLUDED_TAGS = new Set(['script', 'style', 'head', 'meta', 'link', 'title', 'noscript']);

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

  function isScrollable(el) {
    const style = window.getComputedStyle(el);
    return style.overflow === 'auto' || style.overflow === 'scroll' || 
           style.overflowX === 'auto' || style.overflowX === 'scroll' ||
           style.overflowY === 'auto' || style.overflowY === 'scroll';
  }

  function isCoveredByOtherElement(el) {
    const rect = el.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    
    const topElement = document.elementFromPoint(centerX, centerY);
    if (!topElement) return false;
    
    // Check if the element is completely covered
    return !el.contains(topElement) && !topElement.contains(el);
  }

  function shouldExcludeByParent(el, parent) {
    if (!parent) return false;
    
    // Don't exclude independently interactive elements
    if (isInteractive(el)) return false;
    
    // Check if parent is a bounds propagation element
    const boundsElements = ['button', 'a', 'label'];
    if (!boundsElements.includes(parent.tagName.toLowerCase())) return false;
    
    const parentRect = parent.getBoundingClientRect();
    const elRect = el.getBoundingClientRect();
    
    // Check if element is almost entirely contained within parent
    const overlapThreshold = 0.8;
    const overlapWidth = Math.min(parentRect.right, elRect.right) - Math.max(parentRect.left, elRect.left);
    const overlapHeight = Math.min(parentRect.bottom, elRect.bottom) - Math.max(parentRect.top, elRect.top);
    
    if (overlapWidth <= 0 || overlapHeight <= 0) return false;
    
    const overlapArea = overlapWidth * overlapHeight;
    const elArea = elRect.width * elRect.height;
    
    return elArea > 0 && (overlapArea / elArea) >= overlapThreshold;
  }

  function getMeaningfulText(text) {
    if (!text) return '';
    const trimmed = text.trim();
    // Exclude meaningless text of 1-2 characters that are just whitespace or symbols
    if (trimmed.length <= 2 && /^[\s\n\r\t\u00A0]+$/.test(trimmed)) return '';
    return trimmed;
  }

  function getRelevantAttributes(el) {
    const relevantAttrs = ['title', 'type', 'name', 'role', 'value', 'placeholder', 'alt'];
    const attrs = {};
    
    for (const attr of relevantAttrs) {
      const value = el.getAttribute(attr);
      if (value) {
        // Truncate long attribute values
        attrs[attr] = value.length > 100 ? value.substring(0, 100) + '...' : value;
      }
    }
    
    // Include aria attributes
    for (const attr of Array.from(el.attributes)) {
      if (attr.name.startsWith('aria-') && attr.value) {
        const truncated = attr.value.length > 100 ? attr.value.substring(0, 100) + '...' : attr.value;
        attrs[attr.name] = truncated;
      }
    }
    
    return attrs;
  }

  let counter = 1;
  const previousElements = new Set(); // Track elements from previous DOM snapshots
  
  function serialize(node, parent = null) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = getMeaningfulText(node.textContent);
      if (!text) return null;
      return {nodeType: 'text', text};
    }
    
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    
    const tagName = node.tagName.toLowerCase();
    
    // Exclude unnecessary tags early
    if (EXCLUDED_TAGS.has(tagName)) return null;
    
    const visible = isVisible(node);
    
    // Exclude invisible nodes that have no visible children
    if (!visible) {
      const hasVisibleChildren = Array.from(node.childNodes).some(child => {
        if (child.nodeType === Node.TEXT_NODE) {
          return getMeaningfulText(child.textContent);
        }
        if (child.nodeType === Node.ELEMENT_NODE) {
          return isVisible(child);
        }
        return false;
      });
      if (!hasVisibleChildren) return null;
    }
    
    const interactive = visible && isInteractive(node);
    
    // Check if element should be excluded by parent bounds propagation
    const excludedByParent = shouldExcludeByParent(node, parent);
    
    // Check if element is covered by another element (paint order filtering)
    const coveredByOther = visible && isCoveredByOtherElement(node);
    
    if (excludedByParent || coveredByOther) {
      // Still process children in case they're not excluded
      const children = Array.from(node.childNodes)
        .map(child => serialize(child, node))
        .filter(Boolean);
      return children.length > 0 ? {nodeType: 'children', children} : null;
    }
    
    const attrs = getRelevantAttributes(node);
    
    // Generate highlight index for interactive elements
    let highlightIndex = null;
    let isNewElement = false;
    if (interactive) {
      highlightIndex = counter++;
      // Check if this is a newly appeared element (simplified for now)
      isNewElement = !previousElements.has(node.outerHTML);
      previousElements.add(node.outerHTML);
    }
    
    // Add visual annotations
    let visualAnnotations = [];
    if (isScrollable(node)) {
      visualAnnotations.push('|SCROLL|');
    }
    if (tagName === 'iframe') {
      visualAnnotations.push('|IFRAME|');
    }
    
    const children = Array.from(node.childNodes)
      .map(child => serialize(child, node))
      .filter(Boolean);
    
    const result = {
      tagName,
      attributes: attrs,
      xpath: computeXPath(node),
      isVisible: visible,
      isInteractive: interactive,
      isTopElement: interactive,
      highlightIndex,
      isNewElement,
      visualAnnotations,
      children,
    };
    
    return result;
  }

  // Get viewport information for off-screen content detection
  const viewportInfo = {
    scrollTop: window.pageYOffset || document.documentElement.scrollTop,
    scrollHeight: document.documentElement.scrollHeight,
    viewportHeight: window.innerHeight
  };

  const domTree = serialize(document.body);
  
  return {
    domTree,
    viewportInfo
  };
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
    isNewElement: bool = False
    visualAnnotations: List[str] = field(default_factory=list)
    children: List["DOMElementNode"] = field(default_factory=list)
    viewportInfo: Optional[Dict] = None

    @classmethod
    def from_json(cls, data: dict) -> "DOMElementNode":
        if data is None:
            return None
        if data.get("nodeType") == "text":
            return cls(tagName="#text", text=data.get("text"))
        if data.get("nodeType") == "children":
            # Handle flattened children from excluded elements
            children = [cls.from_json(c) for c in data.get("children", []) if c]
            return cls(tagName="#children", children=children)
        
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
            isNewElement=data.get("isNewElement", False),
            visualAnnotations=data.get("visualAnnotations", []),
            children=children,
        )

    @classmethod
    def from_page(cls, page) -> "DOMElementNode":
        """Retrieve DOM information directly from a Playwright page.

        The DOM tree along with visibility and interactivity flags is computed
        inside the browser to avoid Python-side heuristics.
        """
        result = page.evaluate(DOM_SNAPSHOT_SCRIPT)
        if isinstance(result, dict) and "domTree" in result:
            # New format with viewport info
            dom_tree = cls.from_json(result["domTree"])
            if dom_tree:
                dom_tree.viewportInfo = result.get("viewportInfo")
            return dom_tree
        else:
            # Backward compatibility with old format
            return cls.from_json(result)

    # Backwards compatible alias
    from_html = from_page

    def to_structured_text(self, max_lines: int | None = None) -> str:
        """Return structured text representation optimized for LLM consumption."""
        lines = []
        self._build_structured_lines(lines, max_lines)
        
        # Add viewport information if available
        if self.viewportInfo and lines:
            viewport = self.viewportInfo
            scroll_top = viewport.get('scrollTop', 0)
            scroll_height = viewport.get('scrollHeight', 0)
            viewport_height = viewport.get('viewportHeight', 0)
            
            content_above = scroll_top > 0
            content_below = scroll_top + viewport_height < scroll_height
            
            if content_above:
                lines.insert(0, f"... {scroll_top} pixels above ...")
            if content_below:
                lines.append(f"... {scroll_height - scroll_top - viewport_height} pixels below ...")
        
        return "\n".join(lines)

    def _build_structured_lines(self, lines: List[str], max_lines: int | None = None, depth: int = 0) -> None:
        """Build structured text lines for LLM consumption."""
        if max_lines is not None and len(lines) >= max_lines:
            return
            
        if self.tagName == "#text":
            if self.text and self.text.strip():
                # For text nodes, just add the text content
                lines.append(self.text.strip())
            return
        
        if self.tagName == "#children":
            # Handle flattened children from excluded elements
            for child in self.children:
                child._build_structured_lines(lines, max_lines, depth)
            return
        
        # Build element representation
        parts = []
        
        # Add visual annotations
        if self.visualAnnotations:
            parts.extend(self.visualAnnotations)
        
        # Add highlight index with new element marking
        if self.highlightIndex is not None:
            index_str = f"*[{self.highlightIndex}]" if self.isNewElement else f"[{self.highlightIndex}]"
            parts.append(index_str)
        
        # Build tag with relevant attributes
        tag_parts = [f"<{self.tagName}"]
        
        # Add only relevant attributes
        if self.attributes:
            for key, value in self.attributes.items():
                if value:  # Only include non-empty attributes
                    tag_parts.append(f'{key}="{value}"')
        
        tag_parts.append("/>")
        tag_str = " ".join(tag_parts)
        parts.append(tag_str)
        
        # Combine parts
        element_line = " ".join(parts)
        
        # Add text content if present
        text_content = ""
        for child in self.children:
            if child.tagName == "#text" and child.text:
                text_content += " " + child.text.strip()
        
        if text_content.strip():
            element_line += " " + text_content.strip()
        
        lines.append(element_line)
        
        # Process non-text children
        for child in self.children:
            if max_lines is not None and len(lines) >= max_lines:
                break
            if child.tagName != "#text":
                child._build_structured_lines(lines, max_lines, depth + 1)

    def to_lines(
        self, depth: int = 0, max_lines: int | None = None, _lines=None
    ) -> List[str]:
        """Return indented text representation of the DOM tree (legacy format)."""
        if _lines is None:
            _lines = []
        if max_lines is not None and len(_lines) >= max_lines:
            return _lines
        indent = "  " * depth
        if self.tagName == "#text":
            if self.text and self.text.strip():
                _lines.append(f"{indent}{self.text.strip()}")
            return _lines
        if self.tagName == "#children":
            # Handle flattened children
            for ch in self.children:
                ch.to_lines(depth, max_lines, _lines)
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
