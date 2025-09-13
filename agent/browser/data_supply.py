"""
Browser automation data supply stack implementation.

This module provides 4 data formats for LLM browser automation:
1. IDX-Text v1 (indexed text + index_map)
2. AX-Slim v1 (accessibility-focused extraction)
3. DOM-Lite v1 (minimal hierarchical JSON)
4. VIS-ROI v1 (screenshot + OCR + DOM linking)

Uses Chrome DevTools Protocol (CDP) as primary with Playwright integration.
"""

from __future__ import annotations

import json
import time
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, field
import base64

from playwright.async_api import Page, CDPSession
from ..utils.ocr import OCRProcessor, MockOCRProcessor
from ..utils.content_extractor import ContentExtractor, MockContentExtractor

log = logging.getLogger(__name__)


@dataclass
class StableNodeRef:
    """Stable reference to a DOM/AX node across sessions."""
    frame_id: str
    backend_node_id: Optional[int] = None
    ax_node_id: Optional[str] = None
    css_selector: Optional[str] = None
    
    def to_string(self) -> str:
        """Convert to stable reference string format."""
        if self.backend_node_id:
            return f"{self.frame_id}:BN-{self.backend_node_id}"
        elif self.ax_node_id:
            return f"{self.frame_id}:AX-{self.ax_node_id}"
        else:
            return f"{self.frame_id}:CSS-{self.css_selector}"
    
    @classmethod
    def from_string(cls, ref_str: str) -> Optional['StableNodeRef']:
        """Parse stable reference string."""
        try:
            frame_part, node_part = ref_str.split(':', 1)
            if node_part.startswith('BN-'):
                return cls(frame_part, backend_node_id=int(node_part[3:]))
            elif node_part.startswith('AX-'):
                return cls(frame_part, ax_node_id=node_part[3:])
            elif node_part.startswith('CSS-'):
                return cls(frame_part, css_selector=node_part[4:])
        except (ValueError, IndexError):
            pass
        return None


@dataclass
class IDXTextEntry:
    """Entry in IDX-Text format."""
    index: int
    text: str
    tag: str
    attributes: Dict[str, str] = field(default_factory=dict)
    stable_ref: Optional[StableNodeRef] = None


@dataclass
class IDXTextResult:
    """IDX-Text v1 format result."""
    meta: Dict[str, Any]
    text: str
    index_map: Dict[str, Dict[str, Any]]


@dataclass
class AXSlimNode:
    """AX-Slim v1 node."""
    ax_id: str
    role: str
    name: str
    value: str = ""
    backend_node_id: Optional[int] = None
    visible: bool = False
    bbox: List[int] = field(default_factory=list)


@dataclass
class AXSlimResult:
    """AX-Slim v1 format result."""
    root_name: str
    ax_nodes: List[AXSlimNode]


@dataclass
class DOMLiteNode:
    """DOM-Lite v1 node."""
    id: str
    tag: str
    role: str = ""
    attrs: Dict[str, str] = field(default_factory=dict)
    text: str = ""
    bbox: List[int] = field(default_factory=list)
    clickable: bool = False
    backend_node_id: Optional[int] = None


@dataclass
class DOMLiteResult:
    """DOM-Lite v1 format result."""
    ver: str = "1.0"
    frame: str = "F0"
    nodes: List[DOMLiteNode] = field(default_factory=list)


@dataclass
class OCRResult:
    """OCR result with DOM linking."""
    text: str
    bbox: List[int]
    conf: float
    link_backend_node_id: Optional[int] = None


@dataclass
class VISROIResult:
    """VIS-ROI v1 format result."""
    image: Dict[str, Any]
    ocr: List[OCRResult] = field(default_factory=list)


@dataclass
class ExtractionMetrics:
    """Metrics for data extraction operations."""
    nodes_sent: int = 0
    tokens_estimated: int = 0
    diff_bytes: int = 0
    roi_hits: int = 0
    extraction_time_ms: int = 0


