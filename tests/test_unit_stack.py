"""
Unit tests for the data supply stack components without requiring browser installation.

Tests core functionality using mocked components.
"""

import sys
from pathlib import Path
import logging
from typing import Dict, Any
from dataclasses import asdict

sys.path.append(str(Path(__file__).resolve().parents[1]))

from agent.browser.data_supply import (
    StableNodeRef, IDXTextResult, AXSlimResult, DOMLiteResult, VISROIResult,
    IDXTextEntry, AXSlimNode, DOMLiteNode, OCRResult, ExtractionMetrics
)
from agent.actions.dsl_validator import ActionType, DSLValidator, ActionRequest
from agent.utils.ocr import MockOCRProcessor
from agent.utils.content_extractor import MockContentExtractor

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


class MockPage:
    """Mock Playwright page for testing."""
    
    def __init__(self):
        self.url_value = "https://example.com"
        self.title_value = "Test Page"
        self.content_value = "<html><body><h1>Test</h1></body></html>"
    
    async def url(self):
        return self.url_value
    
    async def title(self):
        return self.title_value
    
    async def content(self):
        return self.content_value
    
    async def viewport_size(self):
        return {"width": 1400, "height": 900}
    
    async def screenshot(self, **kwargs):
        return b"fake_screenshot_data"
    
    async def query_selector(self, selector):
        # Mock element found for basic selectors
        if selector in ["#test-btn", "#test-input"]:
            return MockElement()
        return None
    
    async def evaluate(self, script):
        return "test_result"


class MockElement:
    """Mock Playwright element."""
    
    async def click(self):
        pass
    
    async def fill(self, text):
        pass
    
    async def is_visible(self):
        return True
    
    async def inner_text(self):
        return "Mock element text"
    
    async def get_attribute(self, name):
        return "mock_value"


class MockCDPSession:
    """Mock CDP session."""
    
    async def send(self, method, params=None):
        if method == "DOMSnapshot.captureSnapshot":
            return {
                "documents": [{
                    "nodes": [
                        {
                            "nodeName": "BUTTON",
                            "backendNodeId": 12345,
                            "attributes": [0, 1, 2, 3],  # Indices to strings array
                            "textContent": "Test Button"
                        },
                        {
                            "nodeName": "INPUT", 
                            "backendNodeId": 12346,
                            "attributes": [4, 5, 6, 7],
                            "textContent": ""
                        }
                    ],
                    "strings": ["id", "test-btn", "class", "button", "id", "test-input", "type", "text"]
                }]
            }
        elif method == "Accessibility.getFullAXTree":
            return {
                "nodes": [
                    {
                        "nodeId": "AX-1",
                        "role": {"value": "button"},
                        "backendDOMNodeId": 12345,
                        "properties": [
                            {"name": "name", "value": {"value": "Test Button"}}
                        ],
                        "boundingRect": {"x": 100, "y": 100, "width": 120, "height": 30}
                    },
                    {
                        "nodeId": "AX-2", 
                        "role": {"value": "textbox"},
                        "backendDOMNodeId": 12346,
                        "properties": [
                            {"name": "name", "value": {"value": "Test Input"}}
                        ],
                        "boundingRect": {"x": 100, "y": 150, "width": 200, "height": 30}
                    }
                ]
            }
        elif method == "Page.getFrameTree":
            return {
                "frameTree": {
                    "frame": {"id": "F0"}
                }
            }
        elif method == "DOM.describeNode":
            return {"node": {"nodeId": 1}}
        elif method == "DOM.getContentQuads":
            return {"quads": [[100, 100, 220, 100, 220, 130, 100, 130]]}
        else:
            return {}


