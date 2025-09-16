#!/usr/bin/env python3
"""
Unit tests for Browser Use style element catalog functionality
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import asyncio
from unittest.mock import Mock, AsyncMock, patch
import pytest

from agent.element_catalog import (
    ElementCatalogEntry, ElementCatalog, ElementCatalogGenerator,
    format_catalog_for_llm
)


def test_element_catalog_entry_creation():
    """Test creating element catalog entries."""
    entry = ElementCatalogEntry(
        index=0,
        tag="button",
        role="button",
        primary_label="Submit Form",
        robust_selectors=["css=button[type='submit']", "text=Submit Form"],
        bbox={"x": 100, "y": 200, "width": 80, "height": 30}
    )
    
    assert entry.index == 0
    assert entry.tag == "button"
    assert entry.role == "button"
    assert entry.primary_label == "Submit Form"
    assert len(entry.robust_selectors) == 2
    print("✓ Element catalog entry creation works correctly")


def test_element_catalog_creation():
    """Test creating element catalogs with multiple entries."""
    entries = [
        ElementCatalogEntry(
            index=0,
            tag="button",
            primary_label="Submit",
            robust_selectors=["css=button[type='submit']"]
        ),
        ElementCatalogEntry(
            index=1,
            tag="input",
            primary_label="Username",
            robust_selectors=["css=input[name='username']"]
        )
    ]
    
    catalog = ElementCatalog(
        catalog_version="abc123",
        url="https://example.com",
        title="Test Page",
        entries=entries
    )
    
    # Build index map
    catalog.index_map = {entry.index: entry for entry in entries}
    
    assert catalog.catalog_version == "abc123"
    assert len(catalog.entries) == 2
    assert catalog.get_element_by_index(0) is not None
    assert catalog.get_element_by_index(1) is not None
    assert catalog.get_element_by_index(2) is None
    
    print("✓ Element catalog creation works correctly")


def test_catalog_short_view():
    """Test catalog short view generation."""
    entries = [
        ElementCatalogEntry(
            index=0,
            tag="button",
            role="button",
            primary_label="Click Me",
            secondary_label="Submit button",
            state_hint="enabled"
        ),
        ElementCatalogEntry(
            index=1,
            tag="input",
            role="textbox",
            primary_label="Enter text",
            state_hint="disabled"
        )
    ]
    
    catalog = ElementCatalog(
        catalog_version="test123",
        url="https://test.com",
        title="Test",
        entries=entries
    )
    
    short_view = catalog.get_short_view()
    
    assert len(short_view) == 2
    assert short_view[0]["index"] == 0
    assert short_view[0]["tag"] == "button"
    assert short_view[0]["primary_label"] == "Click Me"
    assert short_view[1]["state_hint"] == "disabled"
    
    print("✓ Catalog short view generation works correctly")


def test_robust_selectors_retrieval():
    """Test getting robust selectors by index."""
    entry = ElementCatalogEntry(
        index=0,
        tag="button",
        robust_selectors=[
            "css=[role='button']:has-text('Submit')",
            "text=Submit",
            "css=#submit-btn",
            "xpath=//button[@type='submit']"
        ]
    )
    
    catalog = ElementCatalog(
        catalog_version="test",
        url="test",
        title="test",
        entries=[entry]
    )
    catalog.index_map = {0: entry}
    
    selectors = catalog.get_robust_selectors(0)
    assert len(selectors) == 4
    assert selectors[0].startswith("css=[role='button']")
    assert "text=Submit" in selectors
    assert "xpath=" in selectors[-1]
    
    # Test non-existent index
    assert catalog.get_robust_selectors(999) == []
    
    print("✓ Robust selectors retrieval works correctly")


def test_format_catalog_for_llm():
    """Test formatting catalog for LLM consumption."""
    entries = [
        ElementCatalogEntry(
            index=0,
            tag="button",
            role="button",
            primary_label="Submit Form",
            secondary_label="Main action",
            state_hint="enabled",
            section_hint="form"
        ),
        ElementCatalogEntry(
            index=1,
            tag="a",
            primary_label="Go to next page",
            href_short="/next?page=2",
            section_hint="navigation"
        )
    ]
    
    catalog = ElementCatalog(
        catalog_version="v123",
        url="https://example.com/form",
        title="Example Form",
        entries=entries
    )
    
    formatted = format_catalog_for_llm(catalog, max_elements=10)
    
    assert "v123" in formatted
    assert "Example Form" in formatted
    assert "[0]" in formatted
    assert "[1]" in formatted
    assert "Submit Form" in formatted
    assert "Go to next page" in formatted
    assert "|enabled|" in formatted
    assert "→/next?page=2" in formatted
    assert "in:form" in formatted
    assert "in:navigation" in formatted
    
    print("✓ Catalog formatting for LLM works correctly")


def test_format_empty_catalog():
    """Test formatting empty catalog."""
    catalog = ElementCatalog(
        catalog_version="empty",
        url="https://example.com",
        title="Empty Page"
    )
    
    formatted = format_catalog_for_llm(catalog)
    assert "No interactive elements found" in formatted
    
    print("✓ Empty catalog formatting works correctly")


def test_format_large_catalog():
    """Test formatting large catalog with truncation."""
    entries = []
    for i in range(100):
        entries.append(ElementCatalogEntry(
            index=i,
            tag="button",
            primary_label=f"Button {i}"
        ))
    
    catalog = ElementCatalog(
        catalog_version="large",
        url="https://example.com",
        title="Large Page",
        entries=entries
    )
    
    formatted = format_catalog_for_llm(catalog, max_elements=5)
    
    # Should only show first 5 elements
    assert "[0]" in formatted
    assert "[4]" in formatted
    assert "[5]" not in formatted
    assert "and 95 more elements" in formatted
    
    print("✓ Large catalog truncation works correctly")


async def test_mock_catalog_generation():
    """Test catalog generation with mocked page."""
    # Create a mock page that returns test data
    mock_page = AsyncMock()
    mock_page.url.return_value = "https://test.com"
    mock_page.title.return_value = "Test Page"
    mock_page.viewport_size.return_value = {"width": 1280, "height": 720}
    
    # Mock the JavaScript evaluation
    mock_elements_data = [
        {
            "tag": "button",
            "role": "button",
            "primaryLabel": "Submit",
            "secondaryLabel": None,
            "sectionHint": "form",
            "stateHint": "enabled",
            "robustSelectors": ["css=button[type='submit']", "text=Submit"],
            "bbox": {"x": 100, "y": 200, "width": 80, "height": 30},
            "visible": True,
            "disabled": False,
            "elementId": "submit-btn"
        }
    ]
    mock_page.evaluate.return_value = mock_elements_data
    
    generator = ElementCatalogGenerator(mock_page)
    catalog = await generator.generate_catalog()
    
    assert catalog.url == "https://test.com"
    assert catalog.title == "Test Page"
    assert len(catalog.entries) == 1
    assert catalog.entries[0].tag == "button"
    assert catalog.entries[0].primary_label == "Submit"
    assert len(catalog.catalog_version) > 0
    
    print("✓ Mock catalog generation works correctly")


def run_all_tests():
    """Run all element catalog tests."""
    print("Running Browser Use style element catalog tests...\n")
    
    test_element_catalog_entry_creation()
    test_element_catalog_creation()
    test_catalog_short_view()
    test_robust_selectors_retrieval()
    test_format_catalog_for_llm()
    test_format_empty_catalog()
    test_format_large_catalog()
    
    # Run async test
    asyncio.run(test_mock_catalog_generation())
    
    print("\n🎉 All element catalog tests passed! Browser Use style implementation is working correctly.")


if __name__ == "__main__":
    run_all_tests()