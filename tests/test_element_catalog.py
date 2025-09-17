#!/usr/bin/env python3
"""
Unit tests for the element catalog functionality
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import unittest
from unittest.mock import Mock, patch
from agent.element_catalog import ElementCatalogGenerator, ElementCatalogEntry, ElementCatalog
from agent.browser.dom import DOMElementNode


class TestElementCatalog(unittest.TestCase):
    
    def setUp(self):
        self.generator = ElementCatalogGenerator()
    
    def test_catalog_generation(self):
        """Test basic catalog generation"""
        # Create mock DOM structure
        mock_dom = self.create_mock_dom()
        
        catalog = self.generator.generate_catalog(
            dom_elements=mock_dom,
            url="https://example.com",
            title="Test Page"
        )
        
        self.assertIsInstance(catalog, ElementCatalog)
        self.assertEqual(catalog.url, "https://example.com")
        self.assertEqual(catalog.title, "Test Page")
        self.assertIsInstance(catalog.version, str)
        self.assertGreater(len(catalog.version), 0)
    
    def test_interactive_element_extraction(self):
        """Test extraction of interactive elements"""
        mock_dom = self.create_mock_interactive_dom()
        
        elements = self.generator._extract_interactive_elements(mock_dom)
        
        # Should find button and input
        self.assertGreater(len(elements), 0)
        
        # Check that we found button
        button_found = any(elem['tag'] == 'button' for elem in elements)
        self.assertTrue(button_found, "Button element should be found")
        
        # Check that we found input
        input_found = any(elem['tag'] == 'input' for elem in elements)
        self.assertTrue(input_found, "Input element should be found")
    
    def test_robust_selector_generation(self):
        """Test generation of robust selectors"""
        mock_button = self.create_mock_button()
        
        selectors = self.generator._generate_robust_selectors(mock_button)
        
        self.assertIsInstance(selectors, list)
        self.assertGreater(len(selectors), 0)
        
        # Should include data-testid selector
        testid_selector = any("data-testid" in sel for sel in selectors)
        self.assertTrue(testid_selector, "Should include data-testid selector")
    
    def test_element_visibility_check(self):
        """Test element visibility and enablement checks"""
        # Visible element
        visible_element = Mock()
        visible_element.isVisible = True
        visible_element.attributes = {}
        
        self.assertTrue(self.generator._is_visible_and_enabled(visible_element))
        
        # Hidden element
        hidden_element = Mock()
        hidden_element.isVisible = False
        hidden_element.attributes = {}
        
        self.assertFalse(self.generator._is_visible_and_enabled(hidden_element))
        
        # Disabled element
        disabled_element = Mock()
        disabled_element.isVisible = True
        disabled_element.attributes = {"disabled": "true"}
        
        self.assertFalse(self.generator._is_visible_and_enabled(disabled_element))
    
    def test_catalog_versioning(self):
        """Test catalog version generation and change detection"""
        mock_dom = self.create_mock_dom()
        
        # Generate first catalog
        catalog1 = self.generator.generate_catalog(
            dom_elements=mock_dom,
            url="https://example.com",
            title="Test Page"
        )
        
        # Generate second catalog with same data
        catalog2 = self.generator.generate_catalog(
            dom_elements=mock_dom,
            url="https://example.com",
            title="Test Page"
        )
        
        # Versions should be the same for same content
        self.assertEqual(catalog1.version, catalog2.version)
        
        # Generate catalog with different URL
        catalog3 = self.generator.generate_catalog(
            dom_elements=mock_dom,
            url="https://different.com",
            title="Test Page"
        )
        
        # Version should be different for different URL
        self.assertNotEqual(catalog1.version, catalog3.version)
    
    def test_element_sorting(self):
        """Test stable element sorting"""
        elements = [
            {"node": self.create_mock_element("button", 2), "tag": "button", "primary_label": "Submit"},
            {"node": self.create_mock_element("input", 1), "tag": "input", "primary_label": "Email"},
            {"node": self.create_mock_element("a", 0), "tag": "a", "primary_label": "Home"},
        ]
        
        sorted_elements = self.generator._sort_elements_stable(elements)
        
        # Should be sorted by highlightIndex
        self.assertEqual(sorted_elements[0]["tag"], "a")  # index 0
        self.assertEqual(sorted_elements[1]["tag"], "input")  # index 1
        self.assertEqual(sorted_elements[2]["tag"], "button")  # index 2
    
    def test_section_hint_generation(self):
        """Test generation of section hints"""
        link_element = {"tag": "a", "primary_label": "Home"}
        input_element = {"tag": "input", "primary_label": "Username"}
        button_element = {"tag": "button", "primary_label": "Submit"}
        
        link_hint = self.generator._generate_section_hint(link_element, 0, [])
        input_hint = self.generator._generate_section_hint(input_element, 1, [])
        button_hint = self.generator._generate_section_hint(button_element, 2, [])
        
        self.assertEqual(link_hint, "navigation")
        self.assertEqual(input_hint, "form")
        self.assertEqual(button_hint, "form")  # Submit button
    
    def test_abbreviated_view_generation(self):
        """Test generation of abbreviated view for LLM"""
        entries = [
            ElementCatalogEntry(
                index=0,
                role="button",
                tag="button",
                primary_label="Submit",
                secondary_label="type:submit",
                section_hint="form",
                state_hint="",
                href_short=""
            ),
            ElementCatalogEntry(
                index=1,
                role="link",
                tag="a",
                primary_label="Home",
                secondary_label="",
                section_hint="navigation",
                state_hint="",
                href_short="/home"
            )
        ]
        
        abbreviated = self.generator._generate_abbreviated_view(entries)
        
        self.assertEqual(len(abbreviated), 2)
        self.assertEqual(abbreviated[0]["index"], 0)
        self.assertEqual(abbreviated[0]["tag"], "button")
        self.assertEqual(abbreviated[1]["index"], 1)
        self.assertEqual(abbreviated[1]["tag"], "a")
    
    def create_mock_dom(self):
        """Create a mock DOM structure for testing"""
        mock_dom = Mock(spec=DOMElementNode)
        mock_dom.tag = "body"
        mock_dom.children = []
        return mock_dom
    
    def create_mock_interactive_dom(self):
        """Create mock DOM with interactive elements"""
        mock_dom = Mock(spec=DOMElementNode)
        mock_dom.tag = "body"
        mock_dom.children = [
            self.create_mock_button(),
            self.create_mock_input()
        ]
        return mock_dom
    
    def create_mock_button(self):
        """Create mock button element"""
        button = Mock(spec=DOMElementNode)
        button.tag = "button"
        button.attributes = {
            "data-testid": "submit-button",
            "type": "submit"
        }
        button.text = "Submit"
        button.children = []
        button.isVisible = True
        button.highlightIndex = 1
        return button
    
    def create_mock_input(self):
        """Create mock input element"""
        input_elem = Mock(spec=DOMElementNode)
        input_elem.tag = "input"
        input_elem.attributes = {
            "type": "text",
            "name": "email",
            "placeholder": "Enter email"
        }
        input_elem.text = ""
        input_elem.children = []
        input_elem.isVisible = True
        input_elem.highlightIndex = 0
        return input_elem
    
    def create_mock_element(self, tag, highlight_index):
        """Create mock element with specified tag and highlight index"""
        element = Mock(spec=DOMElementNode)
        element.tag = tag
        element.highlightIndex = highlight_index
        element.attributes = {}
        element.children = []
        element.isVisible = True
        return element


if __name__ == "__main__":
    unittest.main()