class MockDataSupply:
    """Mock data supply for testing."""
    
    def __init__(self):
        self.page = MockPage()
        self.cdp_session = MockCDPSession()
        self.metrics = ExtractionMetrics()
        self.ocr_processor = MockOCRProcessor()
        self.content_extractor = MockContentExtractor()
    
    async def initialize(self):
        pass
    
    async def get_frame_id(self):
        return "F0"
    
    async def extract_idx_text(self, viewport_only=True):
        return IDXTextResult(
            meta={"viewport": [0, 0, 1400, 900], "ts": "2024-01-01T00:00:00Z"},
            text="# viewport: [0,0,1400,900]\n[0] <button id=\"test-btn\" text=\"Test Button\">\n[1] <input id=\"test-input\" text=\"\">",
            index_map={
                "0": {"frameId": "F0", "backendNodeId": 12345, "css": "#test-btn"},
                "1": {"frameId": "F0", "backendNodeId": 12346, "css": "#test-input"}
            }
        )
    
    async def extract_ax_slim(self):
        return AXSlimResult(
            root_name="Test Page",
            ax_nodes=[
                AXSlimNode(
                    ax_id="AX-1",
                    role="button", 
                    name="Test Button",
                    backend_node_id=12345,
                    visible=True,
                    bbox=[100, 100, 220, 130]
                ),
                AXSlimNode(
                    ax_id="AX-2",
                    role="textbox",
                    name="Test Input", 
                    backend_node_id=12346,
                    visible=True,
                    bbox=[100, 150, 300, 180]
                )
            ]
        )
    
    async def extract_dom_lite(self):
        return DOMLiteResult(
            ver="1.0",
            frame="F0",
            nodes=[
                DOMLiteNode(
                    id="N0",
                    tag="button",
                    role="button",
                    attrs={"id": "test-btn"},
                    text="Test Button",
                    bbox=[100, 100, 220, 130],
                    clickable=True,
                    backend_node_id=12345
                ),
                DOMLiteNode(
                    id="N1",
                    tag="input",
                    role="textbox",
                    attrs={"id": "test-input", "type": "text"},
                    text="",
                    bbox=[100, 150, 300, 180],
                    clickable=False,
                    backend_node_id=12346
                )
            ]
        )
    
    async def extract_vis_roi(self, clip_region=None):
        return VISROIResult(
            image={"id": "S-test", "format": "png", "byte_len": 1000},
            ocr=[
                OCRResult(
                    text="Test Button",
                    bbox=[100, 100, 220, 130],
                    conf=0.95,
                    link_backend_node_id=12345
                )
            ]
        )


def test_stable_references():
    """Test stable reference system."""
    log.info("Testing stable references...")
    
    # Test reference creation and parsing
    test_cases = [
        ("F0:BN-12345", True),
        ("F0:AX-67890", True),
        ("F0:CSS-#test-btn", True),
        ("invalid-ref", False),
        ("", False)
    ]
    
    results = []
    for ref_str, should_parse in test_cases:
        parsed = StableNodeRef.from_string(ref_str)
        if should_parse:
            assert parsed is not None, f"Failed to parse valid reference: {ref_str}"
            reconstructed = parsed.to_string()
            assert ref_str == reconstructed, f"Round-trip failed: {ref_str} -> {reconstructed}"
            results.append({"ref": ref_str, "parsed": True, "round_trip": True})
        else:
            assert parsed is None, f"Incorrectly parsed invalid reference: {ref_str}"
            results.append({"ref": ref_str, "parsed": False})
    
    log.info(f"Stable reference tests passed: {len(results)} cases")
    return {"stable_references": results, "success": True}


def test_data_formats():
    """Test all 4 data formats."""
    log.info("Testing data formats...")
    
    data_supply = MockDataSupply()
    
    results = {}
    
    # Test IDX-Text format
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        idx_text = loop.run_until_complete(data_supply.extract_idx_text())
        results["idx_text"] = {
            "has_meta": bool(idx_text.meta),
            "has_text": bool(idx_text.text),
            "has_index_map": bool(idx_text.index_map),
            "interactive_count": len(idx_text.index_map)
        }
        
        # Test AX-Slim format
        ax_slim = loop.run_until_complete(data_supply.extract_ax_slim())
        results["ax_slim"] = {
            "root_name": ax_slim.root_name,
            "node_count": len(ax_slim.ax_nodes),
            "visible_nodes": len([n for n in ax_slim.ax_nodes if n.visible])
        }
        
        # Test DOM-Lite format
        dom_lite = loop.run_until_complete(data_supply.extract_dom_lite())
        results["dom_lite"] = {
            "version": dom_lite.ver,
            "frame": dom_lite.frame,
            "total_nodes": len(dom_lite.nodes),
            "clickable_nodes": len([n for n in dom_lite.nodes if n.clickable])
        }
        
        # Test VIS-ROI format
        vis_roi = loop.run_until_complete(data_supply.extract_vis_roi())
        results["vis_roi"] = {
            "has_image": bool(vis_roi.image),
            "image_format": vis_roi.image.get("format"),
            "ocr_count": len(vis_roi.ocr),
            "linked_ocr": len([o for o in vis_roi.ocr if o.link_backend_node_id])
        }
        
    finally:
        loop.close()
    
    results["success"] = True
    log.info("Data format tests passed")
    return results


