"""
Element Catalog Generation for Browser Use-style Element Specification

This module provides observation phase functionality to generate an "element catalog"
that extracts operational elements on the page and assigns them index numbers.

Features:
- Two-layer system: Abbreviated view (for LLM) and Full view (for execution)
- Stable ordering by position and grouping by sections
- Catalog versioning for consistency verification
- Backward compatibility with existing DOM system
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urlparse

from .browser.dom import DOMElementNode


@dataclass
class ElementCatalogEntry:
    """Single element entry in the catalog"""
    index: int
    
    # Abbreviated view (for LLM presentation)
    role: str
    tag: str
    primary_label: str  # <=60 characters
    secondary_label: str  # <=40 characters
    section_hint: str
    state_hint: str  # e.g., disabled/selected
    href_short: str
    
    # Full view (for executor only)
    robust_selectors: List[str] = field(default_factory=list)
    bbox: Dict[str, float] = field(default_factory=dict)
    visible: bool = True
    disabled: bool = False
    href_full: str = ""
    dom_path_hash: str = ""
    nearest_texts: List[str] = field(default_factory=list)
    section_id: str = ""


@dataclass
class ElementCatalog:
    """Complete element catalog with abbreviated and full views"""
    version: str
    url: str
    title: str
    short_summary: str
    nav_detected: bool = False
    
    # Abbreviated view for LLM
    abbreviated_entries: List[Dict] = field(default_factory=list)
    
    # Full view for execution
    index_map: Dict[int, ElementCatalogEntry] = field(default_factory=dict)


class ElementCatalogGenerator:
    """Generates element catalogs from DOM and page information"""
    
    def __init__(self):
        self.previous_catalog: Optional[ElementCatalog] = None
    
    def generate_catalog(
        self,
        dom_elements: DOMElementNode,
        url: str,
        title: str,
        viewport_info: Optional[Dict] = None
    ) -> ElementCatalog:
        """Generate a complete element catalog from DOM elements"""
        
        # Extract interactive elements from DOM
        interactive_elements = self._extract_interactive_elements(dom_elements)
        
        # Sort elements in stable order (top->bottom, left->right)
        sorted_elements = self._sort_elements_stable(interactive_elements)
        
        # Group by sections and assign indices
        catalog_entries = self._assign_indices_and_sections(sorted_elements)
        
        # Generate abbreviated and full views
        abbreviated_entries = self._generate_abbreviated_view(catalog_entries)
        index_map = {entry.index: entry for entry in catalog_entries}
        
        # Generate version hash
        version = self._generate_catalog_version(url, dom_elements, viewport_info)
        
        # Create summary
        short_summary = self._generate_short_summary(catalog_entries, title)
        
        # Detect navigation changes
        nav_detected = self._detect_navigation_change(url, version)
        
        catalog = ElementCatalog(
            version=version,
            url=url,
            title=title,
            short_summary=short_summary,
            nav_detected=nav_detected,
            abbreviated_entries=abbreviated_entries,
            index_map=index_map
        )
        
        self.previous_catalog = catalog
        return catalog
    
    def _extract_interactive_elements(self, dom_node: DOMElementNode) -> List[Dict]:
        """Extract clickable/enterable elements from DOM tree"""
        elements = []
        
        def traverse(node, depth=0):
            if depth > 50:  # Prevent infinite recursion
                return
                
            # Check if element is interactive
            if self._is_interactive_element(node):
                element_info = self._extract_element_info(node)
                if element_info:
                    elements.append(element_info)
            
            # Traverse children
            for child in getattr(node, 'children', []):
                if isinstance(child, DOMElementNode):
                    traverse(child, depth + 1)
        
        traverse(dom_node)
        return elements
    
    def _is_interactive_element(self, node: DOMElementNode) -> bool:
        """Check if an element is interactive (clickable/enterable)"""
        if not hasattr(node, 'tag') or not node.tag:
            return False
        
        tag = node.tag.lower()
        attrs = getattr(node, 'attributes', {}) or {}
        
        # Interactive tags
        interactive_tags = {
            'a', 'button', 'input', 'select', 'textarea', 'summary'
        }
        
        if tag in interactive_tags:
            # Check for href on links
            if tag == 'a' and not attrs.get('href'):
                return False
            # Check for hidden inputs
            if tag == 'input' and attrs.get('type') == 'hidden':
                return False
            return True
        
        # Elements with interactive roles
        role = attrs.get('role', '').lower()
        interactive_roles = {
            'button', 'link', 'tab', 'menuitem', 'option', 'checkbox', 'radio'
        }
        
        if role in interactive_roles:
            return True
        
        # Contenteditable elements
        if attrs.get('contenteditable') == 'true':
            return True
        
        # Elements with click handlers (if we can detect them)
        if attrs.get('onclick') or 'click' in attrs.get('class', '').lower():
            return True
        
        return False
    
    def _extract_element_info(self, node: DOMElementNode) -> Optional[Dict]:
        """Extract detailed information from an interactive element"""
        if not self._is_visible_and_enabled(node):
            return None
        
        attrs = getattr(node, 'attributes', {}) or {}
        tag = getattr(node, 'tag', '').lower()
        
        # Get text content
        text_content = self._get_element_text(node)
        
        # Generate robust selectors
        robust_selectors = self._generate_robust_selectors(node)
        
        # Get bounding box if available
        bbox = self._get_bounding_box(node)
        
        # Extract labels and hints
        primary_label = self._extract_primary_label(node, text_content, attrs)[:60]
        secondary_label = self._extract_secondary_label(node, attrs)[:40]
        state_hint = self._extract_state_hint(node, attrs)
        
        # Determine role
        role = self._determine_element_role(tag, attrs)
        
        # Extract href info
        href_full = attrs.get('href', '')
        href_short = self._shorten_href(href_full)
        
        return {
            'node': node,
            'tag': tag,
            'primary_label': primary_label,
            'secondary_label': secondary_label,
            'role': role,
            'state_hint': state_hint,
            'href_full': href_full,
            'href_short': href_short,
            'robust_selectors': robust_selectors,
            'bbox': bbox,
            'text_content': text_content,
            'attrs': attrs,
            'visible': True,
            'disabled': attrs.get('disabled') == 'true' or attrs.get('aria-disabled') == 'true'
        }
    
    def _is_visible_and_enabled(self, node: DOMElementNode) -> bool:
        """Check if element is visible and enabled"""
        # Use existing visibility logic from DOM system
        if hasattr(node, 'isVisible') and not node.isVisible:
            return False
        
        attrs = getattr(node, 'attributes', {}) or {}
        
        # Check disabled state
        if attrs.get('disabled') == 'true':
            return False
        
        # Check aria-hidden
        if attrs.get('aria-hidden') == 'true':
            return False
        
        # Check display style (basic check)
        style = attrs.get('style', '')
        if 'display:none' in style.replace(' ', '') or 'visibility:hidden' in style.replace(' ', ''):
            return False
        
        return True
    
    def _get_element_text(self, node: DOMElementNode) -> str:
        """Extract meaningful text from element and its children"""
        text_parts = []
        
        def collect_text(n):
            # Get direct text content
            if hasattr(n, 'text') and n.text:
                clean_text = n.text.strip()
                if clean_text and len(clean_text) > 1:  # Filter out single characters
                    text_parts.append(clean_text)
            
            # Process children
            for child in getattr(n, 'children', []):
                if hasattr(child, 'text'):
                    collect_text(child)
        
        collect_text(node)
        return ' '.join(text_parts)
    
    def _generate_robust_selectors(self, node: DOMElementNode) -> List[str]:
        """Generate multiple robust selectors for the element"""
        selectors = []
        attrs = getattr(node, 'attributes', {}) or {}
        tag = getattr(node, 'tag', '').lower()
        
        # 1. Data-testid (highest priority)
        if attrs.get('data-testid'):
            selectors.append(f"[data-testid='{attrs['data-testid']}']")
        
        # 2. ID selector
        if attrs.get('id'):
            selectors.append(f"#{attrs['id']}")
        
        # 3. Role-based selector
        role = attrs.get('role')
        aria_label = attrs.get('aria-label')
        if role and aria_label:
            selectors.append(f"[role='{role}'][aria-label='{aria_label}']")
        elif role:
            selectors.append(f"[role='{role}']")
        
        # 4. Text-based selector
        text_content = self._get_element_text(node)
        if text_content and len(text_content) <= 50:
            selectors.append(f"text='{text_content}'")
        
        # 5. Name/placeholder for inputs
        if tag in ['input', 'textarea'] and attrs.get('name'):
            selectors.append(f"input[name='{attrs['name']}']")
        if attrs.get('placeholder'):
            selectors.append(f"[placeholder='{attrs['placeholder']}']")
        
        # 6. Short relative CSS
        css_selector = self._generate_short_css_selector(node, attrs, tag)
        if css_selector:
            selectors.append(css_selector)
        
        # 7. XPath as last resort
        if hasattr(node, 'xpath') and node.xpath:
            selectors.append(f"xpath={node.xpath}")
        
        return selectors[:5]  # Limit to top 5 selectors
    
    def _generate_short_css_selector(self, node: DOMElementNode, attrs: Dict, tag: str) -> str:
        """Generate a short, relative CSS selector"""
        parts = [tag]
        
        # Add class if not too generic
        classes = attrs.get('class', '').split()
        specific_classes = [c for c in classes if len(c) > 3 and not c.startswith('css-')]
        if specific_classes:
            parts.append(f".{specific_classes[0]}")
        
        # Add type for inputs
        if tag == 'input' and attrs.get('type'):
            parts.append(f"[type='{attrs['type']}']")
        
        return ''.join(parts)
    
    def _get_bounding_box(self, node: DOMElementNode) -> Dict[str, float]:
        """Extract bounding box information if available"""
        # This would be populated by the browser-side script
        # For now, return empty dict as placeholder
        return {}
    
    def _extract_primary_label(self, node: DOMElementNode, text_content: str, attrs: Dict) -> str:
        """Extract the primary label for the element"""
        # Priority order for labels
        candidates = [
            attrs.get('aria-label', ''),
            attrs.get('title', ''),
            text_content,
            attrs.get('placeholder', ''),
            attrs.get('alt', ''),
            attrs.get('value', ''),
            attrs.get('name', ''),
        ]
        
        for candidate in candidates:
            if candidate and len(candidate.strip()) > 0:
                return candidate.strip()
        
        return f"{attrs.get('tag', 'element')}"
    
    def _extract_secondary_label(self, node: DOMElementNode, attrs: Dict) -> str:
        """Extract secondary label information"""
        parts = []
        
        if attrs.get('type'):
            parts.append(f"type:{attrs['type']}")
        
        if attrs.get('name'):
            parts.append(f"name:{attrs['name']}")
        
        if attrs.get('id'):
            parts.append(f"id:{attrs['id']}")
        
        return ' '.join(parts)
    
    def _extract_state_hint(self, node: DOMElementNode, attrs: Dict) -> str:
        """Extract state hints like disabled, selected, etc."""
        hints = []
        
        if attrs.get('disabled') == 'true':
            hints.append('disabled')
        
        if attrs.get('aria-selected') == 'true':
            hints.append('selected')
        
        if attrs.get('aria-checked') == 'true':
            hints.append('checked')
        elif attrs.get('aria-checked') == 'false':
            hints.append('unchecked')
        
        if attrs.get('aria-expanded') == 'true':
            hints.append('expanded')
        elif attrs.get('aria-expanded') == 'false':
            hints.append('collapsed')
        
        return ', '.join(hints)
    
    def _determine_element_role(self, tag: str, attrs: Dict) -> str:
        """Determine the semantic role of the element"""
        # Explicit role attribute
        if attrs.get('role'):
            return attrs['role']
        
        # Implicit roles based on tag
        role_map = {
            'button': 'button',
            'a': 'link',
            'input': f"input-{attrs.get('type', 'text')}",
            'select': 'combobox',
            'textarea': 'textbox',
            'summary': 'button',
        }
        
        return role_map.get(tag, tag)
    
    def _shorten_href(self, href: str) -> str:
        """Shorten href for display purposes"""
        if not href:
            return ""
        
        if len(href) <= 40:
            return href
        
        # Try to parse URL and shorten intelligently
        try:
            parsed = urlparse(href)
            if parsed.path:
                path_parts = parsed.path.split('/')
                if len(path_parts) > 2:
                    return f"{parsed.netloc}/.../{path_parts[-1]}"
            return f"{parsed.netloc}{parsed.path}"[:40]
        except:
            return href[:37] + "..."
    
    def _sort_elements_stable(self, elements: List[Dict]) -> List[Dict]:
        """Sort elements in stable order (top->bottom, left->right)"""
        # For now, sort by existing highlightIndex if available
        # In a real implementation, this would use bbox coordinates
        
        def sort_key(elem):
            node = elem['node']
            # Use existing highlight index as primary sort
            highlight_idx = getattr(node, 'highlightIndex', 999999)
            if highlight_idx is not None:
                return (highlight_idx, elem['tag'], elem['primary_label'])
            
            # Fallback to tag and label for stable sorting
            return (999999, elem['tag'], elem['primary_label'])
        
        return sorted(elements, key=sort_key)
    
    def _assign_indices_and_sections(self, sorted_elements: List[Dict]) -> List[ElementCatalogEntry]:
        """Assign indices and group by sections"""
        catalog_entries = []
        
        for i, elem_info in enumerate(sorted_elements):
            # Generate section hint based on context
            section_hint = self._generate_section_hint(elem_info, i, sorted_elements)
            
            # Create catalog entry
            entry = ElementCatalogEntry(
                index=i,
                role=elem_info['role'],
                tag=elem_info['tag'],
                primary_label=elem_info['primary_label'],
                secondary_label=elem_info['secondary_label'],
                section_hint=section_hint,
                state_hint=elem_info['state_hint'],
                href_short=elem_info['href_short'],
                robust_selectors=elem_info['robust_selectors'],
                bbox=elem_info['bbox'],
                visible=elem_info['visible'],
                disabled=elem_info['disabled'],
                href_full=elem_info['href_full'],
                dom_path_hash=self._generate_dom_path_hash(elem_info['node']),
                nearest_texts=[],  # Would be populated by spatial analysis
                section_id=f"section_{i // 10}"  # Simple sectioning
            )
            
            catalog_entries.append(entry)
        
        return catalog_entries
    
    def _generate_section_hint(self, elem_info: Dict, index: int, all_elements: List[Dict]) -> str:
        """Generate section hint based on element context"""
        # Simple heuristic: group by tag type and position
        tag = elem_info['tag']
        
        if tag == 'a':
            return 'navigation'
        elif tag in ['input', 'textarea', 'select']:
            return 'form'
        elif tag == 'button':
            if 'submit' in elem_info['primary_label'].lower():
                return 'form'
            return 'action'
        
        return 'content'
    
    def _generate_dom_path_hash(self, node: DOMElementNode) -> str:
        """Generate a hash representing the DOM path to this element"""
        if hasattr(node, 'xpath') and node.xpath:
            return hashlib.md5(node.xpath.encode()).hexdigest()[:8]
        return ""
    
    def _generate_abbreviated_view(self, catalog_entries: List[ElementCatalogEntry]) -> List[Dict]:
        """Generate abbreviated view for LLM consumption"""
        abbreviated = []
        
        for entry in catalog_entries:
            abbreviated.append({
                'index': entry.index,
                'role': entry.role,
                'tag': entry.tag,
                'label': entry.primary_label,
                'secondary': entry.secondary_label,
                'section': entry.section_hint,
                'state': entry.state_hint,
                'href': entry.href_short
            })
        
        return abbreviated
    
    def _generate_catalog_version(
        self,
        url: str,
        dom_elements: DOMElementNode,
        viewport_info: Optional[Dict] = None
    ) -> str:
        """Generate catalog version hash"""
        # Combine URL, DOM structure, and viewport for version
        version_data = {
            'url': url,
            'dom_hash': self._generate_dom_hash(dom_elements),
            'viewport': viewport_info or {}
        }
        
        version_str = json.dumps(version_data, sort_keys=True)
        return hashlib.md5(version_str.encode()).hexdigest()[:12]
    
    def _generate_dom_hash(self, dom_node: DOMElementNode) -> str:
        """Generate hash representing DOM structure"""
        # Simple hash based on tag structure and interactive elements
        def collect_structure(node, depth=0):
            if depth > 20:  # Limit depth
                return []
            
            parts = []
            if hasattr(node, 'tag') and node.tag:
                parts.append(node.tag)
                if hasattr(node, 'highlightIndex') and node.highlightIndex is not None:
                    parts.append(f"idx{node.highlightIndex}")
            
            for child in getattr(node, 'children', []):
                if isinstance(child, DOMElementNode):
                    parts.extend(collect_structure(child, depth + 1))
            
            return parts
        
        structure = collect_structure(dom_node)
        structure_str = '|'.join(structure)
        return hashlib.md5(structure_str.encode()).hexdigest()[:8]
    
    def _generate_short_summary(self, catalog_entries: List[ElementCatalogEntry], title: str) -> str:
        """Generate a short summary of the page for observation"""
        num_elements = len(catalog_entries)
        
        # Count by type
        type_counts = {}
        for entry in catalog_entries:
            type_counts[entry.role] = type_counts.get(entry.role, 0) + 1
        
        # Create summary
        summary_parts = []
        if title:
            summary_parts.append(f"Page: {title[:50]}")
        
        summary_parts.append(f"{num_elements} interactive elements")
        
        # Add top element types
        if type_counts:
            top_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            type_desc = ', '.join([f"{count} {type_name}" for type_name, count in top_types])
            summary_parts.append(f"({type_desc})")
        
        return ' | '.join(summary_parts)
    
    def _detect_navigation_change(self, url: str, version: str) -> bool:
        """Detect if navigation occurred since last catalog"""
        if not self.previous_catalog:
            return True  # First catalog
        
        return (
            self.previous_catalog.url != url or 
            self.previous_catalog.version != version
        )