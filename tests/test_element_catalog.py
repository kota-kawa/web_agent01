#!/usr/bin/env python3
"""
Tests for element catalog generation functionality.
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from agent.browser.dom import DOMElementNode
from agent.element_catalog import ElementCatalogGenerator, generate_element_catalog


def test_catalog_generation():
    """Test basic catalog generation."""
    print("Testing catalog generation...")
    
    # Create mock DOM with interactive elements
    mock_dom_data = {
        "tagName": "body",
        "attributes": {},
        "xpath": "/html/body",
        "isVisible": True,
        "isInteractive": False,
        "text": "",
        "children": [
            {
                "tagName": "button",
                "attributes": {"id": "submit-btn", "class": "btn btn-primary"},
                "xpath": "/html/body/button[1]",
                "isVisible": True,
                "isInteractive": True,
                "text": "Submit",
                "children": []
            },
            {
                "tagName": "input",
                "attributes": {"type": "text", "name": "username", "placeholder": "Enter username"},
                "xpath": "/html/body/input[1]",
                "isVisible": True,
                "isInteractive": True,
                "text": "",
                "children": []
            },
            {
                "tagName": "a",
                "attributes": {"href": "/home", "class": "nav-link"},
                "xpath": "/html/body/a[1]",
                "isVisible": True,
                "isInteractive": True,
                "text": "Home",
                "children": []
            }
        ]
    }
    
    dom_tree = DOMElementNode.from_json(mock_dom_data)
    
    # Generate catalog
    catalog = generate_element_catalog(
        dom_tree, 
        url="https://example.com", 
        title="Test Page"
    )
    
    # Verify catalog structure
    assert catalog.url == "https://example.com"
    assert catalog.title == "Test Page"
    assert len(catalog.abbreviated_view) == 3
    assert len(catalog.full_view) == 3
    assert catalog.catalog_version
    
    # Check element indices
    indices = [elem.index for elem in catalog.abbreviated_view]
    assert indices == [0, 1, 2]
    
    # Check element types
    elements = catalog.abbreviated_view
    assert elements[0].tag == "button"
    assert elements[1].tag == "input"
    assert elements[2].tag == "a"
    
    # Check primary labels
    assert elements[0].primary_label == "Submit"
    assert elements[1].primary_label == "Enter username"
    assert elements[2].primary_label == "Home"
    
    print("✓ Catalog generation works correctly")


def test_robust_selectors():
    """Test robust selector generation."""
    print("Testing robust selector generation...")
    
    generator = ElementCatalogGenerator()
    
    # Create element with multiple identification methods
    mock_element_data = {
        "tagName": "button",
        "attributes": {
            "id": "submit-btn",
            "role": "button",
            "aria-label": "Submit Form",
            "data-testid": "submit-button",
            "class": "btn btn-primary"
        },
        "xpath": "/html/body/form/button[1]",
        "isVisible": True,
        "isInteractive": True,
        "text": "Submit Form",
        "children": []
    }
    
    element = DOMElementNode.from_json(mock_element_data)
    selectors = generator._generate_robust_selectors(element)
    
    # Check selector priority order
    assert len(selectors) >= 3
    assert "getByRole('button'" in selectors[0]  # Role selector first
    assert "getByText('Submit Form')" in selectors  # Text selector
    assert "#submit-btn" in selectors  # ID selector
    assert "[data-testid='submit-button']" in selectors  # Test ID selector
    
    print("✓ Robust selector generation works correctly")


def test_element_extraction():
    """Test interactive element extraction."""
    print("Testing element extraction...")
    
    generator = ElementCatalogGenerator()
    
    # Create DOM with mix of interactive and non-interactive elements
    mock_dom_data = {
        "tagName": "div",
        "attributes": {},
        "xpath": "/html/body/div[1]",
        "isVisible": True,
        "isInteractive": False,
        "text": "",
        "children": [
            {
                "tagName": "h1",
                "attributes": {},
                "xpath": "/html/body/div[1]/h1[1]",
                "isVisible": True,
                "isInteractive": False,
                "text": "Page Title",
                "children": []
            },
            {
                "tagName": "button",
                "attributes": {"type": "submit"},
                "xpath": "/html/body/div[1]/button[1]",
                "isVisible": True,
                "isInteractive": True,
                "text": "Click Me",
                "children": []
            },
            {
                "tagName": "input",
                "attributes": {"type": "hidden", "name": "csrf_token"},
                "xpath": "/html/body/div[1]/input[1]",
                "isVisible": False,
                "isInteractive": False,
                "text": "",
                "children": []
            },
            {
                "tagName": "select",
                "attributes": {"name": "country"},
                "xpath": "/html/body/div[1]/select[1]",
                "isVisible": True,
                "isInteractive": True,
                "text": "",
                "children": []
            }
        ]
    }
    
    dom_tree = DOMElementNode.from_json(mock_dom_data)
    elements = generator._extract_interactive_elements(dom_tree)
    
    # Should extract only visible interactive elements
    assert len(elements) == 2  # button and select, not h1 or hidden input
    
    # Check extracted elements
    button_elem = next(e for e in elements if e['tag'] == 'button')
    select_elem = next(e for e in elements if e['tag'] == 'select')
    
    assert button_elem['primary_label'] == "Click Me"
    assert button_elem['role'] == "button"
    assert select_elem['role'] == "combobox"
    
    print("✓ Element extraction works correctly")


def test_catalog_version_generation():
    """Test catalog version generation and stability."""
    print("Testing catalog version generation...")
    
    generator = ElementCatalogGenerator()
    
    # Create identical DOM trees
    mock_dom_data = {
        "tagName": "button",
        "attributes": {"id": "test-btn"},
        "xpath": "/html/body/button[1]",
        "isVisible": True,
        "isInteractive": True,
        "text": "Test",
        "children": []
    }
    
    dom_tree1 = DOMElementNode.from_json(mock_dom_data)
    dom_tree2 = DOMElementNode.from_json(mock_dom_data)
    
    # Generate catalogs for same content
    catalog1 = generator.generate_catalog(dom_tree1, "https://example.com", "Test")
    catalog2 = generator.generate_catalog(dom_tree2, "https://example.com", "Test")
    
    # Versions should be identical for identical content
    assert catalog1.catalog_version == catalog2.catalog_version
    
    # Change URL, version should be different
    catalog3 = generator.generate_catalog(dom_tree1, "https://different.com", "Test")
    assert catalog1.catalog_version != catalog3.catalog_version
    
    print("✓ Catalog version generation works correctly")


def test_disabled_mode():
    """Test catalog generation with index mode disabled."""
    print("Testing disabled mode...")
    
    generator = ElementCatalogGenerator(index_mode=False)
    
    mock_dom_data = {
        "tagName": "button",
        "attributes": {},
        "xpath": "/html/body/button[1]",
        "isVisible": True,
        "isInteractive": True,
        "text": "Test",
        "children": []
    }
    
    dom_tree = DOMElementNode.from_json(mock_dom_data)
    catalog = generator.generate_catalog(dom_tree, "https://example.com", "Test")
    
    # Should return empty catalog when disabled
    assert catalog.catalog_version == "disabled"
    assert len(catalog.abbreviated_view) == 0
    assert len(catalog.full_view) == 0
    
    print("✓ Disabled mode works correctly")


def test_label_extraction():
    """Test various label extraction methods."""
    print("Testing label extraction...")
    
    generator = ElementCatalogGenerator()
    
    test_cases = [
        # aria-label priority
        {
            "attributes": {"aria-label": "Close dialog", "title": "X"},
            "text": "×",
            "expected": "Close dialog"
        },
        # title fallback
        {
            "attributes": {"title": "Save document"},
            "text": "Save",
            "expected": "Save document"
        },
        # placeholder for inputs
        {
            "attributes": {"type": "text", "placeholder": "Enter email"},
            "text": "",
            "expected": "Enter email"
        },
        # text content fallback
        {
            "attributes": {},
            "text": "Click here",
            "expected": "Click here"
        }
    ]
    
    for i, case in enumerate(test_cases):
        mock_element_data = {
            "tagName": "button",
            "attributes": case["attributes"],
            "xpath": f"/html/body/button[{i+1}]",
            "isVisible": True,
            "isInteractive": True,
            "text": case["text"],
            "children": []
        }
        
        element = DOMElementNode.from_json(mock_element_data)
        label = generator._extract_primary_label(element)
        assert label == case["expected"], f"Case {i}: expected '{case['expected']}', got '{label}'"
    
    print("✓ Label extraction works correctly")


def run_all_tests():
    """Run all element catalog tests."""
    print("Running element catalog tests...")
    print()
    
    test_catalog_generation()
    test_robust_selectors()
    test_element_extraction()
    test_catalog_version_generation()
    test_disabled_mode()
    test_label_extraction()
    
    print()
    print("🎉 All element catalog tests passed!")


if __name__ == "__main__":
    run_all_tests()