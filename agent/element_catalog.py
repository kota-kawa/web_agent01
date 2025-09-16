"""
Element Catalog Generator for Browser Use Style Index-Based Element Selection

This module provides the observation phase functionality that extracts operational
elements from web pages and assigns index numbers (0,1,2,...) for stable targeting.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)


@dataclass
class ElementCatalogEntry:
    """Single element entry in the catalog with both short and complete view data."""
    
    # Core identification
    index: int
    tag: str
    
    # Short view (for LLM presentation) - limited character counts
    role: Optional[str] = None
    primary_label: Optional[str] = None  # <=60 chars
    secondary_label: Optional[str] = None  # <=40 chars
    section_hint: Optional[str] = None
    state_hint: Optional[str] = None  # disabled/selected/checked etc
    href_short: Optional[str] = None  # shortened URL
    
    # Complete view (for execution engine)
    robust_selectors: List[str] = field(default_factory=list)
    bbox: Dict[str, float] = field(default_factory=dict)  # {x, y, width, height}
    visible: bool = True
    disabled: bool = False
    href_full: Optional[str] = None
    dom_path_hash: Optional[str] = None
    nearest_texts: List[str] = field(default_factory=list)
    section_id: Optional[str] = None
    
    # Element attributes for debugging
    element_id: Optional[str] = None
    element_classes: Optional[str] = None
    data_testid: Optional[str] = None


@dataclass  
class ElementCatalog:
    """Complete element catalog with versioning and metadata."""
    
    # Catalog metadata
    catalog_version: str
    url: str
    title: str
    viewport_size: Dict[str, int] = field(default_factory=dict)
    
    # Element collections
    entries: List[ElementCatalogEntry] = field(default_factory=list)
    
    # Index mapping for fast lookup
    index_map: Dict[int, ElementCatalogEntry] = field(default_factory=dict)
    
    # Summary stats
    total_elements: int = 0
    sections_detected: int = 0
    
    def get_short_view(self) -> List[Dict[str, Any]]:
        """Get short view suitable for LLM prompt inclusion."""
        return [
            {
                'index': entry.index,
                'tag': entry.tag,
                'role': entry.role,
                'primary_label': entry.primary_label,
                'secondary_label': entry.secondary_label,
                'section_hint': entry.section_hint,
                'state_hint': entry.state_hint,
                'href_short': entry.href_short,
            }
            for entry in self.entries
        ]
    
    def get_element_by_index(self, index: int) -> Optional[ElementCatalogEntry]:
        """Get element entry by index number."""
        return self.index_map.get(index)
    
    def get_robust_selectors(self, index: int) -> List[str]:
        """Get robust selectors for an element by index."""
        entry = self.get_element_by_index(index)
        return entry.robust_selectors if entry else []


class ElementCatalogGenerator:
    """Generator for creating element catalogs from web pages."""
    
    # Interactive element selectors
    INTERACTIVE_SELECTORS = [
        'a[href]',
        'button',
        'input[type!="hidden"]',
        'select', 
        'textarea',
        'summary',
        '[contenteditable="true"]',
        '[role="button"]',
        '[role="link"]', 
        '[role="tab"]',
        '[role="menuitem"]',
        '[role="option"]',
        '[tabindex]:not([tabindex="-1"])'
    ]
    
    # Section/landmark selectors for grouping
    SECTION_SELECTORS = [
        'main', '[role="main"]',
        'nav', '[role="navigation"]', 
        'header', '[role="banner"]',
        'footer', '[role="contentinfo"]',
        'aside', '[role="complementary"]',
        'section', '[role="region"]',
        'article',
        'form'
    ]

    def __init__(self, page=None):
        """Initialize with optional Playwright page instance."""
        self.page = page
    
    async def generate_catalog(self, page=None) -> ElementCatalog:
        """Generate complete element catalog from current page."""
        if page:
            self.page = page
        if not self.page:
            raise ValueError("Page instance required for catalog generation")
        
        # Get page metadata
        url = await self.page.url()
        title = await self.page.title()
        viewport = await self.page.viewport_size()
        
        # Extract elements
        elements_data = await self._extract_elements()
        
        # Process and sort elements
        entries = await self._process_elements(elements_data)
        
        # Generate catalog version
        catalog_version = await self._generate_catalog_version(url, elements_data, viewport)
        
        # Create catalog
        catalog = ElementCatalog(
            catalog_version=catalog_version,
            url=url,
            title=title,
            viewport_size=viewport,
            entries=entries,
            total_elements=len(entries),
            sections_detected=len(set(e.section_id for e in entries if e.section_id))
        )
        
        # Build index map
        catalog.index_map = {entry.index: entry for entry in entries}
        
        log.info(f"Generated catalog v{catalog_version} with {len(entries)} elements")
        return catalog
    
    async def _extract_elements(self) -> List[Dict[str, Any]]:
        """Extract interactive elements using browser-side JavaScript."""
        
        extraction_script = """
        () => {
            const results = [];
            
            // Interactive element selectors
            const interactiveSelectors = [
                'a[href]', 'button', 'input[type!="hidden"]', 'select', 'textarea', 
                'summary', '[contenteditable="true"]', '[role="button"]', '[role="link"]',
                '[role="tab"]', '[role="menuitem"]', '[role="option"]', 
                '[tabindex]:not([tabindex="-1"])'
            ];
            
            // Section selectors for grouping
            const sectionSelectors = [
                'main', '[role="main"]', 'nav', '[role="navigation"]', 'header', 
                '[role="banner"]', 'footer', '[role="contentinfo"]', 'aside', 
                '[role="complementary"]', 'section', '[role="region"]', 'article', 'form'
            ];
            
            // Helper functions
            function isVisible(el) {
                if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            }
            
            function generateSelector(el) {
                const selectors = [];
                
                // 1. getByRole equivalent
                const role = el.getAttribute('role') || el.tagName.toLowerCase();
                if (role && el.textContent && el.textContent.trim()) {
                    const text = el.textContent.trim().substring(0, 30);
                    selectors.push(`css=[role="${role}"]:has-text("${text}")`);
                }
                
                // 2. Text locators
                if (el.textContent && el.textContent.trim()) {
                    const text = el.textContent.trim().substring(0, 50);
                    selectors.push(`text="${text}"`);
                }
                
                // 3. ID and data-testid
                if (el.id) {
                    selectors.push(`css=#${el.id}`);
                }
                if (el.dataset.testid) {
                    selectors.push(`css=[data-testid="${el.dataset.testid}"]`);
                }
                
                // 4. Relative CSS selectors
                if (el.className) {
                    const classes = el.className.trim().split(/\\s+/).slice(0, 2).join('.');
                    if (classes) {
                        selectors.push(`css=${el.tagName.toLowerCase()}.${classes}`);
                    }
                }
                
                // 5. XPath as fallback
                function getXPath(element) {
                    if (element === document.body) return '/html/body';
                    let xpath = '';
                    for (; element && element.nodeType == Node.ELEMENT_NODE; element = element.parentNode) {
                        let idx = 1;
                        for (let sibling = element.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
                            if (sibling.tagName === element.tagName) idx++;
                        }
                        xpath = '/' + element.tagName.toLowerCase() + '[' + idx + ']' + xpath;
                    }
                    return xpath;
                }
                selectors.push(`xpath=${getXPath(el)}`);
                
                return selectors;
            }
            
            function findNearestSection(el) {
                let current = el.parentElement;
                while (current && current !== document.body) {
                    for (const selector of sectionSelectors) {
                        if (current.matches(selector)) {
                            return {
                                id: current.id || current.tagName.toLowerCase(),
                                hint: current.tagName.toLowerCase()
                            };
                        }
                    }
                    current = current.parentElement;
                }
                return null;
            }
            
            function getNearestTexts(el) {
                const texts = [];
                
                // Check siblings for relevant text
                if (el.parentElement) {
                    for (const sibling of el.parentElement.children) {
                        if (sibling !== el && sibling.textContent && sibling.textContent.trim()) {
                            const text = sibling.textContent.trim().substring(0, 30);
                            if (text.length > 2) texts.push(text);
                        }
                    }
                }
                
                return texts.slice(0, 3); // Limit to 3 nearest texts
            }
            
            // Find all interactive elements
            const allElements = [];
            for (const selector of interactiveSelectors) {
                try {
                    const elements = document.querySelectorAll(selector);
                    allElements.push(...elements);
                } catch (e) {
                    console.warn('Selector failed:', selector, e);
                }
            }
            
            // Remove duplicates and process
            const uniqueElements = [...new Set(allElements)];
            
            for (const el of uniqueElements) {
                if (!isVisible(el)) continue;
                
                const rect = el.getBoundingClientRect();
                const section = findNearestSection(el);
                
                // Primary and secondary labels
                let primaryLabel = '';
                let secondaryLabel = '';
                
                // Try different label sources
                const labelSources = [
                    el.getAttribute('aria-label'),
                    el.getAttribute('title'),
                    el.getAttribute('placeholder'),
                    el.getAttribute('alt'),
                    el.textContent?.trim(),
                    el.value
                ].filter(Boolean);
                
                if (labelSources.length > 0) {
                    primaryLabel = labelSources[0].substring(0, 60);
                    if (labelSources.length > 1) {
                        secondaryLabel = labelSources[1].substring(0, 40);
                    }
                }
                
                // State hints
                const states = [];
                if (el.disabled) states.push('disabled');
                if (el.checked) states.push('checked');
                if (el.selected) states.push('selected');
                if (el.getAttribute('aria-expanded') === 'true') states.push('expanded');
                
                // Short href
                let hrefShort = null;
                if (el.href) {
                    const url = new URL(el.href);
                    hrefShort = url.pathname + (url.search ? '?' : '') + url.search.substring(0, 20);
                    if (hrefShort.length > 30) hrefShort = hrefShort.substring(0, 27) + '...';
                }
                
                results.push({
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role'),
                    primaryLabel,
                    secondaryLabel,
                    sectionHint: section?.hint,
                    sectionId: section?.id,
                    stateHint: states.join(',') || null,
                    hrefShort,
                    robustSelectors: generateSelector(el),
                    bbox: {
                        x: rect.x,
                        y: rect.y, 
                        width: rect.width,
                        height: rect.height
                    },
                    visible: true,
                    disabled: el.disabled || false,
                    hrefFull: el.href || null,
                    nearestTexts: getNearestTexts(el),
                    elementId: el.id || null,
                    elementClasses: el.className || null,
                    dataTestid: el.dataset.testid || null
                });
            }
            
            return results;
        }
        """
        
        try:
            elements_data = await self.page.evaluate(extraction_script)
            log.debug(f"Extracted {len(elements_data)} interactive elements")
            return elements_data
        except Exception as e:
            log.error(f"Failed to extract elements: {e}")
            return []
    
    async def _process_elements(self, elements_data: List[Dict[str, Any]]) -> List[ElementCatalogEntry]:
        """Process raw elements data into catalog entries with stable ordering."""
        entries = []
        
        # Sort elements by position (top to bottom, left to right)
        sorted_elements = sorted(elements_data, key=lambda el: (
            el['bbox']['y'],  # Top first
            el['bbox']['x']   # Left first within same vertical position
        ))
        
        # Group by sections and assign indices
        current_section = None
        section_counter = 0
        
        for i, el_data in enumerate(sorted_elements):
            # Detect section changes
            if el_data.get('sectionId') != current_section:
                current_section = el_data.get('sectionId')
                section_counter += 1
            
            # Generate DOM path hash for stable identification
            dom_path_hash = self._generate_dom_path_hash(el_data)
            
            entry = ElementCatalogEntry(
                index=i,
                tag=el_data['tag'],
                role=el_data.get('role'),
                primary_label=el_data.get('primaryLabel'),
                secondary_label=el_data.get('secondaryLabel'),
                section_hint=el_data.get('sectionHint'),
                state_hint=el_data.get('stateHint'),
                href_short=el_data.get('hrefShort'),
                robust_selectors=el_data.get('robustSelectors', []),
                bbox=el_data.get('bbox', {}),
                visible=el_data.get('visible', True),
                disabled=el_data.get('disabled', False),
                href_full=el_data.get('hrefFull'),
                dom_path_hash=dom_path_hash,
                nearest_texts=el_data.get('nearestTexts', []),
                section_id=el_data.get('sectionId'),
                element_id=el_data.get('elementId'),
                element_classes=el_data.get('elementClasses'),
                data_testid=el_data.get('dataTestid')
            )
            
            entries.append(entry)
        
        log.debug(f"Processed {len(entries)} catalog entries with {section_counter} sections")
        return entries
    
    def _generate_dom_path_hash(self, el_data: Dict[str, Any]) -> str:
        """Generate a hash for stable element identification across DOM changes."""
        # Use tag, position, and key attributes to create a stable hash
        hash_input = f"{el_data['tag']}|{el_data.get('elementId', '')}|{el_data.get('dataTestid', '')}|{el_data.get('primaryLabel', '')}"
        return hashlib.md5(hash_input.encode()).hexdigest()[:8]
    
    async def _generate_catalog_version(self, url: str, elements_data: List[Dict], viewport: Dict) -> str:
        """Generate catalog version based on URL, DOM state, and viewport."""
        # Create hash from URL, elements, and viewport
        dom_hash = hashlib.md5(str(elements_data).encode()).hexdigest()[:8]
        viewport_hash = hashlib.md5(str(viewport).encode()).hexdigest()[:4] 
        url_hash = hashlib.md5(url.encode()).hexdigest()[:4]
        
        version = f"{url_hash}{dom_hash}{viewport_hash}"
        log.debug(f"Generated catalog version: {version}")
        return version


# Utility functions for integration with existing code
async def generate_element_catalog(page) -> ElementCatalog:
    """Convenience function to generate element catalog from a Playwright page."""
    generator = ElementCatalogGenerator(page)
    return await generator.generate_catalog()


def format_catalog_for_llm(catalog: ElementCatalog, max_elements: int = 50) -> str:
    """Format catalog short view for inclusion in LLM prompts."""
    if not catalog.entries:
        return "No interactive elements found on the page."
    
    lines = [f"=== Element Catalog (v{catalog.catalog_version}) ==="]
    lines.append(f"Page: {catalog.title} ({catalog.url})")
    lines.append(f"Found {len(catalog.entries)} interactive elements")
    lines.append("")
    
    # Show up to max_elements
    for entry in catalog.entries[:max_elements]:
        parts = [f"[{entry.index}]", f"<{entry.tag}>"]
        
        if entry.role:
            parts.append(f"role={entry.role}")
        
        if entry.primary_label:
            parts.append(f'"{entry.primary_label}"')
        
        if entry.secondary_label:
            parts.append(f"({entry.secondary_label})")
        
        if entry.state_hint:
            parts.append(f"|{entry.state_hint}|")
        
        if entry.href_short:
            parts.append(f"→{entry.href_short}")
        
        if entry.section_hint:
            parts.append(f"in:{entry.section_hint}")
        
        lines.append(" ".join(parts))
    
    if len(catalog.entries) > max_elements:
        lines.append(f"... and {len(catalog.entries) - max_elements} more elements")
    
    return "\n".join(lines)