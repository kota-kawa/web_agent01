#!/usr/bin/env python3

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))

from agent.browser.dom import DOMElementNode

def test_structured_text_output():
    """Test the new structured text output format."""
    
    # Create a mock DOM structure
    button_node = DOMElementNode(
        tagName="button",
        attributes={"type": "submit", "class": "btn btn-primary"},
        isVisible=True,
        isInteractive=True,
        highlightIndex=1,
        isNewElement=False,
        children=[
            DOMElementNode(tagName="#text", text="Click me")
        ]
    )
    
    input_node = DOMElementNode(
        tagName="input",
        attributes={"type": "text", "placeholder": "Enter your name", "name": "username"},
        isVisible=True,
        isInteractive=True,
        highlightIndex=2,
        isNewElement=True,  # This is a new element
    )
    
    scrollable_div = DOMElementNode(
        tagName="div",
        attributes={"class": "scrollable-content"},
        isVisible=True,
        isInteractive=False,
        visualAnnotations=["|SCROLL|"],
        children=[
            DOMElementNode(tagName="#text", text="Some scrollable content")
        ]
    )
    
    root_node = DOMElementNode(
        tagName="body",
        isVisible=True,
        children=[button_node, input_node, scrollable_div]
    )
    
    # Test structured text output
    structured_text = root_node.to_structured_text()
    print("Structured Text Output:")
    print("=" * 50)
    print(structured_text)
    print("=" * 50)
    
    # Verify the format matches requirements
    lines = structured_text.split('\n')
    
    # Check for index numbers
    assert any('[1]' in line for line in lines), "Should contain [1] index"
    assert any('*[2]' in line for line in lines), "Should contain *[2] for new element"
    
    # Check for visual annotations
    assert any('|SCROLL|' in line for line in lines), "Should contain |SCROLL| annotation"
    
    # Check for attributes
    assert any('type="submit"' in line for line in lines), "Should contain type attribute"
    assert any('placeholder="Enter your name"' in line for line in lines), "Should contain placeholder"
    
    print("✅ All structured text format tests passed!")

def test_backward_compatibility():
    """Test that the old to_text() method still works."""
    
    simple_node = DOMElementNode(
        tagName="div",
        attributes={"id": "test"},
        isVisible=True,
        children=[
            DOMElementNode(tagName="#text", text="Hello World")
        ]
    )
    
    old_format = simple_node.to_text()
    print("\nLegacy Format Output:")
    print("=" * 30)
    print(old_format)
    print("=" * 30)
    
    assert "<div id=test>" in old_format, "Legacy format should work"
    print("✅ Backward compatibility test passed!")

if __name__ == "__main__":
    test_structured_text_output()
    test_backward_compatibility()
    print("\n🎉 All tests passed!")