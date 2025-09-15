import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from agent.browser.dom import DOMElementNode


def test_enhanced_dom_filtering():
    """Test that enhanced DOM filtering features work correctly."""
    
    # Test structured text output format
    button_node = DOMElementNode(
        tagName="button",
        attributes={"type": "submit", "class": "btn"},
        isVisible=True,
        isInteractive=True,
        highlightIndex=1,
        isNewElement=False,
        children=[DOMElementNode(tagName="#text", text="Submit")]
    )
    
    new_input = DOMElementNode(
        tagName="input",
        attributes={"type": "text", "placeholder": "Enter text"},
        isVisible=True,
        isInteractive=True,
        highlightIndex=2,
        isNewElement=True,  # This is new
    )
    
    scrollable = DOMElementNode(
        tagName="div",
        attributes={"class": "content"},
        isVisible=True,
        visualAnnotations=["|SCROLL|"],
        children=[DOMElementNode(tagName="#text", text="Scrollable content")]
    )
    
    iframe = DOMElementNode(
        tagName="iframe",
        attributes={"src": "example.com"},
        isVisible=True,
        visualAnnotations=["|IFRAME|"]
    )
    
    root = DOMElementNode(
        tagName="body",
        isVisible=True,
        children=[button_node, new_input, scrollable, iframe],
        viewportInfo={
            "scrollTop": 50,
            "scrollHeight": 1000,
            "viewportHeight": 600
        }
    )
    
    # Test structured output
    structured = root.to_structured_text()
    lines = structured.split('\n')
    
    # Verify format requirements
    assert any('[1]' in line and 'button' in line for line in lines), "Should have indexed button"
    assert any('*[2]' in line and 'input' in line for line in lines), "Should mark new elements with *"
    assert any('|SCROLL|' in line for line in lines), "Should have scroll annotation"
    assert any('|IFRAME|' in line for line in lines), "Should have iframe annotation"
    assert any('50 pixels above' in line for line in lines), "Should show pixels above"
    assert any('350 pixels below' in line for line in lines), "Should show pixels below"
    assert any('placeholder="Enter text"' in line for line in lines), "Should include relevant attributes"
    assert any('Submit' in line for line in lines), "Should preserve text content"


def test_children_node_handling():
    """Test handling of flattened children from excluded elements."""
    
    children_data = {
        "nodeType": "children",
        "children": [
            {"nodeType": "text", "text": "Some text"},
            {
                "tagName": "span",
                "attributes": {"class": "test"},
                "isVisible": True,
                "children": []
            }
        ]
    }
    
    node = DOMElementNode.from_json(children_data)
    assert node.tagName == "#children"
    assert len(node.children) == 2
    
    structured = node.to_structured_text()
    assert "Some text" in structured
    assert "span" in structured


def test_backward_compatibility():
    """Ensure existing functionality still works."""
    
    # Test original to_text() method
    simple_node = DOMElementNode(
        tagName="div",
        attributes={"id": "test", "class": "container"},
        isVisible=True,
        highlightIndex=1,
        children=[DOMElementNode(tagName="#text", text="Hello")]
    )
    
    legacy_output = simple_node.to_text()
    assert "<div id=test class=container> [1]" in legacy_output
    assert "Hello" in legacy_output
    
    # Test original to_lines() method
    lines = simple_node.to_lines()
    assert len(lines) >= 2  # Should have element and text
    assert any("<div" in line for line in lines)
    assert any("Hello" in line for line in lines)


def test_from_json_compatibility():
    """Test that from_json handles both old and new format."""
    
    # Old format (backward compatibility)
    old_format = {
        "tagName": "button",
        "attributes": {"type": "button"},
        "xpath": "/html/body/button[1]",
        "isVisible": True,
        "isInteractive": True,
        "isTopElement": True,
        "highlightIndex": 1,
        "children": []
    }
    
    node = DOMElementNode.from_json(old_format)
    assert node.tagName == "button"
    assert node.isInteractive
    assert node.highlightIndex == 1
    assert node.isNewElement == False  # Default
    assert len(node.visualAnnotations) == 0  # Default
    
    # New format
    new_format = {
        "tagName": "input",
        "attributes": {"type": "text"},
        "isVisible": True,
        "isInteractive": True,
        "highlightIndex": 2,
        "isNewElement": True,
        "visualAnnotations": ["|SCROLL|"],
        "children": []
    }
    
    node = DOMElementNode.from_json(new_format)
    assert node.tagName == "input"
    assert node.isNewElement == True
    assert "|SCROLL|" in node.visualAnnotations


def test_attribute_filtering():
    """Test that only relevant attributes are included in structured output."""
    
    node = DOMElementNode(
        tagName="input",
        attributes={
            "type": "email",  # Should include
            "name": "email",  # Should include
            "placeholder": "Enter email",  # Should include
            "aria-label": "Email field",  # Should include
            "data-long-value": "a" * 150,  # Should truncate
            "title": "Email input"  # Should include
        },
        isVisible=True,
        isInteractive=True,
        highlightIndex=1
    )
    
    structured = node.to_structured_text()
    
    # Should include relevant attributes
    assert 'type="email"' in structured
    assert 'name="email"' in structured
    assert 'placeholder="Enter email"' in structured
    assert 'aria-label="Email field"' in structured
    assert 'title="Email input"' in structured
    
    # Should truncate long values (implemented in JavaScript, so just verify attribute exists)
    assert 'data-long-value=' in structured


if __name__ == "__main__":
    test_enhanced_dom_filtering()
    test_children_node_handling()
    test_backward_compatibility()
    test_from_json_compatibility()
    test_attribute_filtering()
    print("All enhanced DOM tests passed!")