"""Unit tests for element catalog functionality."""

import unittest
from unittest.mock import Mock, AsyncMock, patch
import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from agent.element_catalog import (
    ElementCatalogGenerator, 
    ElementInfo, 
    ElementCatalogEntry, 
    ElementCatalog,
    format_catalog_for_llm,
    resolve_index_to_selectors
)


class TestElementCatalog(unittest.TestCase):
    """Test element catalog generation and functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_page = Mock()
        self.generator = ElementCatalogGenerator(self.mock_page)
    
    def test_element_info_creation(self):
        """Test ElementInfo creation."""
        element_info = ElementInfo(
            index=0,
            tag_name="button",
            text_content="Click me",
            robust_selectors=["css=button", "text=Click me"],
            visible=True,
            disabled=False
        )
        
        self.assertEqual(element_info.index, 0)
        self.assertEqual(element_info.tag_name, "button")
        self.assertEqual(element_info.text_content, "Click me")
        self.assertIsInstance(element_info.robust_selectors, list)
        self.assertTrue(element_info.visible)
        self.assertFalse(element_info.disabled)
    
    def test_element_catalog_entry_creation(self):
        """Test ElementCatalogEntry creation."""
        entry = ElementCatalogEntry(
            index=0,
            role="button",
            tag="button",
            primary_label="Click me",
            secondary_label="#btn-submit",
            section_hint="main",
            state_hint="",
            href_short=""
        )
        
        self.assertEqual(entry.index, 0)
        self.assertEqual(entry.role, "button")
        self.assertEqual(entry.primary_label, "Click me")
    
    def test_catalog_creation(self):
        """Test ElementCatalog creation."""
        catalog = ElementCatalog(
            abbreviated_view=[],
            full_view={},
            catalog_version="test123",
            url="https://example.com",
            title="Test Page"
        )
        
        self.assertEqual(catalog.catalog_version, "test123")
        self.assertEqual(catalog.url, "https://example.com")
        self.assertEqual(catalog.title, "Test Page")
    
    def test_format_catalog_for_llm_empty(self):
        """Test formatting empty catalog for LLM."""
        catalog = ElementCatalog()
        formatted = format_catalog_for_llm(catalog)
        
        self.assertIn("No interactive elements", formatted)
    
    def test_format_catalog_for_llm_with_elements(self):
        """Test formatting catalog with elements for LLM."""
        entry = ElementCatalogEntry(
            index=0,
            role="button",
            tag="button",
            primary_label="Submit",
            secondary_label="#submit-btn",
            section_hint="form",
            state_hint="",
            href_short=""
        )
        
        catalog = ElementCatalog(
            abbreviated_view=[entry],
            catalog_version="test123",
            title="Test Page"
        )
        
        formatted = format_catalog_for_llm(catalog)
        
        self.assertIn("Element Catalog", formatted)
        self.assertIn("test123", formatted)
        self.assertIn("[0] button: Submit", formatted)
        self.assertIn("index=N", formatted)
    
    def test_resolve_index_to_selectors(self):
        """Test resolving index to selectors."""
        element_info = ElementInfo(
            index=0,
            robust_selectors=["css=#submit", "text=Submit", "xpath=//button[1]"]
        )
        
        catalog = ElementCatalog(
            full_view={0: element_info}
        )
        
        selectors = resolve_index_to_selectors(catalog, 0)
        
        self.assertEqual(len(selectors), 3)
        self.assertIn("css=#submit", selectors)
        self.assertIn("text=Submit", selectors)
        self.assertIn("xpath=//button[1]", selectors)
    
    def test_resolve_index_to_selectors_invalid_index(self):
        """Test resolving invalid index raises ValueError."""
        catalog = ElementCatalog(full_view={})
        
        with self.assertRaises(ValueError):
            resolve_index_to_selectors(catalog, 999)
    
    @patch('agent.element_catalog.log')
    async def test_generate_catalog_version_success(self, mock_log):
        """Test catalog version generation."""
        # Mock page methods
        self.mock_page.url = AsyncMock(return_value="https://example.com")
        self.mock_page.evaluate = AsyncMock(return_value="abc123")
        self.mock_page.viewport_size = AsyncMock(return_value={"width": 1920, "height": 1080})
        
        version = await self.generator._generate_catalog_version()
        
        self.assertIsInstance(version, str)
        self.assertEqual(len(version), 12)  # MD5 hash truncated to 12 chars
    
    @patch('agent.element_catalog.log')
    async def test_generate_catalog_version_fallback(self, mock_log):
        """Test catalog version generation with fallback."""
        # Mock page methods to raise exception
        self.mock_page.url = AsyncMock(side_effect=Exception("Page error"))
        
        version = await self.generator._generate_catalog_version()
        
        self.assertIsInstance(version, str)
        self.assertEqual(len(version), 12)
        mock_log.error.assert_called_once()
    
    async def test_generate_page_summary(self):
        """Test page summary generation."""
        summary_data = {
            "title": "Test Page",
            "headings": ["Main Heading", "Sub Heading"],
            "metaDescription": "A test page description"
        }
        
        self.mock_page.evaluate = AsyncMock(return_value=summary_data)
        
        summary = await self.generator._generate_page_summary()
        
        self.assertIn("Test Page", summary)
        self.assertIn("Main Heading", summary)
        self.assertIn("test page description", summary)


class TestCatalogIntegration(unittest.TestCase):
    """Integration tests for catalog functionality."""
    
    def test_element_catalog_data_consistency(self):
        """Test that abbreviated and full views are consistent."""
        # Create element info
        element_info = ElementInfo(
            index=0,
            tag_name="button",
            text_content="Click me",
            robust_selectors=["css=button", "text=Click me"],
            attributes={"id": "btn-submit", "class": "btn primary"},
            visible=True,
            disabled=False
        )
        
        # Create abbreviated entry manually (simulating _create_abbreviated_entry)
        entry = ElementCatalogEntry(
            index=0,
            role="button",
            tag="button",
            primary_label="Click me",
            secondary_label="#btn-submit",
            section_hint="",
            state_hint="",
            href_short=""
        )
        
        # Create catalog
        catalog = ElementCatalog(
            abbreviated_view=[entry],
            full_view={0: element_info}
        )
        
        # Test consistency
        self.assertEqual(entry.index, element_info.index)
        self.assertEqual(entry.tag, element_info.tag_name)
        self.assertTrue(0 in catalog.full_view)
        self.assertEqual(len(catalog.abbreviated_view), 1)


if __name__ == '__main__':
    # Run with more verbose output
    unittest.main(verbosity=2)