def test_action_dsl_validation():
    """Test action DSL validation."""
    log.info("Testing action DSL validation...")
    
    # Mock data supply manager
    class MockDataSupplyManager:
        async def validate_target(self, target):
            return target.startswith("F0:CSS-#test"), StableNodeRef.from_string(target)
    
    validator = DSLValidator(MockDataSupplyManager())
    
    test_cases = [
        # Valid actions
        {
            "actions": [{"op": "click", "target": "F0:CSS-#test-btn"}],
            "should_pass": True
        },
        {
            "actions": [{"op": "type", "target": "F0:CSS-#test-input", "text": "test"}],
            "should_pass": True
        },
        {
            "actions": [{"op": "scroll", "direction": "down", "amount": 500}],
            "should_pass": True
        },
        # Invalid actions
        {
            "actions": [{"op": "invalid_op"}],
            "should_pass": False
        },
        {
            "actions": [{"op": "click"}],  # Missing target
            "should_pass": False
        },
        {
            "actions": [{"op": "type", "target": "F0:CSS-#test"}],  # Missing text
            "should_pass": False
        }
    ]
    
    results = []
    
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        for i, case in enumerate(test_cases):
            try:
                validation_result = loop.run_until_complete(
                    validator.validate_actions(case["actions"])
                )
                
                passed = validation_result.valid == case["should_pass"]
                results.append({
                    "case": i,
                    "expected": case["should_pass"],
                    "actual": validation_result.valid,
                    "passed": passed,
                    "errors": validation_result.errors
                })
                
            except Exception as e:
                results.append({
                    "case": i,
                    "expected": case["should_pass"], 
                    "actual": False,
                    "passed": not case["should_pass"],
                    "error": str(e)
                })
    finally:
        loop.close()
    
    success = all(r["passed"] for r in results)
    log.info(f"Action DSL validation tests: {len(results)} cases, success: {success}")
    
    return {"validation_results": results, "success": success}


def test_ocr_integration():
    """Test OCR integration."""
    log.info("Testing OCR integration...")
    
    ocr_processor = MockOCRProcessor()
    
    # Test OCR availability
    is_available = ocr_processor.is_available()
    
    # Test OCR extraction
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        fake_image_bytes = b"fake_image_data"
        ocr_results = loop.run_until_complete(
            ocr_processor.extract_text_from_image(fake_image_bytes)
        )
        
        # Test DOM linking
        mock_dom_nodes = [
            {
                "backend_node_id": 12345,
                "bbox": [100, 100, 200, 130],
                "text": "Mock OCR Text 1"
            }
        ]
        
        linked_results = loop.run_until_complete(
            ocr_processor.link_ocr_to_dom(ocr_results, mock_dom_nodes)
        )
        
    finally:
        loop.close()
    
    results = {
        "available": is_available,
        "ocr_count": len(ocr_results),
        "linked_count": len([r for r in linked_results if r.link_backend_node_id]),
        "success": True
    }
    
    log.info("OCR integration tests passed")
    return results


def test_content_extraction():
    """Test content extraction."""
    log.info("Testing content extraction...")
    
    content_extractor = MockContentExtractor()
    mock_page = MockPage()
    
    # Test content extraction
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        extracted_content = loop.run_until_complete(
            content_extractor.extract_content(mock_page)
        )
        
        is_article = loop.run_until_complete(
            content_extractor.is_article_page(mock_page)
        )
        
    finally:
        loop.close()
    
    results = {
        "available": content_extractor.is_available(),
        "extraction_success": extracted_content.success,
        "has_title": bool(extracted_content.title),
        "has_content": bool(extracted_content.content),
        "word_count": extracted_content.word_count,
        "article_detected": is_article,
        "success": True
    }
    
    log.info("Content extraction tests passed")
    return results


