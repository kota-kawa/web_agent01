"""
OCR integration for VIS-ROI format extraction.

Provides text extraction from screenshots with DOM element linking.
"""

from __future__ import annotations

import logging
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass
import base64
import io

try:
    import easyocr
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    easyocr = None

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    Image = None

log = logging.getLogger(__name__)


@dataclass
class OCRResult:
    """Result from OCR processing."""
    text: str
    bbox: List[int]  # [x1, y1, x2, y2]
    confidence: float
    link_backend_node_id: Optional[int] = None


class OCRProcessor:
    """OCR processor for screenshot text extraction."""
    
    def __init__(self, languages: List[str] = None):
        self.languages = languages or ['en', 'ja']  # English and Japanese
        self.reader = None
        self._initialize_ocr()
    
    def _initialize_ocr(self) -> None:
        """Initialize OCR engine."""
        if not OCR_AVAILABLE:
            log.warning("EasyOCR not available. Install with: pip install easyocr")
            return
        
        if not PIL_AVAILABLE:
            log.warning("PIL not available. Install with: pip install Pillow")
            return
        
        try:
            self.reader = easyocr.Reader(self.languages, gpu=False)
            log.info(f"OCR initialized with languages: {self.languages}")
        except Exception as e:
            log.error(f"Failed to initialize OCR: {e}")
            self.reader = None
    
    def is_available(self) -> bool:
        """Check if OCR is available."""
        return self.reader is not None
    
    async def extract_text_from_image(self, image_bytes: bytes) -> List[OCRResult]:
        """Extract text from image bytes."""
        if not self.is_available():
            log.warning("OCR not available")
            return []
        
        try:
            # Convert bytes to PIL Image
            image = Image.open(io.BytesIO(image_bytes))
            
            # Perform OCR
            results = self.reader.readtext(image)
            
            ocr_results = []
            for result in results:
                # EasyOCR returns: (bbox_points, text, confidence)
                bbox_points, text, confidence = result
                
                # Convert bbox points to [x1, y1, x2, y2] format
                x_coords = [point[0] for point in bbox_points]
                y_coords = [point[1] for point in bbox_points]
                
                bbox = [
                    int(min(x_coords)),
                    int(min(y_coords)),
                    int(max(x_coords)),
                    int(max(y_coords))
                ]
                
                ocr_result = OCRResult(
                    text=text.strip(),
                    bbox=bbox,
                    confidence=confidence
                )
                ocr_results.append(ocr_result)
            
            log.info(f"OCR extracted {len(ocr_results)} text regions")
            return ocr_results
            
        except Exception as e:
            log.error(f"OCR extraction failed: {e}")
            return []
    
    async def link_ocr_to_dom(self, ocr_results: List[OCRResult], dom_nodes: List[Dict]) -> List[OCRResult]:
        """Link OCR results to DOM nodes based on position and text similarity."""
        if not ocr_results or not dom_nodes:
            return ocr_results
        
        linked_results = []
        
        for ocr_result in ocr_results:
            best_match = None
            best_score = 0.0
            
            for node in dom_nodes:
                # Check if node has position information
                node_bbox = node.get("bbox", [])
                if len(node_bbox) < 4:
                    continue
                
                # Calculate overlap between OCR result and DOM node
                overlap_score = self._calculate_bbox_overlap(ocr_result.bbox, node_bbox)
                
                # Calculate text similarity
                node_text = node.get("text", "").strip()
                text_score = self._calculate_text_similarity(ocr_result.text, node_text)
                
                # Combined score (weighted)
                combined_score = (overlap_score * 0.7) + (text_score * 0.3)
                
                if combined_score > best_score and combined_score > 0.5:  # Threshold
                    best_score = combined_score
                    best_match = node
            
            # Create linked result
            linked_result = OCRResult(
                text=ocr_result.text,
                bbox=ocr_result.bbox,
                confidence=ocr_result.confidence,
                link_backend_node_id=best_match.get("backend_node_id") if best_match else None
            )
            linked_results.append(linked_result)
        
        return linked_results
    
    def _calculate_bbox_overlap(self, bbox1: List[int], bbox2: List[int]) -> float:
        """Calculate overlap ratio between two bounding boxes."""
        try:
            # Calculate intersection
            x1 = max(bbox1[0], bbox2[0])
            y1 = max(bbox1[1], bbox2[1])
            x2 = min(bbox1[2], bbox2[2])
            y2 = min(bbox1[3], bbox2[3])
            
            if x2 <= x1 or y2 <= y1:
                return 0.0  # No overlap
            
            intersection_area = (x2 - x1) * (y2 - y1)
            
            # Calculate areas
            area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
            area2 = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
            
            # Calculate union
            union_area = area1 + area2 - intersection_area
            
            return intersection_area / union_area if union_area > 0 else 0.0
            
        except Exception:
            return 0.0
    
    def _calculate_text_similarity(self, text1: str, text2: str) -> float:
        """Calculate text similarity using simple methods."""
        if not text1 or not text2:
            return 0.0
        
        text1 = text1.lower().strip()
        text2 = text2.lower().strip()
        
        if text1 == text2:
            return 1.0
        
        # Check if one text contains the other
        if text1 in text2 or text2 in text1:
            return 0.8
        
        # Simple word overlap
        words1 = set(text1.split())
        words2 = set(text2.split())
        
        if not words1 or not words2:
            return 0.0
        
        overlap = len(words1.intersection(words2))
        total = len(words1.union(words2))
        
        return overlap / total if total > 0 else 0.0


class MockOCRProcessor(OCRProcessor):
    """Mock OCR processor for testing when EasyOCR is not available."""
    
    def __init__(self, languages: List[str] = None):
        self.languages = languages or ['en', 'ja']
        self.reader = "mock"  # Indicate mock mode
    
    def is_available(self) -> bool:
        return True
    
    async def extract_text_from_image(self, image_bytes: bytes) -> List[OCRResult]:
        """Mock OCR extraction."""
        # Return some mock OCR results for testing
        return [
            OCRResult(
                text="Mock OCR Text 1",
                bbox=[100, 100, 200, 130],
                confidence=0.95
            ),
            OCRResult(
                text="Mock OCR Text 2",
                bbox=[250, 150, 350, 180],
                confidence=0.87
            )
        ]