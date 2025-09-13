#!/usr/bin/env python3
"""
Browser Operation Agent - Demo Script

Demonstrates the complete data supply stack with all 4 formats and action processing.
Shows practical usage without requiring browser installation.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
import sys

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent))

from agent.browser.data_supply_stack import (
    DataSupplyStack, ReferenceId, IDXTextFormat, AXSlimFormat, 
    DOMLiteFormat, VISROIFormat, ExtractionMetrics
)
from agent.browser.action_processor import ActionProcessor, ActionCommand, ActionRequest
from agent.browser.browser_agent import BrowserOperationAgent, BrowserAgentConfig

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def demo_reference_system():
    """Demonstrate the stable reference ID system."""
    print("=== Reference ID System Demo ===")
    
    # Create reference IDs
    ref1 = ReferenceId("F0", backend_node_id=812345)
    ref2 = ReferenceId("F1", ax_node_id="123")  # Just the ID part, not "AX-123"
    
    print(f"Backend Node Reference: {ref1}")
    print(f"Accessibility Reference: {ref2}")
    
    # Parse from strings
    parsed1 = ReferenceId.from_string("F0:BN-812345")
    parsed2 = ReferenceId.from_string("F1:AX-123")
    
    print(f"Parsed Backend: {parsed1} (matches: {str(ref1) == str(parsed1)})")
    print(f"Parsed AX: {parsed2} (matches: {str(ref2) == str(parsed2)})")
    print()


def demo_data_formats():
    """Demonstrate all 4 data format structures."""
    print("=== Data Format Structures Demo ===")
    
    # IDX-Text v1 format
    idx_text = IDXTextFormat(
        meta={"viewport": [0, 0, 1400, 900], "ts": datetime.utcnow().isoformat() + "Z"},
        text="# viewport: [0,0,1400,900]\n[0] <div id=\"main\" class=\"container\" aria-label=\"検索フォーム\">\n  [1] <h1 text=\"商品検索\">\n  [2] <input id=\"query\" role=\"textbox\" placeholder=\"キーワードを入力\" value=\"\">\n  [3] <button role=\"button\" text=\"検索\">",
        index_map={
            "2": {"frameId": "F0", "backendNodeId": 812345, "css": "#query"},
            "3": {"frameId": "F0", "backendNodeId": 812346, "css": "button"}
        }
    )
    
    print("1. IDX-Text v1 Format:")
    print(f"   Meta: {idx_text.meta}")
    print(f"   Index Map: {len(idx_text.index_map)} interactive elements")
    print(f"   Text Preview: {idx_text.text[:80]}...")
    print()
    
    # AX-Slim v1 format
    ax_slim = AXSlimFormat(
        root_name="商品検索 - example.com",
        ax_nodes=[
            {"axId": "AX-10", "role": "textbox", "name": "検索語", "value": "", 
             "backendNodeId": 812345, "visible": True, "bbox": [320, 180, 760, 210]},
            {"axId": "AX-11", "role": "button", "name": "検索", 
             "backendNodeId": 812346, "visible": True, "bbox": [760, 180, 840, 210]}
        ]
    )
    
    print("2. AX-Slim v1 Format:")
    print(f"   Root: {ax_slim.root_name}")
    print(f"   Interactive Nodes: {len(ax_slim.ax_nodes)}")
    for node in ax_slim.ax_nodes:
        print(f"     {node['axId']}: {node['role']} '{node['name']}'")
    print()
    
    # DOM-Lite v1 format  
    dom_lite = DOMLiteFormat(
        ver="1.0",
        frame="F0",
        nodes=[
            {"id": "N2", "tag": "input", "role": "textbox", 
             "attrs": {"id": "query", "placeholder": "キーワードを入力"}, 
             "text": "", "bbox": [320, 180, 760, 210], "clickable": False, 
             "backend_node_id": 812345},
            {"id": "N3", "tag": "button", "role": "button", "attrs": {}, 
             "text": "検索", "bbox": [760, 180, 840, 210], "clickable": True, 
             "backend_node_id": 812346}
        ]
    )
    
    print("3. DOM-Lite v1 Format:")
    print(f"   Version: {dom_lite.ver}, Frame: {dom_lite.frame}")
    print(f"   Nodes: {len(dom_lite.nodes)}")
    for node in dom_lite.nodes:
        print(f"     {node['id']}: <{node['tag']}> clickable={node['clickable']}")
    print()
    
    # VIS-ROI v1 format
    vis_roi = VISROIFormat(
        image={"id": "S-20250113-120000", "format": "png", "byte_len": 482133},
        ocr=[
            {"text": "商品検索", "bbox": [320, 140, 480, 170], "conf": 0.99},
            {"text": "検索", "bbox": [760, 180, 820, 210], "conf": 0.98, 
             "link_backendNodeId": 812346}
        ]
    )
    
    print("4. VIS-ROI v1 Format:")
    print(f"   Image: {vis_roi.image['id']} ({vis_roi.image['byte_len']} bytes)")
    print(f"   OCR Results: {len(vis_roi.ocr)} text regions")
    for ocr in vis_roi.ocr:
        print(f"     '{ocr['text']}' (conf: {ocr['conf']:.2f})")
    print()


def demo_action_dsl():
    """Demonstrate the action DSL system."""
    print("=== Action DSL Demo ===")
    
    # Create action commands
    actions = [
        ActionCommand(op="type", target="F0:BN-812345", text="ノートPC"),
        ActionCommand(op="click", target="F0:BN-812346"),
        ActionCommand(op="scroll", direction="down", amount=800),
        ActionCommand(op="wait", timeout=1000)
    ]
    
    print("Sample Action Commands:")
    for i, action in enumerate(actions, 1):
        print(f"  {i}. {action.op.upper()}: ", end="")
        if action.target:
            print(f"target={action.target}", end="")
        if action.text:
            print(f" text='{action.text}'", end="")
        if action.direction:
            print(f" direction={action.direction}", end="")
        if action.amount:
            print(f" amount={action.amount}", end="")
        if action.timeout:
            print(f" timeout={action.timeout}ms", end="")
        print()
    
    # Create action request
    request = ActionRequest(
        type="act",
        actions=actions
    )
    
    print(f"\nAction Request Type: {request.type}")
    print(f"Actions Count: {len(request.actions)}")
    
    # JSON representation
    request_json = {
        "type": "act",
        "actions": [
            {"op": "type", "target": "F0:BN-812345", "text": "ノートPC"},
            {"op": "click", "target": "F0:BN-812346"},
            {"op": "scroll", "direction": "down", "amount": 800}
        ]
    }
    
    print(f"\nJSON Format:")
    print(json.dumps(request_json, indent=2, ensure_ascii=False))
    print()


def demo_metrics_system():
    """Demonstrate the metrics and logging system."""
    print("=== Metrics System Demo ===")
    
    # Create sample metrics
    metrics = ExtractionMetrics(
        nodes_sent=25,
        tokens_estimated=1200,
        diff_bytes=512,
        roi_hits=8,
        click_success_rate=0.95,
        retry_count=2,
        not_found_rate=0.01,
        extraction_time_ms=850.5
    )
    
    print("Sample Extraction Metrics:")
    print(f"  Nodes Sent: {metrics.nodes_sent}")
    print(f"  Estimated Tokens: {metrics.tokens_estimated}")
    print(f"  Diff Bytes: {metrics.diff_bytes}")
    print(f"  ROI Hits: {metrics.roi_hits}")
    print(f"  Click Success Rate: {metrics.click_success_rate:.1%}")
    print(f"  Retry Count: {metrics.retry_count}")
    print(f"  Not Found Rate: {metrics.not_found_rate:.1%}")
    print(f"  Extraction Time: {metrics.extraction_time_ms:.1f}ms")
    
    # Check acceptance criteria
    acceptance_criteria = {
        "retry_rate_below_1_percent": metrics.retry_count / 100 <= 0.01,
        "not_found_rate_below_2_percent": metrics.not_found_rate <= 0.02,
        "success_rate_above_90_percent": metrics.click_success_rate >= 0.90
    }
    
    print(f"\nAcceptance Criteria Check:")
    for criterion, passed in acceptance_criteria.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {criterion}: {status}")
    
    overall_pass = all(acceptance_criteria.values())
    print(f"\nOverall Status: {'✓ PASSING' if overall_pass else '✗ FAILING'}")
    print()


def demo_site_scenarios():
    """Demonstrate the 3 required site scenarios."""
    print("=== Site Scenario Templates ===")
    
    scenarios = [
        {
            "name": "Search Form",
            "description": "E-commerce product search with input→search→results flow",
            "key_elements": ["search input", "submit button", "results list", "cart link"],
            "typical_actions": ["type query", "click search", "scroll results", "click product"],
            "success_criteria": "Navigate to product details or add to cart"
        },
        {
            "name": "News Article", 
            "description": "Content consumption with Readability extraction",
            "key_elements": ["article title", "author byline", "main content", "related links"],
            "typical_actions": ["extract content", "scroll article", "click related"],
            "success_criteria": "Successfully extract title, author, and full text content"
        },
        {
            "name": "Dashboard",
            "description": "Admin interface with tabs, dropdowns, and data manipulation",
            "key_elements": ["navigation tabs", "data filters", "action buttons", "data tables"],
            "typical_actions": ["switch tabs", "select filters", "scroll data", "export/edit"],
            "success_criteria": "Successfully navigate and interact with all major controls"
        }
    ]
    
    for i, scenario in enumerate(scenarios, 1):
        print(f"{i}. {scenario['name']}:")
        print(f"   Description: {scenario['description']}")
        print(f"   Key Elements: {', '.join(scenario['key_elements'])}")
        print(f"   Typical Actions: {', '.join(scenario['typical_actions'])}")
        print(f"   Success Criteria: {scenario['success_criteria']}")
        print()


async def demo_configuration_options():
    """Demonstrate configuration options."""
    print("=== Configuration Options Demo ===")
    
    # Default configuration
    default_config = BrowserAgentConfig()
    print("Default Configuration:")
    print(f"  Debug Port: {default_config.debug_port}")
    print(f"  Headless: {default_config.headless}")
    print(f"  Viewport: {default_config.viewport_width}x{default_config.viewport_height}")
    print(f"  Staged Extraction: {default_config.staged_extraction}")
    print(f"  Enable OCR: {default_config.enable_ocr}")
    print(f"  Enable Readability: {default_config.enable_readability}")
    print(f"  Max Retry Attempts: {default_config.max_retry_attempts}")
    print()
    
    # Specialized configurations
    configs = {
        "Search Form": BrowserAgentConfig(
            staged_extraction=True,
            enable_readability=False,
            enable_ocr=False
        ),
        "Article Reading": BrowserAgentConfig(
            staged_extraction=False,
            enable_readability=True,
            enable_ocr=False
        ),
        "Dashboard": BrowserAgentConfig(
            staged_extraction=True,
            enable_readability=False,
            enable_ocr=True
        )
    }
    
    print("Specialized Configurations:")
    for name, config in configs.items():
        print(f"  {name}: staged={config.staged_extraction}, "
              f"readability={config.enable_readability}, ocr={config.enable_ocr}")
    print()


def main():
    """Run the complete demonstration."""
    print("🚀 Browser Operation Agent Data Supply Stack Demo")
    print("=" * 60)
    print()
    
    try:
        # Run synchronous demos
        demo_reference_system()
        demo_data_formats()
        demo_action_dsl()
        demo_metrics_system()
        demo_site_scenarios()
        
        # Run async demo
        asyncio.run(demo_configuration_options())
        
        print("=== Summary ===")
        print("✓ All 4 data formats implemented (IDX-Text, AX-Slim, DOM-Lite, VIS-ROI)")
        print("✓ Stable reference system with frameId:BN-backendNodeId format")
        print("✓ JSON action DSL for LLM integration")
        print("✓ Comprehensive metrics and acceptance criteria")
        print("✓ Support for 3 site scenarios (search, article, dashboard)")
        print("✓ Configurable extraction and optimization policies")
        print()
        print("🎯 Ready for LLM-based web automation!")
        
    except KeyboardInterrupt:
        print("\n👋 Demo interrupted by user")
    except Exception as e:
        print(f"\n❌ Demo error: {e}")
        logger.exception("Demo failed")


if __name__ == "__main__":
    main()