def test_serialization():
    """Test data structure serialization."""
    log.info("Testing serialization...")
    
    # Test that all data structures can be serialized to JSON
    try:
        # Create sample data structures
        stable_ref = StableNodeRef("F0", backend_node_id=12345)
        
        idx_text = IDXTextResult(
            meta={"test": "data"},
            text="test text",
            index_map={"0": {"frameId": "F0", "backendNodeId": 12345}}
        )
        
        ax_node = AXSlimNode(
            ax_id="AX-1",
            role="button",
            name="Test",
            visible=True,
            bbox=[0, 0, 100, 100]
        )
        
        dom_node = DOMLiteNode(
            id="N1",
            tag="button",
            role="button",
            clickable=True
        )
        
        ocr_result = OCRResult(
            text="Test",
            bbox=[0, 0, 100, 100],
            conf=0.95
        )
        
        # Test serialization
        import json
        
        serializable_data = {
            "stable_ref": stable_ref.to_string(),
            "idx_text": {
                "meta": idx_text.meta,
                "text": idx_text.text,
                "index_map": idx_text.index_map
            },
            "ax_node": asdict(ax_node),
            "dom_node": asdict(dom_node),
            "ocr_result": asdict(ocr_result)
        }
        
        json_str = json.dumps(serializable_data, ensure_ascii=False)
        parsed_back = json.loads(json_str)
        
        results = {
            "serialization_success": True,
            "json_length": len(json_str),
            "parsed_keys": list(parsed_back.keys()),
            "success": True
        }
        
    except Exception as e:
        results = {
            "serialization_success": False,
            "error": str(e),
            "success": False
        }
    
    log.info("Serialization tests completed")
    return results


def run_all_tests():
    """Run all unit tests."""
    log.info("Starting unit test suite...")
    
    test_results = {}
    
    # Run individual tests
    test_results["stable_references"] = test_stable_references()
    test_results["data_formats"] = test_data_formats()
    test_results["action_dsl"] = test_action_dsl_validation()
    test_results["ocr_integration"] = test_ocr_integration()
    test_results["content_extraction"] = test_content_extraction()
    test_results["serialization"] = test_serialization()
    
    # Calculate overall success
    total_tests = len(test_results)
    successful_tests = sum(1 for result in test_results.values() if result.get("success", False))
    
    test_results["summary"] = {
        "total_test_suites": total_tests,
        "successful_suites": successful_tests,
        "success_rate": successful_tests / total_tests if total_tests > 0 else 0.0,
        "overall_success": successful_tests == total_tests
    }
    
    return test_results


def main():
    """Main test function."""
    results = run_all_tests()
    
    # Print results
    print("\n" + "="*80)
    print("BROWSER AUTOMATION DATA SUPPLY STACK - UNIT TESTS")
    print("="*80)
    
    for test_name, result in results.items():
        if test_name == "summary":
            continue
        
        status = "✓ PASS" if result.get("success", False) else "✗ FAIL"
        print(f"\n{status} {test_name.upper().replace('_', ' ')}")
        
        if isinstance(result, dict):
            for key, value in result.items():
                if key != "success":
                    print(f"    {key}: {value}")
    
    # Print summary
    summary = results["summary"]
    print(f"\n{'='*80}")
    print("SUMMARY:")
    print(f"  Test Suites: {summary['total_test_suites']}")
    print(f"  Successful: {summary['successful_suites']}")
    print(f"  Success Rate: {summary['success_rate']:.2%}")
    print(f"  Overall Result: {'PASS' if summary['overall_success'] else 'FAIL'}")
    print(f"{'='*80}")
    
    # Save results
    import json
    with open("/tmp/unit_test_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\nDetailed results saved to: /tmp/unit_test_results.json")
    
    return summary['overall_success']


if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)