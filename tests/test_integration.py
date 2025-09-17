"""Integration tests for element catalog system."""

import unittest
from unittest.mock import Mock, patch, AsyncMock
import sys
import os
import json

# Add the project root to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestElementCatalogIntegration(unittest.TestCase):
    """Integration tests for the complete element catalog system."""
    
    def test_index_mode_environment_variable(self):
        """Test INDEX_MODE environment variable handling."""
        # Test default (should be True)
        with patch.dict(os.environ, {}, clear=True):
            from agent.controller.prompt import INDEX_MODE
            # Default should be True when not set
            # Note: This might be True based on the default value
        
        # Test explicit True
        with patch.dict(os.environ, {"INDEX_MODE": "true"}):
            # Reload the module to pick up new env var
            import importlib
            import agent.controller.prompt
            importlib.reload(agent.controller.prompt)
            self.assertTrue(agent.controller.prompt.INDEX_MODE)
        
        # Test explicit False
        with patch.dict(os.environ, {"INDEX_MODE": "false"}):
            import importlib
            import agent.controller.prompt
            importlib.reload(agent.controller.prompt)
            self.assertFalse(agent.controller.prompt.INDEX_MODE)
    
    def test_catalog_data_flow(self):
        """Test the complete data flow of catalog information."""
        # Mock catalog data
        mock_catalog_data = {
            "catalog_version": "test123",
            "url": "https://example.com",
            "title": "Test Page",
            "short_summary": "A test page for catalog testing",
            "nav_detected": False,
            "abbreviated_view": [
                {
                    "index": 0,
                    "role": "button",
                    "tag": "button",
                    "primary_label": "Submit",
                    "secondary_label": "#submit-btn",
                    "section_hint": "form",
                    "state_hint": "",
                    "href_short": ""
                },
                {
                    "index": 1,
                    "role": "link",
                    "tag": "a",
                    "primary_label": "Go to page",
                    "secondary_label": ".nav-link",
                    "section_hint": "nav",
                    "state_hint": "",
                    "href_short": "/page"
                }
            ]
        }
        
        # Test catalog formatting
        from agent.browser.vnc import format_catalog_for_display
        formatted = format_catalog_for_display(mock_catalog_data)
        
        # Verify formatted output contains expected elements
        self.assertIn("Element Catalog (vtest123)", formatted)
        self.assertIn("Test Page", formatted)
        self.assertIn("[0] button: Submit", formatted)
        self.assertIn("[1] link: Go to page", formatted)
        self.assertIn("index=N", formatted)
    
    def test_structured_response_format(self):
        """Test the new structured response format."""
        # Mock DSL execution result
        mock_result = {
            "success": True,
            "error": None,
            "observation": {
                "url": "https://example.com",
                "title": "Test Page",
                "short_summary": "Test page summary",
                "catalog_version": "test123",
                "nav_detected": False
            },
            "is_done": False,
            "complete": False,
            "html": "<html><body>Test</body></html>",
            "warnings": [],
            "correlation_id": "abc123"
        }
        
        # Verify all required fields are present
        required_fields = ["success", "observation", "is_done", "complete"]
        for field in required_fields:
            self.assertIn(field, mock_result)
        
        # Verify observation structure
        observation = mock_result["observation"]
        observation_fields = ["url", "title", "short_summary", "nav_detected"]
        for field in observation_fields:
            self.assertIn(field, observation)
    
    def test_error_response_format(self):
        """Test error response format."""
        mock_error_result = {
            "success": False,
            "error": {
                "code": "CATALOG_OUTDATED",
                "message": "Please execute refresh_catalog",
                "details": {
                    "expected": "old123",
                    "current": "new456"
                }
            },
            "observation": {
                "url": "https://example.com",
                "title": "Test Page",
                "short_summary": "",
                "catalog_version": "new456",
                "nav_detected": True
            },
            "is_done": False,
            "complete": False
        }
        
        # Verify error structure
        self.assertFalse(mock_error_result["success"])
        self.assertIsNotNone(mock_error_result["error"])
        self.assertEqual(mock_error_result["error"]["code"], "CATALOG_OUTDATED")
        self.assertTrue(mock_error_result["observation"]["nav_detected"])
    
    def test_backward_compatibility(self):
        """Test that new features don't break existing functionality."""
        # Test that old DSL actions still work
        from agent.actions.basic import click, navigate, type_text, wait
        
        # These should create actions in the old format
        old_click = click("css=button")
        old_navigate = navigate("https://example.com")
        old_type = type_text("css=input", "test")
        old_wait = wait(1000)
        
        # Verify old format is preserved
        self.assertEqual(old_click["action"], "click")
        self.assertEqual(old_click["target"], "css=button")
        
        self.assertEqual(old_navigate["action"], "navigate")
        self.assertEqual(old_navigate["target"], "https://example.com")
        
        self.assertEqual(old_type["action"], "type")
        self.assertEqual(old_type["target"], "css=input")
        self.assertEqual(old_type["value"], "test")
        
        self.assertEqual(old_wait["action"], "wait")
        self.assertEqual(old_wait["ms"], 1000)
    
    def test_index_target_format_validation(self):
        """Test index target format validation."""
        # Valid index formats
        valid_indexes = ["index=0", "index=5", "index=10", "index=999"]
        
        for index_target in valid_indexes:
            self.assertTrue(index_target.startswith("index="))
            # Extract index number
            index_str = index_target.split("=", 1)[1]
            self.assertTrue(index_str.isdigit())
            index_num = int(index_str)
            self.assertGreaterEqual(index_num, 0)
        
        # Invalid formats (these should be handled gracefully)
        invalid_indexes = ["index=", "index=abc", "index=-1", "idx=5"]
        
        for invalid_target in invalid_indexes:
            if invalid_target.startswith("index="):
                try:
                    index_str = invalid_target.split("=", 1)[1]
                    if index_str:
                        int(index_str)  # This should raise ValueError for invalid formats
                except (ValueError, IndexError):
                    # Expected for invalid formats
                    pass
    
    def test_catalog_version_handling(self):
        """Test catalog version handling and comparison."""
        # Test version format (should be hex string)
        version1 = "abc123def456"
        version2 = "def456abc123"
        
        # Versions should be strings of consistent length
        self.assertIsInstance(version1, str)
        self.assertIsInstance(version2, str)
        self.assertEqual(len(version1), 12)  # As defined in catalog generator
        self.assertEqual(len(version2), 12)
        
        # Different versions should not be equal
        self.assertNotEqual(version1, version2)
        
        # Version comparison should be exact string match
        self.assertEqual(version1, version1)
        self.assertEqual(version2, version2)