class BrowserDataSupply:
    """Main class for browser automation data supply."""
    
    def __init__(self, page: Page):
        self.page = page
        self.cdp_session: Optional[CDPSession] = None
        self.last_snapshot: Optional[Dict] = None
        self.metrics = ExtractionMetrics()
        self._frame_id_counter = 0
        
        # Initialize OCR and content extraction
        try:
            self.ocr_processor = OCRProcessor()
        except Exception:
            self.ocr_processor = MockOCRProcessor()
            
        try:
            self.content_extractor = ContentExtractor()
        except Exception:
            self.content_extractor = MockContentExtractor()
        
    async def initialize(self) -> None:
        """Initialize CDP session and enable required domains."""
        try:
            self.cdp_session = await self.page.context.new_cdp_session(self.page)
            
            # Enable required CDP domains
            await self.cdp_session.send("DOM.enable")
            await self.cdp_session.send("Accessibility.enable")
            await self.cdp_session.send("Page.enable")
            await self.cdp_session.send("Runtime.enable")
            
            log.info("CDP session initialized successfully")
        except Exception as e:
            log.error(f"Failed to initialize CDP session: {e}")
            raise
    
    async def get_frame_id(self) -> str:
        """Get current frame ID."""
        try:
            if self.cdp_session:
                frame_tree = await self.cdp_session.send("Page.getFrameTree")
                return frame_tree.get("frameTree", {}).get("frame", {}).get("id", "F0")
        except Exception:
            pass
        return "F0"
    
    async def extract_idx_text(self, viewport_only: bool = True) -> IDXTextResult:
        """Extract IDX-Text v1 format."""
        start_time = time.time()
        
        try:
            # Get viewport dimensions
            viewport = await self.page.viewport_size()
            viewport_rect = [0, 0, viewport['width'], viewport['height']] if viewport else [0, 0, 1400, 900]
            
            # Get DOM snapshot via CDP
            if not self.cdp_session:
                await self.initialize()
            
            dom_snapshot = await self.cdp_session.send("DOMSnapshot.captureSnapshot", {
                "computedStyles": [],
                "includeDOMRects": True,
                "includePaintOrder": False
            })
            
            # Process DOM nodes to create indexed text
            entries = []
            index_map = {}
            counter = 0
            
            frame_id = await self.get_frame_id()
            
            # Extract interactive elements with text content
            for i, node in enumerate(dom_snapshot.get("documents", [{}])[0].get("nodes", [])):
                node_name = node.get("nodeName", "").lower()
                if node_name in ["#text", "#comment"]:
                    continue
                
                # Get node attributes
                attrs = {}
                attr_names = dom_snapshot.get("documents", [{}])[0].get("strings", [])
                if "attributes" in node:
                    attr_indices = node["attributes"]
                    for j in range(0, len(attr_indices), 2):
                        if j + 1 < len(attr_indices):
                            key_idx = attr_indices[j]
                            val_idx = attr_indices[j + 1]
                            if key_idx < len(attr_names) and val_idx < len(attr_names):
                                attrs[attr_names[key_idx]] = attr_names[val_idx]
                
                # Check if element is interactive
                is_interactive = (
                    node_name in ["input", "button", "a", "select", "textarea"] or
                    attrs.get("role") in ["button", "link", "textbox", "checkbox", "radio"] or
                    "onclick" in attrs or
                    attrs.get("tabindex") is not None
                )
                
                if is_interactive:
                    # Get text content
                    text_content = attrs.get("aria-label", attrs.get("placeholder", attrs.get("value", node_name)))
                    if "textContent" in node:
                        text_content = node["textContent"]
                    
                    # Create stable reference
                    backend_node_id = node.get("backendNodeId")
                    stable_ref = StableNodeRef(
                        frame_id=frame_id,
                        backend_node_id=backend_node_id,
                        css_selector=self._generate_css_selector(node_name, attrs)
                    )
                    
                    entry = IDXTextEntry(
                        index=counter,
                        text=text_content or node_name,
                        tag=node_name,
                        attributes=attrs,
                        stable_ref=stable_ref
                    )
                    entries.append(entry)
                    
                    # Add to index map
                    index_map[str(counter)] = {
                        "frameId": frame_id,
                        "backendNodeId": backend_node_id,
                        "css": stable_ref.css_selector
                    }
                    counter += 1
            
            # Generate text representation
            text_lines = [f"# viewport: {viewport_rect}"]
            
            for entry in entries:
                attrs_str = " ".join(f'{k}="{v}"' for k, v in entry.attributes.items() if k in ["id", "class", "role", "aria-label"])
                text_lines.append(f"  [{entry.index}] <{entry.tag}{' ' + attrs_str if attrs_str else ''} text=\"{entry.text}\">")
            
            result = IDXTextResult(
                meta={
                    "viewport": viewport_rect,
                    "ts": datetime.now(timezone.utc).isoformat()
                },
                text="\n".join(text_lines),
                index_map=index_map
            )
            
            # Update metrics
            self.metrics.nodes_sent = len(entries)
            self.metrics.tokens_estimated = len(result.text) // 4  # Rough token estimate
            self.metrics.extraction_time_ms = int((time.time() - start_time) * 1000)
            
            return result
            
        except Exception as e:
            log.error(f"IDX-Text extraction failed: {e}")
            return IDXTextResult(
                meta={"viewport": [0, 0, 1400, 900], "ts": datetime.now(timezone.utc).isoformat()},
                text="# viewport: [0,0,1400,900]\n# Error: Failed to extract DOM",
                index_map={}
            )
    
    def _generate_css_selector(self, tag: str, attrs: Dict[str, str]) -> str:
        """Generate CSS selector for element."""
        if "id" in attrs:
            return f"#{attrs['id']}"
        
        selector = tag
        if "class" in attrs:
            classes = attrs["class"].strip().split()
            if classes:
                selector += "." + ".".join(classes)
        
        # Add attribute selectors for better specificity
        for attr in ["role", "type", "name"]:
            if attr in attrs:
                selector += f'[{attr}="{attrs[attr]}"]'
        
        return selector
    
    async def extract_ax_slim(self) -> AXSlimResult:
        """Extract AX-Slim v1 format."""
        try:
            if not self.cdp_session:
                await self.initialize()
            
            # Get accessibility tree
            ax_tree = await self.cdp_session.send("Accessibility.getFullAXTree")
            
            # Get page title for root name
            root_name = await self.page.title() or "Untitled Page"
            
            ax_nodes = []
            
            for node in ax_tree.get("nodes", []):
                # Only include interactive/focusable nodes
                role = node.get("role", {}).get("value", "")
                if not role or role in ["generic", "text", "staticText"]:
                    continue
                
                # Extract node properties
                name = ""
                value = ""
                
                for prop in node.get("properties", []):
                    prop_name = prop.get("name", "")
                    if prop_name == "name":
                        name = prop.get("value", {}).get("value", "")
                    elif prop_name == "value":
                        value = prop.get("value", {}).get("value", "")
                
                # Get backend node ID if available
                backend_node_id = node.get("backendDOMNodeId")
                
                # Check visibility (simplified)
                visible = not node.get("ignored", False)
                
                # Get bounding box if available
                bbox = []
                if "boundingRect" in node:
                    rect = node["boundingRect"]
                    bbox = [
                        int(rect.get("x", 0)),
                        int(rect.get("y", 0)),
                        int(rect.get("x", 0) + rect.get("width", 0)),
                        int(rect.get("y", 0) + rect.get("height", 0))
                    ]
                
                ax_node = AXSlimNode(
                    ax_id=node.get("nodeId", f"AX-{len(ax_nodes)}"),
                    role=role,
                    name=name,
                    value=value,
                    backend_node_id=backend_node_id,
                    visible=visible,
                    bbox=bbox
                )
                ax_nodes.append(ax_node)
            
            return AXSlimResult(
                root_name=root_name,
                ax_nodes=ax_nodes
            )
            
        except Exception as e:
            log.error(f"AX-Slim extraction failed: {e}")
            return AXSlimResult(
                root_name="Error - Failed to extract accessibility tree",
                ax_nodes=[]
            )
    
    async def extract_dom_lite(self) -> DOMLiteResult:
        """Extract DOM-Lite v1 format."""
        try:
            if not self.cdp_session:
                await self.initialize()
            
            # Get DOM snapshot with computed styles
            dom_snapshot = await self.cdp_session.send("DOMSnapshot.captureSnapshot", {
                "computedStyles": ["display", "visibility", "pointer-events"],
                "includeDOMRects": True
            })
            
            frame_id = await self.get_frame_id()
            nodes = []
            
            doc = dom_snapshot.get("documents", [{}])[0]
            dom_nodes = doc.get("nodes", [])
            strings = doc.get("strings", [])
            layout_tree = doc.get("layout", [])
            computed_styles = doc.get("computedStyles", [])
            
            for i, node in enumerate(dom_nodes):
                node_name = node.get("nodeName", "").lower()
                if node_name in ["#text", "#comment", "script", "style"]:
                    continue
                
                # Get attributes
                attrs = {}
                if "attributes" in node:
                    attr_indices = node["attributes"]
                    for j in range(0, len(attr_indices), 2):
                        if j + 1 < len(attr_indices):
                            key_idx = attr_indices[j]
                            val_idx = attr_indices[j + 1]
                            if key_idx < len(strings) and val_idx < len(strings):
                                attr_name = strings[key_idx]
                                if attr_name in ["id", "name", "class", "role", "aria-label", "value", "placeholder", "type"]:
                                    attrs[attr_name] = strings[val_idx]
                
                # Get role
                role = attrs.get("role", "")
                if not role:
                    role_map = {
                        "button": "button", "input": "textbox", "a": "link",
                        "select": "combobox", "textarea": "textbox"
                    }
                    role = role_map.get(node_name, "")
                
                # Get text content
                text_content = node.get("textContent", "").strip()[:100]  # Limit text length
                
                # Get bounding box
                bbox = []
                if i < len(layout_tree) and layout_tree[i]:
                    layout = layout_tree[i]
                    if "boundingBox" in layout:
                        bb = layout["boundingBox"]
                        bbox = [
                            int(bb.get("x", 0)),
                            int(bb.get("y", 0)),
                            int(bb.get("x", 0) + bb.get("width", 0)),
                            int(bb.get("y", 0) + bb.get("height", 0))
                        ]
                
                # Determine if clickable
                clickable = (
                    node_name in ["button", "a", "input", "select"] or
                    attrs.get("role") in ["button", "link"] or
                    "onclick" in attrs
                )
                
                # Check visibility from computed styles
                if i < len(computed_styles) and computed_styles[i]:
                    styles = computed_styles[i]
                    if len(styles) >= 3:  # display, visibility, pointer-events
                        display = strings[styles[0]] if styles[0] < len(strings) else ""
                        visibility = strings[styles[1]] if styles[1] < len(strings) else ""
                        if display == "none" or visibility == "hidden":
                            continue  # Skip hidden elements
                
                dom_node = DOMLiteNode(
                    id=f"N{len(nodes)}",
                    tag=node_name,
                    role=role,
                    attrs=attrs,
                    text=text_content,
                    bbox=bbox,
                    clickable=clickable,
                    backend_node_id=node.get("backendNodeId")
                )
                nodes.append(dom_node)
            
            return DOMLiteResult(
                ver="1.0",
                frame=frame_id,
                nodes=nodes
            )
            
        except Exception as e:
            log.error(f"DOM-Lite extraction failed: {e}")
            return DOMLiteResult(ver="1.0", frame="F0", nodes=[])
    
    async def extract_vis_roi(self, clip_region: Optional[Dict] = None) -> VISROIResult:
        """Extract VIS-ROI v1 format."""
        try:
            # Capture screenshot
            screenshot_options = {"type": "png"}
            if clip_region:
                screenshot_options["clip"] = clip_region
            
            screenshot_bytes = await self.page.screenshot(**screenshot_options)
            
            # Create image metadata
            image_id = f"S-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            image_info = {
                "id": image_id,
                "format": "png", 
                "byte_len": len(screenshot_bytes)
            }
            
            # Perform OCR
            ocr_results_raw = await self.ocr_processor.extract_text_from_image(screenshot_bytes)
            
            # Get DOM nodes for linking
            dom_lite = await self.extract_dom_lite()
            dom_nodes = [
                {
                    "backend_node_id": node.backend_node_id,
                    "bbox": node.bbox,
                    "text": node.text
                }
                for node in dom_lite.nodes
                if node.backend_node_id and node.bbox
            ]
            
            # Link OCR results to DOM
            linked_ocr_results = await self.ocr_processor.link_ocr_to_dom(ocr_results_raw, dom_nodes)
            
            # Convert to VIS-ROI format
            ocr_results = []
            for ocr_result in linked_ocr_results:
                vis_ocr = OCRResult(
                    text=ocr_result.text,
                    bbox=ocr_result.bbox,
                    conf=ocr_result.confidence,
                    link_backend_node_id=ocr_result.link_backend_node_id
                )
                ocr_results.append(vis_ocr)
            
            return VISROIResult(
                image=image_info,
                ocr=ocr_results
            )
            
        except Exception as e:
            log.error(f"VIS-ROI extraction failed: {e}")
            return VISROIResult(
                image={"id": "error", "format": "png", "byte_len": 0},
                ocr=[]
            )
    
    async def detect_changes(self, current_snapshot: Dict) -> Dict:
        """Detect changes from last snapshot for differential updates."""
        if not self.last_snapshot:
            self.last_snapshot = current_snapshot
            return {"type": "full", "data": current_snapshot}
        
        # Simple diff implementation - in production, use more sophisticated diffing
        changes = {}
        
        # Compare node counts
        old_count = len(self.last_snapshot.get("nodes", []))
        new_count = len(current_snapshot.get("nodes", []))
        
        if abs(new_count - old_count) > 5:  # Significant change threshold
            changes["type"] = "major"
            changes["data"] = current_snapshot
        else:
            changes["type"] = "minor" 
            changes["data"] = {"incremental": True}  # Simplified diff
        
        self.last_snapshot = current_snapshot
        return changes


