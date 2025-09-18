from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

DOM_SNAPSHOT_SCRIPT = """
(() => {
  // Tags that provide no visual value and should be excluded entirely
  const excludedTags = new Set(['script', 'style', 'head', 'meta', 'link', 'title', 'noscript']);
  
  // Tags that should propagate their bounds to children (for merging)
  const boundsPropagateTags = new Set(['button', 'a', 'label', 'summary']);
  
  // Interactive elements that should remain separate even if inside propagation bounds
  const independentInteractiveTags = new Set(['input', 'select', 'textarea', 'button', 'a']);
  
  // Relevant attributes to keep (others will be filtered out)
  const relevantAttributes = new Set(['title', 'type', 'name', 'role', 'value', 'placeholder', 'alt', 'aria-label', 'aria-describedby', 'aria-expanded', 'aria-hidden', 'aria-selected', 'aria-checked', 'id', 'class', 'href', 'src']);

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

  function shouldExcludeElement(el) {
    if (excludedTags.has(el.tagName.toLowerCase())) return true;
    
    // Exclude if not visible and has no visible children
    if (!isVisible(el)) {
      const hasVisibleChildren = Array.from(el.children).some(child => isVisible(child));
      return !hasVisibleChildren;
    }
    
    return false;
  }

  function isElementCompletelyHidden(el, allElements) {
    if (!isVisible(el)) return true;
    
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return true;
    
    // Paint order filtering: check if element is completely covered by others
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    const topElement = document.elementFromPoint(centerX, centerY);
    
    // If the element at the center point is not this element or a descendant,
    // it might be covered
    if (topElement && !el.contains(topElement) && topElement !== el) {
      // Additional checks at corners and edges
      const points = [
        [rect.left + 1, rect.top + 1],
        [rect.right - 1, rect.top + 1],
        [rect.left + 1, rect.bottom - 1],
        [rect.right - 1, rect.bottom - 1],
        [centerX, rect.top + 1],
        [centerX, rect.bottom - 1],
        [rect.left + 1, centerY],
        [rect.right - 1, centerY]
      ];
      
      let visiblePoints = 0;
      for (const [x, y] of points) {
        const elementAtPoint = document.elementFromPoint(x, y);
        if (elementAtPoint && (el.contains(elementAtPoint) || elementAtPoint === el)) {
          visiblePoints++;
        }
      }
      
      // If less than 25% of test points are visible, consider it hidden
      return visiblePoints < 2;
    }
    
    return false;
  }

  function isInsideBoundsPropagateParent(el) {
    let parent = el.parentElement;
    while (parent) {
      if (boundsPropagateTags.has(parent.tagName.toLowerCase())) {
        const parentRect = parent.getBoundingClientRect();
        const elRect = el.getBoundingClientRect();
        
        // Check if element is substantially within parent bounds (allow small margin)
        const margin = 5;
        if (elRect.left >= parentRect.left - margin &&
            elRect.right <= parentRect.right + margin &&
            elRect.top >= parentRect.top - margin &&
            elRect.bottom <= parentRect.bottom + margin) {
          
          // Don't exclude if this is an independent interactive element
          if (independentInteractiveTags.has(el.tagName.toLowerCase())) {
            return false;
          }
          
          return true;
        }
      }
      parent = parent.parentElement;
    }
    return false;
  }

  function extractRelevantAttributes(el) {
    const attrs = {};
    for (const attr of Array.from(el.attributes)) {
      if (relevantAttributes.has(attr.name)) {
        let value = attr.value;
        // Trim long attribute values
        if (value && value.length > 100) {
          value = value.substring(0, 97) + '...';
        }
        attrs[attr.name] = value;
      }
    }
    return attrs;
  }

  function isScrollableContainer(el) {
    const style = window.getComputedStyle(el);
    return style.overflow === 'scroll' || style.overflow === 'auto' || 
           style.overflowX === 'scroll' || style.overflowX === 'auto' ||
           style.overflowY === 'scroll' || style.overflowY === 'auto';
  }

  function isIframe(el) {
    return el.tagName.toLowerCase() === 'iframe';
  }

  function getTextContent(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent.trim();
      // Filter out meaningless text (1-2 characters, only whitespace, etc.)
      if (!text || text.length <= 2 && /^[\\s\\n\\r\\t]*$/.test(text)) {
        return null;
      }
      return text;
    }
    return null;
  }

  let counter = 0;
  const allElements = Array.from(document.querySelectorAll('*'));
  
  function serialize(node, depth = 0) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = getTextContent(node);
      if (!text) return null;
      return {nodeType: 'text', text};
    }
    
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    
    // Exclude unnecessary elements
    if (shouldExcludeElement(node)) return null;
    
    // Paint order filtering - exclude completely hidden elements
    if (isElementCompletelyHidden(node, allElements)) return null;
    
    // Bounds propagation - exclude if inside bounds propagate parent
    if (isInsideBoundsPropagateParent(node)) return null;
    
    const visible = isVisible(node);
    const interactive = visible && isInteractive(node);
    const attrs = extractRelevantAttributes(node);
    
    // Add visual annotations
    const annotations = [];
    if (isScrollableContainer(node)) {
      annotations.push('SCROLL');
    }
    if (isIframe(node)) {
      annotations.push('IFRAME');
    }
    
    const children = Array.from(node.childNodes).map(child => serialize(child, depth + 1)).filter(Boolean);
    
    const result = {
      tagName: node.tagName.toLowerCase(),
      attributes: attrs,
      xpath: computeXPath(node),
      isVisible: visible,
      isInteractive: interactive,
      isTopElement: interactive,
      highlightIndex: interactive ? counter++ : undefined,
      children,
      annotations: annotations.length > 0 ? annotations : undefined,
      excludedByParent: false, // This will be set by parent elements
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
    annotations: Optional[List[str]] = None
    excludedByParent: bool = False
    isNewElement: bool = False  # For marking new elements with *

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
            annotations=data.get("annotations"),
            excludedByParent=data.get("excludedByParent", False),
            isNewElement=data.get("isNewElement", False),
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
        """Return structured text representation of the DOM tree optimized for LLM consumption."""
        if _lines is None:
            _lines = []
        if max_lines is not None and len(_lines) >= max_lines:
            return _lines
            
        indent = "  " * depth
        
        # Handle text nodes
        if self.tagName == "#text":
            if self.text and self.text.strip():
                # Only add meaningful text (already filtered in JS)
                _lines.append(f"{indent}{self.text.strip()}")
            return _lines
        
        # Skip excluded elements
        if self.excludedByParent:
            return _lines
            
        # Build element representation
        parts = []
        
        # Add interactive element index at the beginning
        if self.highlightIndex is not None:
            prefix = "*" if self.isNewElement else ""
            parts.append(f"[{prefix}{self.highlightIndex}]")
        
        # Add opening tag with attributes
        tag_parts = [f"<{self.tagName}"]
        
        # Add relevant attributes
        attr_parts = []
        for key, value in self.attributes.items():
            if value:  # Only add attributes with values
                # Format specific attributes nicely
                if key in ['type', 'name', 'role', 'title', 'placeholder', 'alt', 'aria-label']:
                    attr_parts.append(f'{key}="{value}"')
                elif key == 'href' and value.startswith('http'):
                    # Shorten long URLs
                    if len(value) > 50:
                        attr_parts.append(f'href="{value[:47]}..."')
                    else:
                        attr_parts.append(f'href="{value}"')
                elif key in ['id', 'class'] and len(value) <= 30:
                    attr_parts.append(f'{key}="{value}"')
        
        if attr_parts:
            tag_parts.append(" " + " ".join(attr_parts))
        
        # For self-closing elements or elements with only text content
        text_content = self._collect_text_content()
        
        if not self.children or (len(self.children) == 1 and self.children[0].tagName == "#text"):
            # Self-closing format with text content
            if text_content:
                tag_parts.append(f" /> {text_content}")
            else:
                tag_parts.append(" />")
            
            # Add visual annotations
            if self.annotations:
                for annotation in self.annotations:
                    tag_parts.append(f" |{annotation}|")
            
            parts.extend(tag_parts)
            _lines.append(f"{indent}{''.join(parts)}")
        else:
            # Opening tag
            tag_parts.append(">")
            
            # Add visual annotations
            if self.annotations:
                for annotation in self.annotations:
                    tag_parts.append(f" |{annotation}|")
            
            parts.extend(tag_parts)
            _lines.append(f"{indent}{''.join(parts)}")
            
            # Add children
            for ch in self.children:
                if max_lines is not None and len(_lines) >= max_lines:
                    break
                ch.to_lines(depth + 1, max_lines, _lines)
        
        return _lines
    
    def _collect_text_content(self) -> str:
        """Collect all text content from this element and its children."""
        texts = []
        
        def collect_text(node):
            if node.tagName == "#text" and node.text:
                texts.append(node.text.strip())
            else:
                for child in node.children:
                    collect_text(child)
        
        for child in self.children:
            collect_text(child)
        
        return " ".join(texts).strip()

    def to_text(self, max_lines: int | None = None, previous_dom: "DOMElementNode" = None) -> str:
        """Generate structured text representation with scroll position annotations."""
        # Mark new elements if we have a previous DOM to compare against
        if previous_dom:
            self._mark_new_elements(previous_dom)
        
        lines = self.to_lines(max_lines=max_lines)
        
        # Add scroll position annotations
        # Note: This would need to be enhanced with actual scroll position data from the browser
        # For now, we'll add placeholder logic that could be enhanced later
        result_lines = []
        
        # Check if we need scroll annotations (this would be populated from browser data)
        # For demonstration, we'll add the structure that would be used
        scroll_info = getattr(self, '_scroll_info', None)
        if scroll_info:
            if scroll_info.get('pixels_above', 0) > 0:
                result_lines.append(f"... {scroll_info['pixels_above']} pixels above ...")
        
        result_lines.extend(lines)
        
        if scroll_info:
            if scroll_info.get('pixels_below', 0) > 0:
                result_lines.append(f"... {scroll_info['pixels_below']} pixels below ...")
        
        return "\n".join(result_lines)
    
    def _mark_new_elements(self, previous_dom: "DOMElementNode"):
        """Mark elements that are new compared to previous DOM."""
        previous_elements = set()
        
        def collect_elements(node, elements_set):
            if node.xpath:
                elements_set.add(node.xpath)
            for child in node.children:
                collect_elements(child, elements_set)
        
        collect_elements(previous_dom, previous_elements)
        
        def mark_new(node):
            if node.xpath and node.xpath not in previous_elements:
                node.isNewElement = True
            for child in node.children:
                mark_new(child)
        
        mark_new(self)
    
    def set_scroll_info(self, pixels_above: int = 0, pixels_below: int = 0):
        """Set scroll position information for annotations."""
        self._scroll_info = {
            'pixels_above': pixels_above,
            'pixels_below': pixels_below
        }