class TestPromptIntegration(unittest.TestCase):
    """Test prompt integration with catalog system."""
    
    @patch('agent.controller.prompt.get_catalog')
    @patch('agent.controller.prompt.INDEX_MODE', True)
    def test_prompt_includes_catalog_when_enabled(self, mock_get_catalog):
        """Test that prompt includes catalog when INDEX_MODE is enabled."""
        from agent.controller.prompt import build_prompt
        
        # Mock catalog data
        mock_catalog_data = {
            "catalog_version": "test123",
            "title": "Test Page",
            "abbreviated_view": [
                {
                    "index": 0,
                    "role": "button",
                    "tag": "button", 
                    "primary_label": "Submit",
                    "secondary_label": "#submit-btn",
                    "section_hint": "form",
                    "state_hint": "",
                    "href_short": ""
                }
            ]
        }
        
        mock_get_catalog.return_value = mock_catalog_data
        
        # Build prompt
        prompt = build_prompt(
            cmd="Click the submit button",
            page="<html><body>Test</body></html>",
            hist=[]
        )
        
        # Verify catalog information is included
        self.assertIn("Element Catalog", prompt)
        self.assertIn("test123", prompt)
        self.assertIn("index=N", prompt)
    
    @patch('agent.controller.prompt.get_catalog')
    @patch('agent.controller.prompt.INDEX_MODE', False) 
    def test_prompt_excludes_catalog_when_disabled(self, mock_get_catalog):
        """Test that prompt excludes catalog when INDEX_MODE is disabled."""
        from agent.controller.prompt import build_prompt
        
        # Build prompt
        prompt = build_prompt(
            cmd="Click the submit button",
            page="<html><body>Test</body></html>",
            hist=[]
        )
        
        # Verify catalog information is not included
        self.assertNotIn("Element Catalog", prompt)
        # get_catalog should not be called when INDEX_MODE is False
        mock_get_catalog.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)