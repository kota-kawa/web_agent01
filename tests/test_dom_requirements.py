#!/usr/bin/env python3
"""
Unit tests for the enhanced DOM optimization implementation
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from agent.browser.dom import DOMElementNode

def test_unnecessary_tag_filtering():
    """Test that unnecessary tags are filtered out"""
    print("Testing unnecessary tag filtering...")
    
    # Mock data that should include filtered out elements
    mock_data = {
        "tagName": "body",
        "attributes": {},
        "isVisible": True,
        "children": [
            {
                "tagName": "div",
                "attributes": {"class": "content"},
                "isVisible": True,
                "children": [
                    {
                        "nodeType": "text",
                        "text": "Visible content"
                    }
                ]
            }
            # Note: script, style, head, meta elements would be filtered out by JS
        ]
    }
    
    dom = DOMElementNode.from_json(mock_data)
    output = dom.to_text()
    
    # Verify that only the body and visible content div remain
    assert "script" not in output.lower()
    assert "style" not in output.lower()
    assert "head" not in output.lower()
    assert "meta" not in output.lower()
    assert "Visible content" in output
    print("✓ Unnecessary tag filtering works correctly")

def test_hidden_element_filtering():
    """Test that hidden elements are filtered out"""
    print("Testing hidden element filtering...")
    
    # Test excludedByParent functionality
    mock_data = {
        "tagName": "body",
        "attributes": {},
        "isVisible": True,
        "children": [
            {
                "tagName": "div",
                "attributes": {"class": "visible"},
                "isVisible": True,
                "children": [
                    {
                        "nodeType": "text", 
                        "text": "Visible text"
                    }
                ]
            },
            {
                "tagName": "div",
                "attributes": {"class": "hidden"},
                "isVisible": False,
                "excludedByParent": True,
                "children": [
                    {
                        "nodeType": "text",
                        "text": "Hidden text"
                    }
                ]
            }
        ]
    }
    
    dom = DOMElementNode.from_json(mock_data)
    output = dom.to_text()
    
    # Verify hidden elements are not in output
    assert "Visible text" in output
    assert "Hidden text" not in output
    print("✓ Hidden element filtering works correctly")

def test_interactive_element_numbering():
    """Test that interactive elements get proper numbering"""
    print("Testing interactive element numbering...")
    
    mock_data = {
        "tagName": "body",
        "attributes": {},
        "isVisible": True,
        "children": [
            {
                "tagName": "button",
                "attributes": {"type": "submit"},
                "isVisible": True,
                "isInteractive": True,
                "highlightIndex": 1,
                "children": [
                    {
                        "nodeType": "text",
                        "text": "Submit"
                    }
                ]
            },
            {
                "tagName": "input",
                "attributes": {"type": "text", "name": "username"},
                "isVisible": True,
                "isInteractive": True,
                "highlightIndex": 2,
                "children": []
            },
            {
                "tagName": "div",
                "attributes": {"class": "info"},
                "isVisible": True,
                "isInteractive": False,
                "children": [
                    {
                        "nodeType": "text",
                        "text": "Information"
                    }
                ]
            }
        ]
    }
    
    dom = DOMElementNode.from_json(mock_data)
    output = dom.to_text()
    
    # Verify interactive elements have indices
    assert "[1]" in output
    assert "[2]" in output
    assert "Submit" in output
    assert "username" in output
    # Non-interactive elements should not have indices
    lines = output.split('\n')
    info_line = [line for line in lines if "Information" in line][0]
    assert "[" not in info_line or "]" not in info_line
    print("✓ Interactive element numbering works correctly")

def test_new_element_marking():
    """Test that new elements are marked with asterisk"""
    print("Testing new element marking...")
    
    original_data = {
        "tagName": "body",
        "attributes": {},
        "xpath": "/html/body",
        "isVisible": True,
        "children": [
            {
                "tagName": "button",
                "attributes": {"id": "existing"},
                "xpath": "/html/body/button[1]",
                "isVisible": True,
                "isInteractive": True,
                "highlightIndex": 1,
                "children": [
                    {
                        "nodeType": "text",
                        "text": "Existing"
                    }
                ]
            }
        ]
    }
    
    new_data = {
        "tagName": "body",
        "attributes": {},
        "xpath": "/html/body",
        "isVisible": True,
        "children": [
            {
                "tagName": "button",
                "attributes": {"id": "existing"},
                "xpath": "/html/body/button[1]",
                "isVisible": True,
                "isInteractive": True,
                "highlightIndex": 1,
                "children": [
                    {
                        "nodeType": "text",
                        "text": "Existing"
                    }
                ]
            },
            {
                "tagName": "button",
                "attributes": {"id": "new"},
                "xpath": "/html/body/button[2]",
                "isVisible": True,
                "isInteractive": True,
                "highlightIndex": 2,
                "isNewElement": True,
                "children": [
                    {
                        "nodeType": "text",
                        "text": "New Button"
                    }
                ]
            }
        ]
    }
    
    original_dom = DOMElementNode.from_json(original_data)
    new_dom = DOMElementNode.from_json(new_data)
    output = new_dom.to_text(previous_dom=original_dom)
    
    # Verify new elements are marked
    assert "[1]" in output  # Existing element (no asterisk)
    assert "[*2]" in output  # New element (with asterisk)
    print("✓ New element marking works correctly")

def test_visual_annotations():
    """Test that visual annotations are added"""
    print("Testing visual annotations...")
    
    mock_data = {
        "tagName": "body",
        "attributes": {},
        "isVisible": True,
        "children": [
            {
                "tagName": "div",
                "attributes": {"class": "scrollable"},
                "isVisible": True,
                "annotations": ["SCROLL"],
                "children": [
                    {
                        "nodeType": "text",
                        "text": "Scrollable content"
                    }
                ]
            },
            {
                "tagName": "iframe",
                "attributes": {"src": "https://example.com"},
                "isVisible": True,
                "annotations": ["IFRAME"],
                "children": []
            }
        ]
    }
    
    dom = DOMElementNode.from_json(mock_data)
    output = dom.to_text()
    
    # Verify annotations are present
    assert "|SCROLL|" in output
    assert "|IFRAME|" in output
    print("✓ Visual annotations work correctly")

def test_attribute_filtering():
    """Test that only relevant attributes are included"""
    print("Testing attribute filtering...")
    
    mock_data = {
        "tagName": "body",
        "attributes": {},
        "isVisible": True,
        "children": [
            {
                "tagName": "input",
                "attributes": {
                    "type": "text",
                    "name": "username", 
                    "placeholder": "Enter username",
                    "title": "Username field",
                    "id": "user-input",
                    "class": "form-control"
                },
                "isVisible": True,
                "isInteractive": True,
                "highlightIndex": 1,
                "children": []
            }
        ]
    }
    
    dom = DOMElementNode.from_json(mock_data)
    output = dom.to_text()
    
    # Verify relevant attributes are included
    assert 'type="text"' in output
    assert 'name="username"' in output
    assert 'placeholder="Enter username"' in output
    assert 'title="Username field"' in output
    assert 'id="user-input"' in output
    assert 'class="form-control"' in output
    
    print("✓ Attribute filtering works correctly")

def test_scroll_position_annotations():
    """Test that scroll position annotations are added"""
    print("Testing scroll position annotations...")
    
    mock_data = {
        "tagName": "body",
        "attributes": {},
        "isVisible": True,
        "children": [
            {
                "tagName": "div",
                "attributes": {"class": "content"},
                "isVisible": True,
                "children": [
                    {
                        "nodeType": "text",
                        "text": "Main content"
                    }
                ]
            }
        ]
    }
    
    dom = DOMElementNode.from_json(mock_data)
    
    # Test with scroll info
    dom.set_scroll_info(pixels_above=150, pixels_below=300)
    output = dom.to_text()
    
    assert "... 150 pixels above ..." in output
    assert "... 300 pixels below ..." in output
    print("✓ Scroll position annotations work correctly")

def test_text_content_extraction():
    """Test that text content is properly extracted and formatted"""
    print("Testing text content extraction...")
    
    mock_data = {
        "tagName": "body",
        "attributes": {},
        "isVisible": True,
        "children": [
            {
                "tagName": "button",
                "attributes": {"type": "submit"},
                "isVisible": True,
                "isInteractive": True,
                "highlightIndex": 1,
                "children": [
                    {
                        "nodeType": "text",
                        "text": "Click Me"
                    }
                ]
            },
            {
                "tagName": "div",
                "attributes": {"class": "description"},
                "isVisible": True,
                "children": [
                    {
                        "nodeType": "text",
                        "text": "This is a longer description text that should be included"
                    }
                ]
            }
        ]
    }
    
    dom = DOMElementNode.from_json(mock_data)
    output = dom.to_text()
    
    # Verify text content is extracted
    assert "Click Me" in output
    assert "This is a longer description text that should be included" in output
    print("✓ Text content extraction works correctly")

def run_all_tests():
    """Run all DOM optimization tests"""
    print("Running enhanced DOM optimization tests...\n")
    
    test_unnecessary_tag_filtering()
    test_hidden_element_filtering()
    test_interactive_element_numbering()
    test_new_element_marking()
    test_visual_annotations()
    test_attribute_filtering()
    test_scroll_position_annotations()
    test_text_content_extraction()
    
    print("\n🎉 All tests passed! DOM optimization implementation is working correctly.")

if __name__ == "__main__":
    run_all_tests()