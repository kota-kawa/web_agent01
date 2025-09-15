#!/usr/bin/env python3
"""
Test script for the new DOM optimization implementation
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))

from agent.browser.dom import DOMElementNode

# Test with mock data to verify the new structure works
def test_structured_output():
    print("Testing structured DOM output...")
    
    # Create a mock DOM structure
    mock_data = {
        "tagName": "body",
        "attributes": {},
        "isVisible": True,
        "isInteractive": False,
        "children": [
            {
                "tagName": "button",
                "attributes": {
                    "type": "submit",
                    "class": "btn btn-primary",
                    "title": "Submit form"
                },
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
                "attributes": {
                    "type": "text",
                    "placeholder": "Enter your name",
                    "name": "username"
                },
                "isVisible": True,
                "isInteractive": True,
                "highlightIndex": 2,
                "children": []
            },
            {
                "tagName": "div",
                "attributes": {
                    "class": "scroll-container"
                },
                "isVisible": True,
                "isInteractive": False,
                "annotations": ["SCROLL"],
                "children": [
                    {
                        "nodeType": "text",
                        "text": "This is a scrollable content area"
                    }
                ]
            },
            {
                "tagName": "iframe",
                "attributes": {
                    "src": "https://example.com/embed",
                    "title": "Embedded content"
                },
                "isVisible": True,
                "isInteractive": False,
                "annotations": ["IFRAME"],
                "children": []
            }
        ]
    }
    
    # Convert to DOMElementNode
    dom = DOMElementNode.from_json(mock_data)
    
    # Test structured output
    text_output = dom.to_text()
    print("Structured DOM output:")
    print("=" * 50)
    print(text_output)
    print("=" * 50)
    
    # Test marking new elements
    print("\nTesting new element marking...")
    
    # Create a modified version with a new element
    mock_data_new = {
        "tagName": "body",
        "attributes": {},
        "isVisible": True,
        "isInteractive": False,
        "children": [
            {
                "tagName": "button",
                "attributes": {
                    "type": "submit",
                    "class": "btn btn-primary",
                    "title": "Submit form"
                },
                "xpath": "/html/body/button[1]",
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
                "tagName": "button",
                "attributes": {
                    "type": "button",
                    "class": "btn btn-secondary",
                    "title": "Cancel"
                },
                "xpath": "/html/body/button[2]",
                "isVisible": True,
                "isInteractive": True,
                "highlightIndex": 3,
                "isNewElement": True,
                "children": [
                    {
                        "nodeType": "text",
                        "text": "Cancel"
                    }
                ]
            }
        ]
    }
    
    dom_new = DOMElementNode.from_json(mock_data_new)
    text_output_new = dom_new.to_text(previous_dom=dom)
    print("Output with new elements marked:")
    print("=" * 50)
    print(text_output_new)
    print("=" * 50)
    
    print("Test completed successfully!")

if __name__ == "__main__":
    test_structured_output()