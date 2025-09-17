"""Element catalog generation for robust index-based element targeting.

This module provides observation phase functionality to generate element catalogs
that enable index-based targeting while maintaining backward compatibility.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Extraction targets for actionable elements
ACTIONABLE_SELECTORS = [
    "a[href]",  # Links with href
    "button",   # All buttons
    "input:not([type='hidden'])",  # Input fields except hidden
    "select",   # Select dropdowns
    "textarea", # Text areas
    "details summary",  # Collapsible sections
    "[contenteditable='true']",  # Editable content
    "[role='button']",   # Role-based buttons
    "[role='link']",     # Role-based links
    "[role='tab']",      # Tab elements
    "[role='menuitem']", # Menu items
    "[role='option']",   # Option elements
    "[role='checkbox']", # Checkbox elements
    "[role='radio']",    # Radio button elements
    "[role='textbox']",  # Textbox elements
]

@dataclass
class ElementInfo:
    """Full element information for executor use."""
    index: int
    robust_selectors: List[str] = field(default_factory=list)
    bbox: Optional[Dict[str, float]] = None
    visible: bool = True
    disabled: bool = False
    href_full: Optional[str] = None
    dom_path_hash: Optional[str] = None
    nearest_texts: List[str] = field(default_factory=list)
    section_id: Optional[str] = None
    tag_name: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    text_content: str = ""

@dataclass 
class ElementCatalogEntry:
    """Abbreviated element information for LLM presentation."""
    index: int
    role: str
    tag: str
    primary_label: str  # <=60 characters
    secondary_label: str  # <=40 characters
    section_hint: str
    state_hint: str  # disabled/selected/checked etc
    href_short: str  # Shortened href for links

@dataclass
class ElementCatalog:
    """Complete element catalog with both abbreviated and full views."""
    abbreviated_view: List[ElementCatalogEntry] = field(default_factory=list)
    full_view: Dict[int, ElementInfo] = field(default_factory=dict)
    catalog_version: str = ""
    url: str = ""
    title: str = ""
    short_summary: str = ""
    nav_detected: bool = False

class ElementCatalogGenerator:
    """Generates element catalogs for robust targeting."""
    
    def __init__(self, page=None):
        """Initialize with Playwright page object."""
        self.page = page
        
    async def generate_catalog(self, expected_version: Optional[str] = None) -> ElementCatalog:
        """Generate complete element catalog.
        
        Args:
            expected_version: Optional version to check against current state
            
        Returns:
            ElementCatalog with both abbreviated and full views
        """
        if not self.page:
            raise ValueError("Page object not available for catalog generation")
            
        # Get basic page info
        url = await self.page.url()
        title = await self.page.title()
        
        # Generate catalog version
        current_version = await self._generate_catalog_version()
        
        # Check if catalog is outdated
        if expected_version and expected_version != current_version:
            catalog = ElementCatalog(
                catalog_version=current_version,
                url=url,
                title=title,
                nav_detected=True
            )
            return catalog
            
        # Extract actionable elements
        elements = await self._extract_actionable_elements()
        
        # Generate both views
        abbreviated_view = []
        full_view = {}
        
        for i, element_info in enumerate(elements):
            element_info.index = i
            
            # Create abbreviated entry
            abbreviated_entry = self._create_abbreviated_entry(element_info)
            abbreviated_view.append(abbreviated_entry)
            
            # Store full info
            full_view[i] = element_info
        
        # Generate page summary
        short_summary = await self._generate_page_summary()
        
        catalog = ElementCatalog(
            abbreviated_view=abbreviated_view,
            full_view=full_view,
            catalog_version=current_version,
            url=url,
            title=title,
            short_summary=short_summary,
            nav_detected=False
        )
        
        log.info(f"Generated catalog with {len(elements)} elements, version: {current_version}")
        return catalog
        
    async def _generate_catalog_version(self) -> str:
        """Generate catalog version based on URL + DOM hash + viewport hash."""
        try:
            url = await self.page.url()
            
            # Get DOM structure hash (lightweight)
            dom_hash = await self.page.evaluate("""
                () => {
                    const getAllElements = (element) => {
                        let result = element.tagName || '';
                        if (element.id) result += '#' + element.id;
                        if (element.className) result += '.' + element.className.replace(/\\s+/g, '.');
                        for (let child of element.children) {
                            result += getAllElements(child);
                        }
                        return result;
                    };
                    return btoa(getAllElements(document.body || document.documentElement)).slice(0, 16);
                }
            """)
            
            # Get viewport hash
            viewport = await self.page.viewport_size()
            viewport_hash = hashlib.md5(f"{viewport['width']}x{viewport['height']}".encode()).hexdigest()[:8]
            
            # Combine for final version
            combined = f"{url}#{dom_hash}#{viewport_hash}"
            version = hashlib.md5(combined.encode()).hexdigest()[:12]
            
            return version
            
        except Exception as e:
            log.error(f"Failed to generate catalog version: {e}")
            # Fallback to timestamp-based version
            import time
            return hashlib.md5(str(time.time()).encode()).hexdigest()[:12]
    
    async def _extract_actionable_elements(self) -> List[ElementInfo]:
        """Extract actionable elements from the page."""
        elements = []
        
        try:
            # Use JavaScript to extract elements efficiently
            element_data = await self.page.evaluate("""
                () => {
                    const actionableSelectors = %s;
                    const elements = [];
                    
                    // Get all actionable elements
                    const allElements = [];
                    for (const selector of actionableSelectors) {
                        try {
                            const found = document.querySelectorAll(selector);
                            allElements.push(...found);
                        } catch (e) {
                            console.warn('Invalid selector:', selector, e);
                        }
                    }
                    
                    // Remove duplicates and filter
                    const uniqueElements = [...new Set(allElements)];
                    
                    for (const element of uniqueElements) {
                        // Check visibility and interactability
                        const rect = element.getBoundingClientRect();
                        const style = window.getComputedStyle(element);
                        
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        if (style.opacity === '0') continue;
                        
                        // Generate robust selectors
                        const robustSelectors = [];
                        
                        // Try ID selector
                        if (element.id) {
                            robustSelectors.push(`css=#${element.id}`);
                        }
                        
                        // Try data-testid
                        if (element.getAttribute('data-testid')) {
                            robustSelectors.push(`css=[data-testid="${element.getAttribute('data-testid')}"]`);
                        }
                        
                        // Try role-based selector
                        const role = element.getAttribute('role') || element.tagName.toLowerCase();
                        if (element.textContent && element.textContent.trim()) {
                            const text = element.textContent.trim().slice(0, 30);
                            robustSelectors.push(`css=[role="${role}"]:has-text("${text}")`);
                        }
                        
                        // Try aria-label
                        if (element.getAttribute('aria-label')) {
                            robustSelectors.push(`css=[aria-label="${element.getAttribute('aria-label')}"]`);
                        }
                        
                        // Try text content for clickable elements
                        if (element.textContent && element.textContent.trim()) {
                            const text = element.textContent.trim();
                            if (text.length <= 50) {
                                robustSelectors.push(`text=${text}`);
                            }
                        }
                        
                        // Fallback CSS selector (position-based)
                        const tagName = element.tagName.toLowerCase();
                        const classList = element.className ? '.' + element.className.split(' ').join('.') : '';
                        robustSelectors.push(`css=${tagName}${classList}:nth-of-type(${Array.from(element.parentElement.children).filter(e => e.tagName === element.tagName).indexOf(element) + 1})`);
                        
                        // XPath as last resort
                        const getXPath = (element) => {
                            if (element === document.body) return '/html/body';
                            let ix = 0;
                            let siblings = element.parentNode.childNodes;
                            for (let i = 0; i < siblings.length; i++) {
                                let sibling = siblings[i];
                                if (sibling === element) {
                                    return getXPath(element.parentNode) + '/' + element.tagName.toLowerCase() + '[' + (ix + 1) + ']';
                                }
                                if (sibling.nodeType === 1 && sibling.tagName === element.tagName) {
                                    ix++;
                                }
                            }
                        };
                        robustSelectors.push(`xpath=${getXPath(element)}`);
                        
                        // Get nearby text for context
                        const nearbyTexts = [];
                        const walker = document.createTreeWalker(
                            element.parentElement || document.body,
                            NodeFilter.SHOW_TEXT,
                            null,
                            false
                        );
                        
                        let node;
                        while (node = walker.nextNode()) {
                            const text = node.textContent.trim();
                            if (text && text.length > 3 && text.length < 100) {
                                nearbyTexts.push(text);
                            }
                            if (nearbyTexts.length >= 3) break;
                        }
                        
                        // Determine section
                        let sectionId = null;
                        let parent = element.parentElement;
                        while (parent && parent !== document.body) {
                            if (parent.id) {
                                sectionId = parent.id;
                                break;
                            }
                            if (['header', 'nav', 'main', 'section', 'article', 'aside', 'footer'].includes(parent.tagName.toLowerCase())) {
                                sectionId = parent.tagName.toLowerCase();
                                break;
                            }
                            parent = parent.parentElement;
                        }
                        
                        elements.push({
                            tag_name: element.tagName.toLowerCase(),
                            attributes: {
                                id: element.id || null,
                                class: element.className || null,
                                role: element.getAttribute('role') || null,
                                'aria-label': element.getAttribute('aria-label') || null,
                                href: element.getAttribute('href') || null,
                                type: element.getAttribute('type') || null,
                                placeholder: element.getAttribute('placeholder') || null
                            },
                            text_content: element.textContent ? element.textContent.trim().slice(0, 200) : '',
                            robust_selectors: robustSelectors,
                            bbox: {
                                x: rect.x,
                                y: rect.y,
                                width: rect.width,
                                height: rect.height
                            },
                            visible: true,
                            disabled: element.disabled || element.getAttribute('aria-disabled') === 'true',
                            href_full: element.getAttribute('href'),
                            nearest_texts: nearbyTexts,
                            section_id: sectionId
                        });
                    }
                    
                    // Sort by position (top to bottom, left to right)
                    elements.sort((a, b) => {
                        if (Math.abs(a.bbox.y - b.bbox.y) < 10) {
                            return a.bbox.x - b.bbox.x;
                        }
                        return a.bbox.y - b.bbox.y;
                    });
                    
                    return elements;
                }
            """ % json.dumps(ACTIONABLE_SELECTORS))
            
            # Convert to ElementInfo objects
            for data in element_data:
                element_info = ElementInfo(
                    index=0,  # Will be set later
                    tag_name=data['tag_name'],
                    attributes=data['attributes'],
                    text_content=data['text_content'],
                    robust_selectors=data['robust_selectors'],
                    bbox=data['bbox'],
                    visible=data['visible'],
                    disabled=data['disabled'],
                    href_full=data['href_full'],
                    nearest_texts=data['nearest_texts'],
                    section_id=data['section_id']
                )
                
                # Generate DOM path hash
                element_info.dom_path_hash = hashlib.md5(
                    f"{element_info.tag_name}:{element_info.attributes.get('id', '')}:{element_info.text_content[:50]}"
                    .encode()
                ).hexdigest()[:8]
                
                elements.append(element_info)
                
        except Exception as e:
            log.error(f"Failed to extract actionable elements: {e}")
            
        return elements
    
    def _create_abbreviated_entry(self, element_info: ElementInfo) -> ElementCatalogEntry:
        """Create abbreviated entry for LLM presentation."""
        # Determine role
        role = element_info.attributes.get('role', element_info.tag_name)
        
        # Create primary label (<=60 chars)
        primary_label = ""
        if element_info.text_content:
            primary_label = element_info.text_content[:60]
        elif element_info.attributes.get('aria-label'):
            primary_label = element_info.attributes['aria-label'][:60]
        elif element_info.attributes.get('placeholder'):
            primary_label = f"[{element_info.attributes['placeholder'][:55]}]"
        elif element_info.attributes.get('id'):
            primary_label = f"#{element_info.attributes['id'][:55]}"
        
        # Create secondary label (<=40 chars)
        secondary_label = ""
        if element_info.attributes.get('id') and not primary_label.startswith('#'):
            secondary_label = f"#{element_info.attributes['id'][:35]}"
        elif element_info.attributes.get('class'):
            classes = element_info.attributes['class'].split()[:2]
            secondary_label = f".{'.'.join(classes)}"[:40]
        
        # Section hint
        section_hint = element_info.section_id or ""
        
        # State hint
        state_hints = []
        if element_info.disabled:
            state_hints.append("disabled")
        if element_info.attributes.get('aria-selected') == 'true':
            state_hints.append("selected")
        if element_info.attributes.get('aria-checked') == 'true':
            state_hints.append("checked")
        if element_info.attributes.get('aria-expanded') == 'true':
            state_hints.append("expanded")
        state_hint = ", ".join(state_hints)
        
        # Href short
        href_short = ""
        if element_info.href_full:
            try:
                parsed = urlparse(element_info.href_full)
                if parsed.path:
                    href_short = parsed.path[:30]
                elif parsed.netloc:
                    href_short = parsed.netloc[:30]
                else:
                    href_short = element_info.href_full[:30]
            except:
                href_short = element_info.href_full[:30]
        
        return ElementCatalogEntry(
            index=element_info.index,
            role=role,
            tag=element_info.tag_name,
            primary_label=primary_label,
            secondary_label=secondary_label,
            section_hint=section_hint,
            state_hint=state_hint,
            href_short=href_short
        )
    
    async def _generate_page_summary(self) -> str:
        """Generate a short summary of the page."""
        try:
            summary_data = await self.page.evaluate("""
                () => {
                    const title = document.title || '';
                    const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
                        .map(h => h.textContent.trim())
                        .filter(text => text && text.length > 0)
                        .slice(0, 3);
                    
                    const description = document.querySelector('meta[name="description"]');
                    const metaDesc = description ? description.getAttribute('content') : '';
                    
                    return {
                        title: title.slice(0, 100),
                        headings: headings,
                        metaDescription: metaDesc.slice(0, 150)
                    };
                }
            """)
            
            parts = []
            if summary_data['title']:
                parts.append(f"Title: {summary_data['title']}")
            if summary_data['headings']:
                parts.append(f"Headings: {', '.join(summary_data['headings'])}")
            if summary_data['metaDescription']:
                parts.append(f"Description: {summary_data['metaDescription']}")
                
            return " | ".join(parts)[:300]
            
        except Exception as e:
            log.error(f"Failed to generate page summary: {e}")
            return ""

def format_catalog_for_llm(catalog: ElementCatalog) -> str:
    """Format element catalog for LLM presentation."""
    if not catalog.abbreviated_view:
        return "No interactive elements found on the page."
    
    lines = []
    lines.append(f"=== Element Catalog (v{catalog.catalog_version}) ===")
    lines.append(f"Page: {catalog.title}")
    if catalog.short_summary:
        lines.append(f"Summary: {catalog.short_summary}")
    lines.append("")
    
    current_section = None
    for entry in catalog.abbreviated_view:
        # Group by section
        if entry.section_hint and entry.section_hint != current_section:
            lines.append(f"--- {entry.section_hint.upper()} ---")
            current_section = entry.section_hint
        
        # Format entry
        label_parts = []
        if entry.primary_label:
            label_parts.append(entry.primary_label)
        if entry.secondary_label:
            label_parts.append(f"({entry.secondary_label})")
        if entry.href_short:
            label_parts.append(f"→{entry.href_short}")
        if entry.state_hint:
            label_parts.append(f"[{entry.state_hint}]")
            
        label = " ".join(label_parts) if label_parts else f"<{entry.tag}>"
        lines.append(f"[{entry.index}] {entry.role}: {label}")
    
    lines.append("")
    lines.append("Use index=N to target elements (e.g., index=0, index=5)")
    
    return "\n".join(lines)

def resolve_index_to_selectors(catalog: ElementCatalog, index: int) -> List[str]:
    """Resolve index to robust selectors for execution."""
    if index not in catalog.full_view:
        raise ValueError(f"Index {index} not found in catalog")
    
    element_info = catalog.full_view[index]
    return element_info.robust_selectors