class DataSupplyManager:
    """High-level manager for all data supply operations."""
    
    def __init__(self, page: Page):
        self.page = page
        self.data_supply = BrowserDataSupply(page)
        self.initialized = False
    
    async def initialize(self) -> None:
        """Initialize the data supply system."""
        if not self.initialized:
            await self.data_supply.initialize()
            self.initialized = True
    
    async def get_all_formats(self, include_screenshot: bool = False, include_content: bool = False) -> Dict[str, Any]:
        """Get all 4 data formats in one call."""
        if not self.initialized:
            await self.initialize()
        
        results = {}
        
        try:
            # Extract all formats
            results["idx_text"] = await self.data_supply.extract_idx_text()
            results["ax_slim"] = await self.data_supply.extract_ax_slim()
            results["dom_lite"] = await self.data_supply.extract_dom_lite()
            
            if include_screenshot:
                results["vis_roi"] = await self.data_supply.extract_vis_roi()
            
            # Extract content if requested and page looks like an article
            if include_content:
                try:
                    is_article = await self.data_supply.content_extractor.is_article_page(self.page)
                    if is_article:
                        content = await self.data_supply.content_extractor.extract_content(self.page)
                        results["extracted_content"] = content
                except Exception as e:
                    log.warning(f"Content extraction failed: {e}")
            
            # Include metrics
            results["metrics"] = self.data_supply.metrics
            
        except Exception as e:
            log.error(f"Failed to extract all formats: {e}")
            results["error"] = str(e)
        
        return results
    
    async def validate_target(self, target_ref: str) -> Tuple[bool, Optional[StableNodeRef]]:
        """Validate that a target reference exists and is actionable."""
        stable_ref = StableNodeRef.from_string(target_ref)
        if not stable_ref:
            return False, None
        
        try:
            if not self.initialized:
                await self.initialize()
            
            # Check if node exists via CDP
            if stable_ref.backend_node_id:
                try:
                    await self.data_supply.cdp_session.send("DOM.describeNode", {
                        "backendNodeId": stable_ref.backend_node_id
                    })
                    return True, stable_ref
                except Exception:
                    return False, stable_ref
            
            # Fallback to CSS selector check
            if stable_ref.css_selector:
                try:
                    element = await self.page.query_selector(stable_ref.css_selector)
                    return element is not None, stable_ref
                except Exception:
                    return False, stable_ref
                    
        except Exception as e:
            log.error(f"Target validation failed: {e}")
            return False, stable_ref
        
        return False, stable_ref