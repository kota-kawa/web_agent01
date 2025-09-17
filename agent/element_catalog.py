"""
Element catalog generation for Browser Use-style element specification.

This module provides functionality to:
1. Generate element catalogs with index-based identification
2. Create abbreviated views for LLM consumption
3. Generate full views for executor resolution
4. Track catalog versions for consistency
"""
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from agent.browser.dom import DOMElementNode

log = logging.getLogger(__name__)


@dataclass
class ElementInfo:
    """Information about a single interactive element."""
    index: int
    role: str
    tag: str
    primary_label: str
    secondary_label: str
    section_hint: str
    state_hint: str
    href_short: str
    # Full view only fields
    robust_selectors: List[str] = field(default_factory=list)
    bbox: Dict[str, float] = field(default_factory=dict)
    visible: bool = True
    disabled: bool = False
    href_full: str = ""
    dom_path_hash: str = ""
    nearest_texts: List[str] = field(default_factory=list)
    section_id: str = ""
    xpath: str = ""


@dataclass
class ElementCatalog:
    """Complete element catalog with abbreviated and full views."""
    catalog_version: str
    url: str
    title: str
    short_summary: str
    abbreviated_view: List[ElementInfo]
    full_view: Dict[int, ElementInfo]
    nav_detected: bool = False


