from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

DOM_SNAPSHOT_SCRIPT = """
(() => {
  // Constants for filtering disabled elements
  const DISABLED_ELEMENTS = new Set(['style', 'script', 'head', 'meta', 'link', 'title', 'noscript']);
  
  // Default attributes to include in the output
  const DEFAULT_INCLUDE_ATTRIBUTES = new Set([
    'title', 'type', 'name', 'role', 'value', 'placeholder', 'alt', 'href', 'src',
    'id', 'class', 'data-testid', 'aria-label', 'aria-labelledby', 'aria-describedby',
    'aria-expanded', 'aria-selected', 'aria-checked', 'aria-disabled', 'aria-hidden',
    'checked', 'disabled', 'readonly', 'required', 'selected', 'multiple'
  ]);
  
  // Elements that propagate their bounds to children
  const BOUNDS_PROPAGATION_ELEMENTS = new Set([
    'button', 'a', 'select', 'option', 'summary', 'label', 'li'
  ]);
  
  // Form input elements that should not be excluded even if inside propagation elements
  const FORM_INPUT_ELEMENTS = new Set([
    'input', 'textarea', 'select', 'option', 'checkbox', 'radio'
  ]);

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

  function isScrollable(el) {
    if (!(el instanceof Element)) return false;
    const style = window.getComputedStyle(el);
    const overflowX = style.overflowX;
    const overflowY = style.overflowY;
    const hasScroll = (overflowX === 'scroll' || overflowX === 'auto' || overflowY === 'scroll' || overflowY === 'auto');
    if (!hasScroll) return false;
    
    // Check if element actually has scrollable content
    return el.scrollHeight > el.clientHeight || el.scrollWidth > el.clientWidth;
  }

  function getElementRect(el) {
    if (!(el instanceof Element)) return null;
    const rect = el.getBoundingClientRect();
    return {
      top: rect.top,
      left: rect.left,
      right: rect.right,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height
    };
  }

  function isCompletelyObscuredBy(elementRect, otherRect) {
    if (!elementRect || !otherRect) return false;
    return (
      otherRect.left <= elementRect.left &&
      otherRect.top <= elementRect.top &&
      otherRect.right >= elementRect.right &&
      otherRect.bottom >= elementRect.bottom
    );
  }

  function rectContainsRect(parentRect, childRect, tolerance = 5) {
    if (!parentRect || !childRect) return false;
    return (
      parentRect.left <= childRect.left + tolerance &&
      parentRect.top <= childRect.top + tolerance &&
      parentRect.right >= childRect.right - tolerance &&
      parentRect.bottom >= childRect.bottom - tolerance
    );
  }

  function filterAttributes(attrs) {
    const filtered = {};
    for (const [name, value] of Object.entries(attrs)) {
      if (DEFAULT_INCLUDE_ATTRIBUTES.has(name)) {
        // Trim long attribute values
        let trimmedValue = value;
        if (typeof value === 'string' && value.length > 100) {
          trimmedValue = value.substring(0, 100) + '...';
        }
        filtered[name] = trimmedValue;
      }
    }
    return filtered;
  }

  // Paint order filtering - mark elements that are completely hidden by others
  function paintOrderFiltering(elements) {
    const visibleElements = elements.filter(el => el.isVisible && el.rect);
    
    for (let i = 0; i < visibleElements.length; i++) {
      const element = visibleElements[i];
      let isObscured = false;
      
      for (let j = i + 1; j < visibleElements.length; j++) {
        const otherElement = visibleElements[j];
        if (isCompletelyObscuredBy(element.rect, otherElement.rect)) {
          isObscured = true;
          break;
        }
      }
      
      if (isObscured) {
        element.excludedByPaint = true;
      }
    }
  }

  // Bounds propagation - mark children that are contained within propagation elements
  function boundsPropagate(elements) {
    const propagationElements = elements.filter(el => 
      el.isVisible && 
      BOUNDS_PROPAGATION_ELEMENTS.has(el.tagName) && 
      el.rect
    );
    
    for (const parent of propagationElements) {
      for (const element of elements) {
        if (element === parent || !element.isVisible || !element.rect) continue;
        
        // Don't exclude form input elements or other interactive elements
        if (FORM_INPUT_ELEMENTS.has(element.tagName) || element.isInteractive) continue;
        
        // Check if element is contained within parent bounds
        if (rectContainsRect(parent.rect, element.rect)) {
          element.excludedByParent = true;
        }
      }
    }
  }

  let counter = 1;
  let allElements = [];

  function serialize(node, depth = 0) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent.trim();
      // Filter out very short or meaningless text
      if (!text || text.length < 2) return null;
      return {nodeType: 'text', text, depth};
    }
    
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    
    const tagName = node.tagName.toLowerCase();
    
    // Skip disabled elements early
    if (DISABLED_ELEMENTS.has(tagName)) {
      return null;
    }
    
    const visible = isVisible(node);
    const interactive = visible && isInteractive(node);
    const scrollable = visible && isScrollable(node);
    const rect = visible ? getElementRect(node) : null;
    
    // Collect all attributes
    const attrs = {};
    for (const attr of Array.from(node.attributes)) {
      attrs[attr.name] = attr.value;
    }
    
    // Filter attributes to only include important ones
    const filteredAttrs = filterAttributes(attrs);
    
    const children = Array.from(node.childNodes).map(child => serialize(child, depth + 1)).filter(Boolean);
    
    const result = {
      tagName,
      attributes: filteredAttrs,
      xpath: computeXPath(node),
      isVisible: visible,
      isInteractive: interactive,
      isTopElement: interactive,
      isScrollable: scrollable,
      isIframe: tagName === 'iframe',
      highlightIndex: interactive ? counter++ : undefined,
      children,
      rect,
      depth,
      excludedByPaint: false,
      excludedByParent: false
    };
    
    // Store element for post-processing
    if (visible) {
      allElements.push(result);
    }
    
    return result;
  }

  // Generate the DOM tree
  const domTree = serialize(document.body);
  
  // Apply paint order filtering
  paintOrderFiltering(allElements);
  
  // Apply bounds propagation
  boundsPropagate(allElements);
  
  // Add viewport information
  const viewportInfo = {
    width: window.innerWidth,
    height: window.innerHeight,
    scrollX: window.scrollX,
    scrollY: window.scrollY,
    documentHeight: document.documentElement.scrollHeight,
    documentWidth: document.documentElement.scrollWidth
  };
  
  return {
    dom: domTree,
    viewport: viewportInfo,
    timestamp: Date.now()
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
    isScrollable: bool = False
    isIframe: bool = False
    highlightIndex: Optional[int] = None
    children: List["DOMElementNode"] = field(default_factory=list)
    excludedByPaint: bool = False
    excludedByParent: bool = False
    depth: int = 0

    @classmethod
    def from_json(cls, data: dict) -> "DOMElementNode":
        if data is None:
            return None
        if data.get("nodeType") == "text":
            return cls(
                tagName="#text", 
                text=data.get("text"),
                depth=data.get("depth", 0)
            )
        children = [cls.from_json(c) for c in data.get("children", []) if c]
        return cls(
            tagName=data.get("tagName", ""),
            attributes=data.get("attributes", {}),
            text=data.get("text"),
            xpath=data.get("xpath", ""),
            isVisible=data.get("isVisible", False),
            isInteractive=data.get("isInteractive", False),
            isTopElement=data.get("isTopElement", False),
            isScrollable=data.get("isScrollable", False),
            isIframe=data.get("isIframe", False),
            highlightIndex=data.get("highlightIndex"),
            children=children,
            excludedByPaint=data.get("excludedByPaint", False),
            excludedByParent=data.get("excludedByParent", False),
            depth=data.get("depth", 0),
        )

    @classmethod
    def from_page(cls, page) -> "DOMElementNode":
        """Retrieve DOM information directly from a Playwright page.

        The DOM tree along with visibility and interactivity flags is computed
        inside the browser to avoid Python-side heuristics.
        """
        dom_data = page.evaluate(DOM_SNAPSHOT_SCRIPT)
        # Handle new format with viewport info
        if isinstance(dom_data, dict) and "dom" in dom_data:
            return cls.from_json(dom_data["dom"])
        # Fallback for old format
        return cls.from_json(dom_data)

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

    def to_simplified_text(self, viewport_info: dict = None, prev_selector_map: dict = None) -> str:
        """Convert DOM tree to simplified text representation for LLM.
        
        This method implements the multi-stage filtering and summarization process
        described in the problem statement:
        - Excludes disabled elements (script, style, etc.)
        - Filters out invisible nodes
        - Applies paint order filtering for overlapping elements  
        - Uses bounding box propagation to consolidate child elements
        - Extracts meaningful text nodes
        - Includes only essential attributes
        - Adds index numbers to interactive elements
        - Provides visual annotations for scroll and iframe elements
        """
        lines = []
        selector_map = {}
        current_index = 1
        
        # Add viewport scroll indicators if available
        if viewport_info:
            scrollY = viewport_info.get('scrollY', 0)
            scrollX = viewport_info.get('scrollX', 0)
            docHeight = viewport_info.get('documentHeight', 0)
            docWidth = viewport_info.get('documentWidth', 0)
            viewHeight = viewport_info.get('height', 0)
            viewWidth = viewport_info.get('width', 0)
            
            if scrollY > 0:
                lines.append(f"... {scrollY} pixels above ...")
            if scrollX > 0:
                lines.append(f"... {scrollX} pixels left ...")
        
        def should_exclude_element(node):
            """Determine if element should be excluded from output."""
            # Skip text nodes that are too short
            if node.tagName == "#text":
                return not node.text or len(node.text.strip()) < 2
            
            # Skip invisible elements unless they have visible children
            if not node.isVisible and not any(child.isVisible for child in node.children):
                return True
                
            # Skip elements excluded by paint order filtering
            if node.excludedByPaint:
                return True
                
            # Skip elements excluded by parent bounds propagation
            # But preserve form inputs and interactive elements
            if node.excludedByParent and not node.isInteractive:
                return True
                
            return False
        
        def format_attributes(attrs):
            """Format attributes for display."""
            if not attrs:
                return ""
            
            # Prioritize important attributes
            priority_attrs = ['title', 'type', 'name', 'role', 'value', 'placeholder', 'alt']
            other_attrs = []
            formatted = []
            
            for attr in priority_attrs:
                if attr in attrs and attrs[attr]:
                    formatted.append(f'{attr}="{attrs[attr]}"')
            
            for attr, value in attrs.items():
                if attr not in priority_attrs and value:
                    other_attrs.append(f'{attr}="{value}"')
            
            formatted.extend(other_attrs[:3])  # Limit other attributes
            return " ".join(formatted)
        
        def add_element_to_output(node, depth=0):
            nonlocal current_index
            
            if should_exclude_element(node):
                return
            
            indent = "  " * depth
            
            # Handle text nodes
            if node.tagName == "#text":
                if node.text and node.text.strip():
                    lines.append(f"{indent}{node.text.strip()}")
                return
            
            # Format element
            attr_str = format_attributes(node.attributes)
            
            # Add special annotations
            annotations = []
            if node.isScrollable:
                annotations.append("|SCROLL|")
            if node.isIframe:
                annotations.append("|IFRAME|")
            
            # Add index for interactive elements
            index_str = ""
            if node.isInteractive and node.isVisible:
                # Check if this is a new element compared to previous state
                is_new = prev_selector_map is not None and node.xpath not in prev_selector_map.values()
                if is_new:
                    index_str = f"*[{current_index}]"
                else:
                    index_str = f"[{current_index}]"
                
                selector_map[current_index] = node.xpath
                current_index += 1
            
            # Format the element line
            tag_line = f"{indent}<{node.tagName}"
            if attr_str:
                tag_line += f" {attr_str}"
            tag_line += " />"
            
            if index_str:
                tag_line += f" {index_str}"
            
            if annotations:
                tag_line += f" {' '.join(annotations)}"
            
            lines.append(tag_line)
            
            # Add text content if any (for elements like buttons with text)
            if node.text and node.text.strip():
                lines.append(f"{indent}  {node.text.strip()}")
            
            # Process children
            for child in node.children:
                add_element_to_output(child, depth + 1)
        
        # Generate the simplified representation
        add_element_to_output(self)
        
        # Add bottom scroll indicators
        if viewport_info:
            scrollY = viewport_info.get('scrollY', 0)
            scrollX = viewport_info.get('scrollX', 0)
            docHeight = viewport_info.get('documentHeight', 0)
            docWidth = viewport_info.get('documentWidth', 0)
            viewHeight = viewport_info.get('height', 0)
            viewWidth = viewport_info.get('width', 0)
            
            remaining_height = docHeight - (scrollY + viewHeight)
            remaining_width = docWidth - (scrollX + viewWidth)
            
            if remaining_height > 0:
                lines.append(f"... {remaining_height} pixels below ...")
            if remaining_width > 0:
                lines.append(f"... {remaining_width} pixels right ...")
        
        return "\n".join(lines), selector_map
