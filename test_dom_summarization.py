#!/usr/bin/env python3
"""
Test script for DOM summarization functionality.
This script tests the new simplified DOM text representation without requiring a full browser.
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))

from agent.browser.dom import DOMElementNode

def test_simple_dom_creation():
    """Test creating a simple DOM structure manually and converting to simplified text."""
    
    # Create a simple DOM structure manually
    root = DOMElementNode(
        tagName="body",
        isVisible=True,
        children=[
            DOMElementNode(
                tagName="div",
                attributes={"class": "container"},
                isVisible=True,
                children=[
                    DOMElementNode(
                        tagName="#text",
                        text="Welcome to our site",
                        isVisible=True
                    ),
                    DOMElementNode(
                        tagName="button",
                        attributes={"id": "login-btn", "type": "button", "title": "Login"},
                        isVisible=True,
                        isInteractive=True,
                        highlightIndex=1,
                        xpath="/html/body/div/button",
                        text="Login"
                    ),
                    DOMElementNode(
                        tagName="input",
                        attributes={"type": "text", "placeholder": "Username", "name": "username"},
                        isVisible=True,
                        isInteractive=True,
                        highlightIndex=2,
                        xpath="/html/body/div/input[1]"
                    ),
                    DOMElementNode(
                        tagName="input",
                        attributes={"type": "password", "placeholder": "Password", "name": "password"},
                        isVisible=True,
                        isInteractive=True,
                        highlightIndex=3,
                        xpath="/html/body/div/input[2]"
                    )
                ]
            ),
            DOMElementNode(
                tagName="div",
                attributes={"class": "scrollable", "style": "overflow: auto"},
                isVisible=True,
                isScrollable=True,
                children=[
                    DOMElementNode(
                        tagName="#text",
                        text="This is a scrollable area with lots of content...",
                        isVisible=True
                    )
                ]
            ),
            DOMElementNode(
                tagName="iframe",
                attributes={"src": "https://example.com"},
                isVisible=True,
                isIframe=True
            )
        ]
    )
    
    print("=== Testing simplified DOM text generation ===")
    
    # Test the simplified text generation
    try:
        simplified_text, selector_map = root.to_simplified_text()
        print("✅ Simplified text generation successful!")
        print("\n--- Simplified DOM Output ---")
        print(simplified_text)
        print("\n--- Selector Map ---")
        for index, xpath in selector_map.items():
            print(f"[{index}] -> {xpath}")
            
    except Exception as e:
        print(f"❌ Error generating simplified text: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test with viewport information
    print("\n=== Testing with viewport information ===")
    viewport_info = {
        "width": 1920,
        "height": 1080,
        "scrollX": 0,
        "scrollY": 100,
        "documentHeight": 2000,
        "documentWidth": 1920
    }
    
    try:
        simplified_text_with_viewport, selector_map = root.to_simplified_text(viewport_info)
        print("✅ Simplified text with viewport successful!")
        print("\n--- Simplified DOM Output with Viewport ---")
        print(simplified_text_with_viewport)
        
    except Exception as e:
        print(f"❌ Error generating simplified text with viewport: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test with previous selector map (to check new element marking)
    print("\n=== Testing new element detection ===")
    # Use the actual xpath from the selector map output above
    prev_selector_map = {
        1: "/html/body/div/button",  # Button was there before
        2: "/html/body/div/input[1]"  # First input was there before
        # Second input is new
    }
    
    try:
        simplified_text_with_new, selector_map = root.to_simplified_text(viewport_info, prev_selector_map)
        print("✅ New element detection successful!")
        print("\n--- Simplified DOM Output with New Element Detection ---")
        print(simplified_text_with_new)
        
    except Exception as e:
        print(f"❌ Error with new element detection: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

def test_filtering():
    """Test that filtering logic works correctly."""
    print("\n=== Testing filtering logic ===")
    
    # Create DOM with elements that should be filtered
    root = DOMElementNode(
        tagName="body",
        isVisible=True,
        children=[
            # This should be included
            DOMElementNode(
                tagName="div",
                isVisible=True,
                children=[
                    DOMElementNode(
                        tagName="#text",
                        text="Visible text",
                        isVisible=True
                    )
                ]
            ),
            # This should be excluded (not visible)
            DOMElementNode(
                tagName="div",
                isVisible=False,
                children=[
                    DOMElementNode(
                        tagName="#text",
                        text="Hidden text",
                        isVisible=False
                    )
                ]
            ),
            # This should be excluded (too short text)
            DOMElementNode(
                tagName="#text",
                text="a",
                isVisible=True
            ),
            # This should be excluded by paint order
            DOMElementNode(
                tagName="span",
                isVisible=True,
                excludedByPaint=True,
                children=[
                    DOMElementNode(
                        tagName="#text",
                        text="Covered by another element",
                        isVisible=True
                    )
                ]
            ),
            # This should be excluded by parent bounds (non-interactive)
            DOMElementNode(
                tagName="span",
                isVisible=True,
                excludedByParent=True,
                isInteractive=False,
                children=[
                    DOMElementNode(
                        tagName="#text",
                        text="Inside button decoration",
                        isVisible=True
                    )
                ]
            ),
            # This should NOT be excluded by parent bounds (interactive)
            DOMElementNode(
                tagName="input",
                isVisible=True,
                excludedByParent=True,
                isInteractive=True,
                highlightIndex=1,
                attributes={"type": "text", "name": "search"}
            )
        ]
    )
    
    try:
        simplified_text, selector_map = root.to_simplified_text()
        print("✅ Filtering test successful!")
        print("\n--- Filtered DOM Output ---")
        print(simplified_text)
        
        # Check that interactive element was preserved despite excludedByParent
        if "[1]" in simplified_text and "search" in simplified_text:
            print("✅ Interactive element correctly preserved despite parent exclusion")
        else:
            print("❌ Interactive element was incorrectly filtered out")
            return False
            
        # Check that paint-excluded and parent-excluded non-interactive elements are gone
        if "Covered by another element" not in simplified_text:
            print("✅ Paint-excluded element correctly filtered")
        else:
            print("❌ Paint-excluded element was not filtered")
            return False
            
        if "Inside button decoration" not in simplified_text:
            print("✅ Parent-excluded non-interactive element correctly filtered")
        else:
            print("❌ Parent-excluded non-interactive element was not filtered")
            return False
        
    except Exception as e:
        print(f"❌ Error in filtering test: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

def main():
    """Run all tests."""
    print("🧪 Testing DOM Summarization Functionality\n")
    
    success = True
    
    # Test basic functionality
    if not test_simple_dom_creation():
        success = False
    
    # Test filtering
    if not test_filtering():
        success = False
    
    if success:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print("\n💥 Some tests failed!")
        return 1

if __name__ == "__main__":
    sys.exit(main())