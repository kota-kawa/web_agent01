#!/usr/bin/env python3
"""
Test the enhanced DOM snapshot script structure.
This validates that the JavaScript returns the expected data format.
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))

from agent.browser.dom import DOM_SNAPSHOT_SCRIPT, DOMElementNode

def test_script_structure():
    """Test that the DOM snapshot script has the expected structure."""
    print("=== Testing DOM Snapshot Script Structure ===")
    
    # Check that the script contains the required functionality
    required_functions = [
        "DISABLED_ELEMENTS",
        "DEFAULT_INCLUDE_ATTRIBUTES", 
        "BOUNDS_PROPAGATION_ELEMENTS",
        "isVisible",
        "isInteractive",
        "isScrollable",
        "paintOrderFiltering",
        "boundsPropagate",
        "filterAttributes"
    ]
    
    for func in required_functions:
        if func in DOM_SNAPSHOT_SCRIPT:
            print(f"✅ {func} found in script")
        else:
            print(f"❌ {func} missing from script")
            return False
    
    # Check for disabled elements filtering
    disabled_elements = ['style', 'script', 'head', 'meta', 'link', 'title']
    for element in disabled_elements:
        if f"'{element}'" in DOM_SNAPSHOT_SCRIPT:
            print(f"✅ {element} in disabled elements list")
        else:
            print(f"❌ {element} missing from disabled elements list")
    
    # Check for important attributes
    important_attrs = ['title', 'type', 'name', 'role', 'value', 'placeholder', 'alt']
    for attr in important_attrs:
        if f"'{attr}'" in DOM_SNAPSHOT_SCRIPT:
            print(f"✅ {attr} in important attributes list")
        else:
            print(f"❌ {attr} missing from important attributes list")
    
    print("✅ DOM snapshot script structure validation complete")
    return True

def test_json_from_mock():
    """Test creating DOM from a mock JSON structure that the script would return."""
    print("\n=== Testing Enhanced JSON Structure ===")
    
    # Mock the enhanced JSON structure that the new script would return
    mock_json = {
        "dom": {
            "tagName": "body",
            "attributes": {},
            "xpath": "/html/body",
            "isVisible": True,
            "isInteractive": False,
            "isTopElement": False,
            "isScrollable": False,
            "isIframe": False,
            "highlightIndex": None,
            "excludedByPaint": False,
            "excludedByParent": False,
            "depth": 0,
            "children": [
                {
                    "nodeType": "text",
                    "text": "Welcome to the site",
                    "depth": 1
                },
                {
                    "tagName": "button", 
                    "attributes": {
                        "title": "Click me",
                        "type": "button",
                        "id": "btn1"
                    },
                    "xpath": "/html/body/button",
                    "isVisible": True,
                    "isInteractive": True,
                    "isTopElement": True,
                    "isScrollable": False,
                    "isIframe": False,
                    "highlightIndex": 1,
                    "excludedByPaint": False,
                    "excludedByParent": False,
                    "depth": 1,
                    "children": [
                        {
                            "nodeType": "text", 
                            "text": "Click me",
                            "depth": 2
                        }
                    ]
                },
                {
                    "tagName": "div",
                    "attributes": {"class": "scrollable"},
                    "xpath": "/html/body/div",
                    "isVisible": True,
                    "isInteractive": False,
                    "isTopElement": False,
                    "isScrollable": True,
                    "isIframe": False,
                    "highlightIndex": None,
                    "excludedByPaint": False,
                    "excludedByParent": False,
                    "depth": 1,
                    "children": []
                },
                {
                    "tagName": "iframe",
                    "attributes": {"src": "https://example.com"},
                    "xpath": "/html/body/iframe",
                    "isVisible": True,
                    "isInteractive": False,
                    "isTopElement": False,
                    "isScrollable": False,
                    "isIframe": True,
                    "highlightIndex": None,
                    "excludedByPaint": False,
                    "excludedByParent": False,
                    "depth": 1,
                    "children": []
                }
            ]
        },
        "viewport": {
            "width": 1920,
            "height": 1080,
            "scrollX": 0,
            "scrollY": 50,
            "documentHeight": 2000,
            "documentWidth": 1920
        },
        "timestamp": 1640995200000
    }
    
    try:
        # Test that we can create a DOM from the enhanced structure
        dom_tree = DOMElementNode.from_json(mock_json["dom"])
        print("✅ Successfully created DOM tree from enhanced JSON")
        
        # Test the simplified text generation 
        simplified_text, selector_map = dom_tree.to_simplified_text(mock_json["viewport"])
        print("✅ Successfully generated simplified text")
        
        print("\n--- Generated Simplified Text ---")
        print(simplified_text)
        
        # Verify expected elements are present
        if "[1]" in simplified_text:
            print("✅ Interactive element has index")
        else:
            print("❌ Interactive element missing index")
            return False
            
        if "|SCROLL|" in simplified_text:
            print("✅ Scrollable element has scroll annotation")
        else:
            print("❌ Scrollable element missing scroll annotation")
            return False
            
        if "|IFRAME|" in simplified_text:
            print("✅ Iframe element has iframe annotation")
        else:
            print("❌ Iframe element missing iframe annotation")
            return False
            
        if "pixels above" in simplified_text:
            print("✅ Viewport scroll indicators present")
        else:
            print("❌ Viewport scroll indicators missing")
            return False
        
        print("\n--- Selector Map ---")
        for idx, xpath in selector_map.items():
            print(f"[{idx}] -> {xpath}")
            
        return True
        
    except Exception as e:
        print(f"❌ Error testing enhanced JSON structure: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests."""
    print("🧪 Testing Enhanced DOM Script and JSON Structure\n")
    
    success = True
    
    if not test_script_structure():
        success = False
    
    if not test_json_from_mock():
        success = False
        
    if success:
        print("\n🎉 All enhanced DOM tests passed!")
        return 0
    else:
        print("\n💥 Some enhanced DOM tests failed!")
        return 1

if __name__ == "__main__":
    sys.exit(main())