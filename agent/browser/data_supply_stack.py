"""
Browser Operation Agent Data Supply Stack

This module implements the core data extraction system that provides 4 data formats
for LLM-based web automation:
1. IDX-Text v1 (indexed text + index_map)
2. AX-Slim v1 (accessibility-focused extraction)
3. DOM-Lite v1 (minimal hierarchical JSON)
4. VIS-ROI v1 (screenshot + OCR + DOM links)

Uses Chrome DevTools Protocol (CDP) for stable references and comprehensive data extraction.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Union

import pychrome
import requests
from PIL import Image
from playwright.async_api import async_playwright, Page, Browser, BrowserContext
from readabilipy import simple_json_from_html_string

try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class ReferenceId:
    """Stable reference ID for DOM elements."""
    frame_id: str
    backend_node_id: Optional[int] = None
    ax_node_id: Optional[str] = None
    
    def __str__(self) -> str:
        if self.backend_node_id:
            return f"{self.frame_id}:BN-{self.backend_node_id}"
        elif self.ax_node_id:
            return f"{self.frame_id}:AX-{self.ax_node_id}"
        else:
            return f"{self.frame_id}:UNKNOWN"
    
    @classmethod
    def from_string(cls, ref_str: str) -> "ReferenceId":
        """Parse reference ID from string format."""
        parts = ref_str.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid reference ID format: {ref_str}")
        
        frame_id, node_part = parts
        if node_part.startswith("BN-"):
            backend_node_id = int(node_part[3:])
            return cls(frame_id, backend_node_id=backend_node_id)
        elif node_part.startswith("AX-"):
            ax_node_id = node_part[3:]
            return cls(frame_id, ax_node_id=ax_node_id)
        else:
            raise ValueError(f"Invalid node reference format: {node_part}")


@dataclass
class ExtractionMetrics:
    """Metrics for extraction operations."""
    nodes_sent: int = 0
    tokens_estimated: int = 0
    diff_bytes: int = 0
    roi_hits: int = 0
    click_success_rate: float = 0.0
    retry_count: int = 0
    not_found_rate: float = 0.0
    extraction_time_ms: float = 0.0


@dataclass
class IDXTextFormat:
    """IDX-Text v1 format data structure."""
    meta: Dict[str, Any]
    text: str
    index_map: Dict[str, Dict[str, Any]]


@dataclass
class AXSlimFormat:
    """AX-Slim v1 format data structure."""
    root_name: str
    ax_nodes: List[Dict[str, Any]]


@dataclass 
class DOMNodeLite:
    """Single node in DOM-Lite format."""
    id: str
    tag: str
    role: Optional[str]
    attrs: Dict[str, str]
    text: str
    bbox: List[int]
    clickable: bool
    backend_node_id: int


@dataclass
class DOMLiteFormat:
    """DOM-Lite v1 format data structure."""
    ver: str
    frame: str
    nodes: List[DOMNodeLite]


@dataclass
class VISROIFormat:
    """VIS-ROI v1 format data structure."""
    image: Dict[str, Any]
    ocr: List[Dict[str, Any]]


class DataSupplyStack:
    """Main data extraction engine using CDP and Playwright."""
    
    def __init__(self, debug_port: int = 9222):
        self.debug_port = debug_port
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.cdp_client: Optional[pychrome.Browser] = None
        self.cdp_tab: Optional[pychrome.Tab] = None
        self.frame_id_map: Dict[str, str] = {}
        self.last_snapshot: Optional[Dict[str, Any]] = None
        self.metrics = ExtractionMetrics()
        
    async def initialize(self) -> None:
        """Initialize the browser and CDP connection."""
        # Start Playwright browser with CDP enabled
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(
            headless=True,
            args=[f"--remote-debugging-port={self.debug_port}"]
        )
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        
        # Connect to CDP
        await asyncio.sleep(1)  # Wait for CDP to be ready
        try:
            self.cdp_client = pychrome.Browser(url=f"http://127.0.0.1:{self.debug_port}")
            tabs = self.cdp_client.list_tab()
            if tabs:
                self.cdp_tab = tabs[0]
                self.cdp_tab.start()
                
                # Enable required CDP domains
                await self._enable_cdp_domains()
        except Exception as e:
            logger.warning(f"CDP connection failed: {e}. Some features may be limited.")
    
    async def _enable_cdp_domains(self) -> None:
        """Enable required CDP domains for stable references."""
        if not self.cdp_tab:
            return
            
        try:
            # Enable Accessibility domain for AXNodeId
            self.cdp_tab.Accessibility.enable()
            
            # Enable DOM domain for backendNodeId
            self.cdp_tab.DOM.enable()
            
            # Enable Page domain for screenshots
            self.cdp_tab.Page.enable()
            
            # Get frame tree to map frame IDs
            frame_tree = self.cdp_tab.Page.getFrameTree()
            self._map_frame_ids(frame_tree.get("frameTree", {}))
            
        except Exception as e:
            logger.error(f"Failed to enable CDP domains: {e}")
    
    def _map_frame_ids(self, frame_tree: Dict[str, Any], parent_id: str = "F0") -> None:
        """Map frame IDs for stable references."""
        frame = frame_tree.get("frame", {})
        frame_id = frame.get("id", "")
        
        if frame_id:
            self.frame_id_map[frame_id] = parent_id
        
        # Process child frames
        for i, child_frame in enumerate(frame_tree.get("childFrames", [])):
            child_id = f"{parent_id}_C{i}"
            self._map_frame_ids(child_frame, child_id)
    
    async def navigate(self, url: str) -> None:
        """Navigate to a URL."""
        if self.page:
            await self.page.goto(url)
            await self.page.wait_for_load_state("networkidle")
    
    async def extract_all_formats(self, staged: bool = True) -> Dict[str, Any]:
        """Extract all 4 data formats."""
        start_time = time.time()
        
        try:
            # Get viewport info
            viewport = await self.page.viewport_size() if self.page else {"width": 1400, "height": 900}
            viewport_rect = [0, 0, viewport["width"], viewport["height"]]
            
            # Extract formats
            idx_text = await self.extract_idx_text(viewport_rect, staged)
            ax_slim = await self.extract_ax_slim()
            dom_lite = await self.extract_dom_lite()
            vis_roi = await self.extract_vis_roi(viewport_rect)
            
            # Update metrics
            self.metrics.extraction_time_ms = (time.time() - start_time) * 1000
            
            return {
                "idx_text": idx_text,
                "ax_slim": ax_slim,
                "dom_lite": dom_lite,
                "vis_roi": vis_roi,
                "metrics": self.metrics
            }
            
        except Exception as e:
            logger.error(f"Format extraction failed: {e}")
            raise
    
    async def extract_idx_text(self, viewport: List[int], staged: bool = True) -> IDXTextFormat:
        """Extract IDX-Text v1 format."""
        if not self.page:
            raise RuntimeError("Page not initialized")
        
        # Get DOM structure with stable references
        dom_data = await self._get_dom_with_references()
        
        # Build indexed text representation
        text_lines = [f"# viewport: {viewport}"]
        index_map = {}
        node_counter = 0
        
        def process_node(node: Dict[str, Any], depth: int = 0) -> None:
            nonlocal node_counter
            
            if node.get("nodeType") == 1:  # Element node
                tag = node.get("nodeName", "").lower()
                attrs = self._get_element_attributes(node)
                
                # Check if this is an interactive element
                if self._is_interactive_element(node, attrs):
                    indent = "  " * depth
                    attr_str = self._format_attributes(attrs)
                    text_content = self._get_text_content(node)
                    
                    line = f"{indent}[{node_counter}] <{tag}{attr_str}>"
                    if text_content:
                        line += f" text=\"{text_content[:50]}\""
                    
                    text_lines.append(line)
                    
                    # Add to index map with stable reference
                    backend_node_id = node.get("backendNodeId")
                    if backend_node_id:
                        frame_id = self._get_frame_id_for_node(node)
                        css_selector = self._generate_css_selector(node, attrs)
                        
                        index_map[str(node_counter)] = {
                            "frameId": frame_id,
                            "backendNodeId": backend_node_id,
                            "css": css_selector
                        }
                    
                    node_counter += 1
                
                # Process children
                for child in node.get("children", []):
                    process_node(child, depth + 1)
        
        # Process all DOM nodes
        for node in dom_data:
            process_node(node)
        
        self.metrics.nodes_sent = len(index_map)
        self.metrics.tokens_estimated = len("\n".join(text_lines)) // 4  # Rough estimate
        
        return IDXTextFormat(
            meta={
                "viewport": viewport,
                "ts": datetime.utcnow().isoformat() + "Z"
            },
            text="\n".join(text_lines),
            index_map=index_map
        )
    
    async def extract_ax_slim(self) -> AXSlimFormat:
        """Extract AX-Slim v1 format using CDP Accessibility domain."""
        if not self.cdp_tab:
            # Fallback to Playwright accessibility tree
            return await self._extract_ax_slim_playwright()
        
        try:
            # Get accessibility tree from CDP
            ax_tree = self.cdp_tab.Accessibility.getFullAXTree()
            root_name = await self.page.title() if self.page else "Unknown"
            
            ax_nodes = []
            
            def process_ax_node(node: Dict[str, Any]) -> None:
                role = node.get("role", {}).get("value", "")
                name = node.get("name", {}).get("value", "")
                value = node.get("value", {}).get("value", "")
                
                # Only include interactive elements
                if self._is_interactive_role(role):
                    ax_id = node.get("nodeId", "")
                    backend_node_id = node.get("backendDOMNodeId")
                    
                    # Get bounding box
                    bbox = self._get_ax_node_bbox(node)
                    
                    # Check visibility
                    visible = not node.get("ignored", False) and bbox != [0, 0, 0, 0]
                    
                    if visible:
                        ax_nodes.append({
                            "axId": f"AX-{ax_id}",
                            "role": role,
                            "name": name,
                            "value": value,
                            "backendNodeId": backend_node_id,
                            "visible": visible,
                            "bbox": bbox
                        })
                
                # Process children
                for child in node.get("children", []):
                    process_ax_node(child)
            
            # Process root nodes
            for node in ax_tree.get("nodes", []):
                process_ax_node(node)
            
            return AXSlimFormat(
                root_name=root_name,
                ax_nodes=ax_nodes
            )
            
        except Exception as e:
            logger.error(f"CDP AX extraction failed: {e}")
            return await self._extract_ax_slim_playwright()
    
    async def _extract_ax_slim_playwright(self) -> AXSlimFormat:
        """Fallback AX extraction using Playwright."""
        if not self.page:
            raise RuntimeError("Page not initialized")
        
        root_name = await self.page.title()
        
        # Get accessibility snapshot
        ax_snapshot = await self.page.accessibility.snapshot()
        ax_nodes = []
        
        def process_playwright_ax_node(node: Dict[str, Any]) -> None:
            role = node.get("role", "")
            name = node.get("name", "")
            value = node.get("value", "")
            
            if self._is_interactive_role(role):
                # Estimate backend node ID (not available in Playwright snapshot)
                estimated_id = hash(f"{role}{name}{value}") % 1000000
                
                ax_nodes.append({
                    "axId": f"AX-{estimated_id}",
                    "role": role,
                    "name": name,
                    "value": value,
                    "backendNodeId": estimated_id,
                    "visible": True,  # Playwright snapshot only includes visible elements
                    "bbox": [0, 0, 0, 0]  # Not available in basic snapshot
                })
            
            # Process children
            for child in node.get("children", []):
                process_playwright_ax_node(child)
        
        if ax_snapshot:
            process_playwright_ax_node(ax_snapshot)
        
        return AXSlimFormat(
            root_name=root_name,
            ax_nodes=ax_nodes
        )
    
    async def extract_dom_lite(self) -> DOMLiteFormat:
        """Extract DOM-Lite v1 format."""
        if not self.page:
            raise RuntimeError("Page not initialized")
        
        # Get DOM nodes with bounding boxes
        dom_data = await self._get_dom_with_boxes()
        
        nodes = []
        node_counter = 0
        
        for dom_node in dom_data:
            if dom_node.get("nodeType") == 1:  # Element node
                tag = dom_node.get("nodeName", "").lower()
                attrs = self._get_element_attributes(dom_node)
                
                # Filter to interactive/important elements
                if self._is_relevant_element(dom_node, attrs):
                    # Get role
                    role = attrs.get("role") or self._infer_role(tag, attrs)
                    
                    # Get bounding box
                    bbox = self._get_element_bbox(dom_node)
                    
                    # Determine if clickable
                    clickable = self._is_clickable_element(dom_node, attrs, bbox)
                    
                    # Get text content
                    text = self._get_text_content(dom_node)
                    
                    # Whitelist attributes
                    filtered_attrs = self._filter_attributes(attrs)
                    
                    backend_node_id = dom_node.get("backendNodeId", node_counter)
                    
                    nodes.append(DOMNodeLite(
                        id=f"N{node_counter}",
                        tag=tag,
                        role=role,
                        attrs=filtered_attrs,
                        text=text,
                        bbox=bbox,
                        clickable=clickable,
                        backend_node_id=backend_node_id
                    ))
                    
                    node_counter += 1
        
        return DOMLiteFormat(
            ver="1.0",
            frame="F0",  # Main frame
            nodes=nodes
        )
    
    async def extract_vis_roi(self, viewport: List[int]) -> VISROIFormat:
        """Extract VIS-ROI v1 format with screenshot and OCR."""
        if not self.page:
            raise RuntimeError("Page not initialized")
        
        # Take screenshot
        screenshot_bytes = await self.page.screenshot(
            full_page=False,  # Viewport only
            type="png"
        )
        
        # Generate image metadata
        image_id = f"S-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        image_data = {
            "id": image_id,
            "format": "png",
            "byte_len": len(screenshot_bytes)
        }
        
        # Perform OCR if available
        ocr_results = []
        if OCR_AVAILABLE:
            try:
                image = Image.open(io.BytesIO(screenshot_bytes))
                ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
                
                # Get DOM elements for linking
                dom_elements = await self._get_dom_with_boxes()
                
                for i, text in enumerate(ocr_data["text"]):
                    if text.strip() and int(ocr_data["conf"][i]) > 50:
                        x = int(ocr_data["left"][i])
                        y = int(ocr_data["top"][i])
                        w = int(ocr_data["width"][i])
                        h = int(ocr_data["height"][i])
                        
                        bbox = [x, y, x + w, y + h]
                        confidence = int(ocr_data["conf"][i]) / 100.0
                        
                        # Try to link to DOM element
                        linked_node_id = self._find_dom_element_by_bbox(dom_elements, bbox)
                        
                        ocr_result = {
                            "text": text.strip(),
                            "bbox": bbox,
                            "conf": confidence
                        }
                        
                        if linked_node_id:
                            ocr_result["link_backendNodeId"] = linked_node_id
                        
                        ocr_results.append(ocr_result)
                        
            except Exception as e:
                logger.error(f"OCR processing failed: {e}")
        
        self.metrics.roi_hits = len(ocr_results)
        
        return VISROIFormat(
            image=image_data,
            ocr=ocr_results
        )
    
    # Helper methods
    async def _get_dom_with_references(self) -> List[Dict[str, Any]]:
        """Get DOM tree with stable backend node IDs."""
        if self.cdp_tab:
            try:
                doc = self.cdp_tab.DOM.getDocument(depth=-1, pierce=True)
                return self._flatten_dom_tree(doc.get("root", {}))
            except Exception as e:
                logger.error(f"CDP DOM access failed: {e}")
        
        # Fallback to Playwright evaluation
        dom_script = """
        () => {
            function serializeNode(node) {
                if (node.nodeType === Node.ELEMENT_NODE) {
                    const attrs = {};
                    for (const attr of node.attributes) {
                        attrs[attr.name] = attr.value;
                    }
                    
                    return {
                        nodeType: node.nodeType,
                        nodeName: node.nodeName,
                        attributes: attrs,
                        children: Array.from(node.childNodes).map(serializeNode),
                        backendNodeId: Math.floor(Math.random() * 1000000) // Simulate ID
                    };
                }
                return null;
            }
            
            return [serializeNode(document.documentElement)].filter(Boolean);
        }
        """
        
        return await self.page.evaluate(dom_script) if self.page else []
    
    async def _get_dom_with_boxes(self) -> List[Dict[str, Any]]:
        """Get DOM elements with bounding box information."""
        dom_data = await self._get_dom_with_references()
        
        # Add bounding box information
        for node in dom_data:
            if node.get("nodeType") == 1:
                node["bbox"] = await self._calculate_element_bbox(node)
        
        return dom_data
    
    async def _calculate_element_bbox(self, node: Dict[str, Any]) -> List[int]:
        """Calculate bounding box for a DOM element."""
        if not self.page:
            return [0, 0, 0, 0]
        
        try:
            # Use CSS selector to find element
            attrs = node.get("attributes", {})
            selector = self._generate_css_selector(node, attrs)
            
            if selector:
                element = await self.page.query_selector(selector)
                if element:
                    box = await element.bounding_box()
                    if box:
                        return [
                            int(box["x"]),
                            int(box["y"]),
                            int(box["x"] + box["width"]),
                            int(box["y"] + box["height"])
                        ]
        except Exception:
            pass
        
        return [0, 0, 0, 0]
    
    def _flatten_dom_tree(self, node: Dict[str, Any], result: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Flatten DOM tree to list of nodes."""
        if result is None:
            result = []
        
        result.append(node)
        
        for child in node.get("children", []):
            self._flatten_dom_tree(child, result)
        
        return result
    
    def _get_element_attributes(self, node: Dict[str, Any]) -> Dict[str, str]:
        """Extract attributes from DOM node."""
        attrs = {}
        
        # CDP format
        if "attributes" in node and isinstance(node["attributes"], list):
            attr_list = node["attributes"]
            for i in range(0, len(attr_list), 2):
                if i + 1 < len(attr_list):
                    attrs[attr_list[i]] = attr_list[i + 1]
        
        # Playwright format
        elif "attributes" in node and isinstance(node["attributes"], dict):
            attrs = node["attributes"]
        
        return attrs
    
    def _is_interactive_element(self, node: Dict[str, Any], attrs: Dict[str, str]) -> bool:
        """Check if element is interactive."""
        tag = node.get("nodeName", "").lower()
        interactive_tags = {"a", "button", "input", "select", "textarea", "option", "summary"}
        
        if tag in interactive_tags:
            return True
        
        role = attrs.get("role", "")
        interactive_roles = {"button", "link", "textbox", "checkbox", "radio", "menuitem", "tab", "switch", "combobox"}
        
        if role in interactive_roles:
            return True
        
        if attrs.get("tabindex", "").isdigit() and int(attrs["tabindex"]) >= 0:
            return True
        
        return attrs.get("contenteditable", "").lower() == "true"
    
    def _is_interactive_role(self, role: str) -> bool:
        """Check if accessibility role is interactive."""
        interactive_roles = {
            "button", "link", "textbox", "checkbox", "radio", "menuitem", 
            "tab", "switch", "combobox", "slider", "spinbutton", "searchbox"
        }
        return role.lower() in interactive_roles
    
    def _is_relevant_element(self, node: Dict[str, Any], attrs: Dict[str, str]) -> bool:
        """Check if element should be included in DOM-Lite."""
        return self._is_interactive_element(node, attrs) or self._has_important_content(node, attrs)
    
    def _has_important_content(self, node: Dict[str, Any], attrs: Dict[str, str]) -> bool:
        """Check if element has important content."""
        tag = node.get("nodeName", "").lower()
        important_tags = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "span", "label"}
        
        if tag in important_tags:
            text = self._get_text_content(node)
            return len(text.strip()) > 0
        
        return False
    
    def _is_clickable_element(self, node: Dict[str, Any], attrs: Dict[str, str], bbox: List[int]) -> bool:
        """Determine if element is clickable."""
        if bbox == [0, 0, 0, 0]:
            return False
        
        tag = node.get("nodeName", "").lower()
        clickable_tags = {"a", "button", "input", "select", "textarea"}
        
        if tag in clickable_tags and attrs.get("disabled") != "true":
            return True
        
        # Check for click handlers (would need DOM access)
        if attrs.get("onclick") or attrs.get("role") in {"button", "link"}:
            return True
        
        return False
    
    def _get_text_content(self, node: Dict[str, Any]) -> str:
        """Extract text content from DOM node."""
        # This is a simplified version - in practice would need recursive text extraction
        return ""  # Placeholder
    
    def _format_attributes(self, attrs: Dict[str, str]) -> str:
        """Format attributes for text representation."""
        important_attrs = ["id", "class", "aria-label", "role", "placeholder", "value"]
        attr_parts = []
        
        for attr in important_attrs:
            if attr in attrs:
                attr_parts.append(f'{attr}="{attrs[attr]}"')
        
        return f" {' '.join(attr_parts)}" if attr_parts else ""
    
    def _generate_css_selector(self, node: Dict[str, Any], attrs: Dict[str, str]) -> str:
        """Generate CSS selector for element."""
        if "id" in attrs:
            return f"#{attrs['id']}"
        
        tag = node.get("nodeName", "").lower()
        
        if "class" in attrs:
            classes = attrs["class"].split()
            if classes:
                return f"{tag}.{'.'.join(classes)}"
        
        # Fallback to tag name
        return tag
    
    def _get_frame_id_for_node(self, node: Dict[str, Any]) -> str:
        """Get frame ID for a DOM node."""
        # For now, assume main frame
        return "F0"
    
    def _filter_attributes(self, attrs: Dict[str, str]) -> Dict[str, str]:
        """Filter attributes to whitelist."""
        whitelist = {"id", "name", "class", "role", "aria-label", "aria-labelledby", 
                    "aria-describedby", "value", "placeholder", "href", "src", "alt", "title"}
        
        return {k: v for k, v in attrs.items() if k in whitelist}
    
    def _infer_role(self, tag: str, attrs: Dict[str, str]) -> Optional[str]:
        """Infer ARIA role from element."""
        role_map = {
            "button": "button",
            "a": "link",
            "input": "textbox",  # Simplified
            "textarea": "textbox",
            "select": "combobox",
            "h1": "heading",
            "h2": "heading",
            "h3": "heading",
            "h4": "heading",
            "h5": "heading",
            "h6": "heading"
        }
        
        return role_map.get(tag)
    
    def _get_element_bbox(self, node: Dict[str, Any]) -> List[int]:
        """Get element bounding box."""
        # Placeholder - would be populated by _calculate_element_bbox
        return node.get("bbox", [0, 0, 0, 0])
    
    def _get_ax_node_bbox(self, ax_node: Dict[str, Any]) -> List[int]:
        """Get bounding box from accessibility node."""
        bound = ax_node.get("bound")
        if bound:
            return [
                int(bound.get("left", 0)),
                int(bound.get("top", 0)),
                int(bound.get("left", 0) + bound.get("width", 0)),
                int(bound.get("top", 0) + bound.get("height", 0))
            ]
        return [0, 0, 0, 0]
    
    def _find_dom_element_by_bbox(self, dom_elements: List[Dict[str, Any]], bbox: List[int]) -> Optional[int]:
        """Find DOM element that matches OCR text bounding box."""
        target_x, target_y, target_x2, target_y2 = bbox
        
        for element in dom_elements:
            if element.get("nodeType") == 1:  # Element node
                elem_bbox = element.get("bbox", [0, 0, 0, 0])
                ex, ey, ex2, ey2 = elem_bbox
                
                # Check for overlap
                if (target_x < ex2 and target_x2 > ex and 
                    target_y < ey2 and target_y2 > ey):
                    return element.get("backendNodeId")
        
        return None
    
    async def close(self) -> None:
        """Clean up resources."""
        if self.cdp_tab:
            try:
                self.cdp_tab.stop()
            except:
                pass
        
        if self.context:
            await self.context.close()
        
        if self.browser:
            await self.browser.close()