class ElementCatalogGenerator:
    """Generates element catalogs from DOM trees."""
    
    def __init__(self, index_mode: bool = True):
        self.index_mode = index_mode
        
    def generate_catalog(
        self, 
        dom_tree: DOMElementNode, 
        url: str = "", 
        title: str = "",
        viewport_info: Optional[Dict] = None
    ) -> ElementCatalog:
        """Generate complete element catalog from DOM tree."""
        if not self.index_mode:
            # Return empty catalog if index mode is disabled
            return ElementCatalog(
                catalog_version="disabled",
                url=url,
                title=title,
                short_summary="Index mode disabled",
                abbreviated_view=[],
                full_view={}
            )
        
        # Extract interactive elements
        elements = self._extract_interactive_elements(dom_tree)
        
        # Sort elements by position (top to bottom, left to right)
        elements = self._sort_elements_by_position(elements)
        
        # Group by sections and assign stable indices
        indexed_elements = self._assign_indices(elements)
        
        # Generate catalog version
        catalog_version = self._generate_catalog_version(url, dom_tree, viewport_info)
        
        # Create abbreviated and full views
        abbreviated_view = []
        full_view = {}
        
        for element_info in indexed_elements:
            # Add to both views
            abbreviated_view.append(element_info)
            full_view[element_info.index] = element_info
        
        # Generate short summary
        short_summary = self._generate_short_summary(indexed_elements, title)
        
        return ElementCatalog(
            catalog_version=catalog_version,
            url=url,
            title=title,
            short_summary=short_summary,
            abbreviated_view=abbreviated_view,
            full_view=full_view
        )
    
    def _extract_interactive_elements(self, dom_tree: DOMElementNode) -> List[Dict]:
        """Extract clickable/enterable elements from DOM tree."""
        elements = []
        
        def traverse(node: DOMElementNode, section_id: str = ""):
            if not node:
                return
            
            # Determine section ID from headings
            current_section_id = section_id
            if node.tagName.lower() in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article']:
                current_section_id = self._extract_section_id(node)
            
            # Check if this element is interactive
            if self._is_interactive_element(node):
                element_data = self._extract_element_data(node, current_section_id)
                if element_data:
                    elements.append(element_data)
            
            # Traverse children
            for child in node.children:
                traverse(child, current_section_id)
        
        traverse(dom_tree)
        return elements
    
    def _is_interactive_element(self, node: DOMElementNode) -> bool:
        """Check if element is interactive (clickable/enterable)."""
        if not node.isVisible or not node.isInteractive:
            return False
        
        tag = node.tagName.lower()
        
        # Direct interactive tags
        interactive_tags = {
            'a', 'button', 'input', 'select', 'textarea', 'summary'
        }
        
        if tag in interactive_tags:
            # Exclude hidden inputs
            if tag == 'input' and node.attributes.get('type') == 'hidden':
                return False
            return True
        
        # Check for interactive roles
        role = node.attributes.get('role', '').lower()
        interactive_roles = {
            'button', 'link', 'tab', 'menuitem', 'option'
        }
        
        if role in interactive_roles:
            return True
        
        # Check for contenteditable
        if node.attributes.get('contenteditable') == 'true':
            return True
        
        return False
    
    def _extract_element_data(self, node: DOMElementNode, section_id: str) -> Optional[Dict]:
        """Extract data for a single interactive element."""
        try:
            tag = node.tagName.lower()
            attrs = node.attributes
            
            # Generate robust selectors
            robust_selectors = self._generate_robust_selectors(node)
            
            # Extract labels
            primary_label = self._extract_primary_label(node)
            secondary_label = self._extract_secondary_label(node)
            
            # Extract state information
            state_hint = self._extract_state_hint(node)
            
            # Extract role
            role = self._extract_role(node)
            
            # Extract href information
            href_full = attrs.get('href', '')
            href_short = self._shorten_href(href_full)
            
            # Generate DOM path hash for stability
            dom_path_hash = self._generate_dom_path_hash(node)
            
            # Extract nearby text for context
            nearest_texts = self._extract_nearest_texts(node)
            
            # Extract bounding box if available
            bbox = self._extract_bbox(node)
            
            return {
                'role': role,
                'tag': tag,
                'primary_label': primary_label[:60] if primary_label else "",
                'secondary_label': secondary_label[:40] if secondary_label else "",
                'section_hint': section_id[:30] if section_id else "",
                'state_hint': state_hint,
                'href_short': href_short,
                'robust_selectors': robust_selectors,
                'bbox': bbox,
                'visible': node.isVisible,
                'disabled': attrs.get('disabled') == 'true' or attrs.get('disabled') == '',
                'href_full': href_full,
                'dom_path_hash': dom_path_hash,
                'nearest_texts': nearest_texts,
                'section_id': section_id,
                'xpath': node.xpath
            }
        except Exception as e:
            log.error("Error extracting element data: %s", e)
            return None
    
    def _generate_robust_selectors(self, node: DOMElementNode) -> List[str]:
        """Generate robust selectors in priority order."""
        selectors = []
        attrs = node.attributes
        tag = node.tagName.lower()
        
        # 1. getByRole (highest priority)
        role = attrs.get('role')
        aria_label = attrs.get('aria-label')
        if role and aria_label:
            selectors.append(f"getByRole('{role}', {{ name: '{aria_label}' }})")
        elif role:
            selectors.append(f"getByRole('{role}')")
        
        # 2. Text locator
        text_content = self._extract_primary_label(node)
        if text_content and len(text_content.strip()) > 0:
            selectors.append(f"getByText('{text_content.strip()}')")
        
        # 3. ID selector
        element_id = attrs.get('id')
        if element_id:
            selectors.append(f"#{element_id}")
        
        # 4. data-testid
        test_id = attrs.get('data-testid')
        if test_id:
            selectors.append(f"[data-testid='{test_id}']")
        
        # 5. Short relative CSS
        css_selector = self._generate_short_css(node)
        if css_selector:
            selectors.append(css_selector)
        
        # 6. XPath (last resort)
        if node.xpath:
            selectors.append(node.xpath)
        
        return selectors
    
    def _extract_primary_label(self, node: DOMElementNode) -> str:
        """Extract primary label for the element."""
        attrs = node.attributes
        
        # Priority order for label extraction
        label_sources = [
            attrs.get('aria-label'),
            attrs.get('title'),
            attrs.get('placeholder'),
            attrs.get('alt'),
            attrs.get('value') if attrs.get('type') in ['submit', 'button'] else None,
            node.text
        ]
        
        for source in label_sources:
            if source and source.strip():
                return source.strip()
        
        return ""
    
    def _extract_secondary_label(self, node: DOMElementNode) -> str:
        """Extract secondary label information."""
        attrs = node.attributes
        tag = node.tagName.lower()
        
        if tag == 'input':
            input_type = attrs.get('type', 'text')
            name = attrs.get('name', '')
            return f"{input_type}" + (f"[{name}]" if name else "")
        
        if tag == 'select':
            name = attrs.get('name', '')
            return f"select" + (f"[{name}]" if name else "")
        
        # Use class names for additional context
        class_names = attrs.get('class', '')
        if class_names:
            # Take first 2-3 meaningful class names
            classes = [cls for cls in class_names.split() if len(cls) > 2][:2]
            return " ".join(classes)
        
        return ""
    
    def _extract_role(self, node: DOMElementNode) -> str:
        """Extract semantic role of the element."""
        tag = node.tagName.lower()
        attrs = node.attributes
        
        # Explicit role attribute
        explicit_role = attrs.get('role')
        if explicit_role:
            return explicit_role
        
        # Implicit roles based on tag
        implicit_roles = {
            'a': 'link',
            'button': 'button',
            'input': 'textbox',  # Default, may be overridden
            'select': 'combobox',
            'textarea': 'textbox',
            'summary': 'button'
        }
        
        if tag == 'input':
            input_type = attrs.get('type', 'text')
            type_roles = {
                'button': 'button',
                'submit': 'button',
                'reset': 'button',
                'checkbox': 'checkbox',
                'radio': 'radio',
                'range': 'slider'
            }
            return type_roles.get(input_type, 'textbox')
        
        return implicit_roles.get(tag, 'generic')
    
    def _extract_state_hint(self, node: DOMElementNode) -> str:
        """Extract state information (disabled, selected, etc.)."""
        attrs = node.attributes
        states = []
        
        if attrs.get('disabled') in ['true', '']:
            states.append('disabled')
        
        if attrs.get('aria-selected') == 'true':
            states.append('selected')
        
        if attrs.get('aria-checked') == 'true':
            states.append('checked')
        
        if attrs.get('aria-expanded') == 'true':
            states.append('expanded')
        elif attrs.get('aria-expanded') == 'false':
            states.append('collapsed')
        
        return " ".join(states)
    
    def _extract_section_id(self, node: DOMElementNode) -> str:
        """Extract section identifier from heading or section element."""
        if node.text:
            # Use heading text as section ID
            return node.text.strip()[:30]
        
        # Fallback to ID or class
        attrs = node.attributes
        section_id = attrs.get('id') or attrs.get('class', '').split()[0] if attrs.get('class') else ''
        return section_id[:30]
    
    def _shorten_href(self, href: str) -> str:
        """Shorten href for abbreviated view."""
        if not href:
            return ""
        
        if len(href) <= 50:
            return href
        
        # Try to keep meaningful parts
        if href.startswith('http'):
            try:
                from urllib.parse import urlparse
                parsed = urlparse(href)
                path = parsed.path
                if len(path) > 30:
                    path = "..." + path[-27:]
                return f"{parsed.netloc}{path}"
            except:
                pass
        
        return href[:47] + "..."
    
    def _generate_short_css(self, node: DOMElementNode) -> str:
        """Generate short, stable CSS selector."""
        attrs = node.attributes
        tag = node.tagName.lower()
        
        # Try class-based selector first
        class_names = attrs.get('class', '')
        if class_names:
            # Use first meaningful class
            classes = [cls for cls in class_names.split() if len(cls) > 2]
            if classes:
                return f"{tag}.{classes[0]}"
        
        # Try attribute-based selector
        for attr in ['name', 'type', 'data-testid']:
            if attrs.get(attr):
                return f"{tag}[{attr}='{attrs[attr]}']"
        
        return f"{tag}"
    
    def _generate_dom_path_hash(self, node: DOMElementNode) -> str:
        """Generate stable hash for DOM path."""
        if node.xpath:
            return hashlib.md5(node.xpath.encode()).hexdigest()[:8]
        return ""
    
    def _extract_nearest_texts(self, node: DOMElementNode) -> List[str]:
        """Extract nearby text content for context."""
        texts = []
        
        # Get text from the element itself
        if node.text and node.text.strip():
            texts.append(node.text.strip())
        
        # Note: In a full implementation, we would traverse siblings
        # and parent elements to find contextual text
        
        return texts[:3]  # Limit to 3 items
    
    def _extract_bbox(self, node: DOMElementNode) -> Dict[str, float]:
        """Extract bounding box information if available."""
        # This would typically be populated by browser-side evaluation
        # For now, return empty dict as DOM tree doesn't include bbox by default
        return {}
    
    def _sort_elements_by_position(self, elements: List[Dict]) -> List[Dict]:
        """Sort elements by visual position (top to bottom, left to right)."""
        # In a full implementation, this would use actual bounding box data
        # For now, maintain document order which approximates visual order
        return elements
    
    def _assign_indices(self, elements: List[Dict]) -> List[ElementInfo]:
        """Assign stable indices to elements."""
        indexed_elements = []
        
        for i, element_data in enumerate(elements):
            element_info = ElementInfo(
                index=i,
                role=element_data['role'],
                tag=element_data['tag'],
                primary_label=element_data['primary_label'],
                secondary_label=element_data['secondary_label'],
                section_hint=element_data['section_hint'],
                state_hint=element_data['state_hint'],
                href_short=element_data['href_short'],
                robust_selectors=element_data['robust_selectors'],
                bbox=element_data['bbox'],
                visible=element_data['visible'],
                disabled=element_data['disabled'],
                href_full=element_data['href_full'],
                dom_path_hash=element_data['dom_path_hash'],
                nearest_texts=element_data['nearest_texts'],
                section_id=element_data['section_id'],
                xpath=element_data['xpath']
            )
            indexed_elements.append(element_info)
        
        return indexed_elements
    
    def _generate_catalog_version(
        self, 
        url: str, 
        dom_tree: DOMElementNode, 
        viewport_info: Optional[Dict] = None
    ) -> str:
        """Generate catalog version hash."""
        # Create hash from URL + DOM structure + viewport
        dom_hash = self._calculate_dom_hash(dom_tree)
        viewport_hash = str(hash(str(viewport_info))) if viewport_info else "none"
        
        combined = f"{url}|{dom_hash}|{viewport_hash}"
        return hashlib.md5(combined.encode()).hexdigest()[:12]
    
    def _calculate_dom_hash(self, dom_tree: DOMElementNode) -> str:
        """Calculate hash of DOM structure."""
        def serialize_node(node: DOMElementNode) -> str:
            parts = [node.tagName, str(node.isVisible), str(node.isInteractive)]
            if node.attributes:
                # Include key attributes that affect interactivity
                key_attrs = ['id', 'class', 'type', 'name', 'href']
                attr_parts = [f"{k}:{v}" for k, v in node.attributes.items() if k in key_attrs]
                parts.extend(sorted(attr_parts))
            
            return "|".join(parts)
        
        def traverse_for_hash(node: DOMElementNode) -> List[str]:
            parts = [serialize_node(node)]
            for child in node.children:
                parts.extend(traverse_for_hash(child))
            return parts
        
        all_parts = traverse_for_hash(dom_tree)
        combined = "||".join(all_parts)
        return hashlib.md5(combined.encode()).hexdigest()[:8]
    
    def _generate_short_summary(self, elements: List[ElementInfo], title: str) -> str:
        """Generate short summary of the page."""
        element_count = len(elements)
        
        # Count element types
        type_counts = {}
        for element in elements:
            role = element.role
            type_counts[role] = type_counts.get(role, 0) + 1
        
        # Create summary
        summary_parts = []
        if title:
            summary_parts.append(f"'{title[:30]}'")
        
        summary_parts.append(f"{element_count} interactive elements")
        
        # Add top element types
        if type_counts:
            top_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            type_desc = ", ".join([f"{count} {role}s" for role, count in top_types])
            summary_parts.append(f"({type_desc})")
        
        return " ".join(summary_parts)


# Global instance
_catalog_generator = None


def get_catalog_generator() -> ElementCatalogGenerator:
    """Get global catalog generator instance."""
    global _catalog_generator
    if _catalog_generator is None:
        _catalog_generator = ElementCatalogGenerator()
    return _catalog_generator


def generate_element_catalog(
    dom_tree: DOMElementNode,
    url: str = "",
    title: str = "",
    viewport_info: Optional[Dict] = None
) -> ElementCatalog:
    """Convenience function to generate element catalog."""
    generator = get_catalog_generator()
    return generator.generate_catalog(dom_tree, url, title, viewport_info)