#!/usr/bin/env python3
"""
Unit tests for index resolution and enhanced DSL executor
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import unittest
from unittest.mock import Mock, patch, MagicMock
from agent.dsl_executor import EnhancedDSLExecutor, DSLResponse
from agent.element_catalog import ElementCatalog, ElementCatalogEntry


class TestIndexResolution(unittest.TestCase):
    
    def setUp(self):
        self.executor = EnhancedDSLExecutor()
        self.setup_mock_catalog()
    
    def setup_mock_catalog(self):
        """Setup a mock catalog for testing"""
        entries = [
            ElementCatalogEntry(
                index=0,
                role="button",
                tag="button", 
                primary_label="Submit",
                secondary_label="type:submit",
                section_hint="form",
                state_hint="",
                href_short="",
                robust_selectors=["[data-testid='submit-btn']", "button[type='submit']", "css=button.submit"],
                visible=True,
                disabled=False
            ),
            ElementCatalogEntry(
                index=1,
                role="input-text",
                tag="input",
                primary_label="Email",
                secondary_label="name:email",
                section_hint="form",
                state_hint="",
                href_short="",
                robust_selectors=["input[name='email']", "input[type='email']", "css=input.email"],
                visible=True,
                disabled=False
            ),
            ElementCatalogEntry(
                index=2,
                role="button",
                tag="button",
                primary_label="Disabled Button",
                secondary_label="",
                section_hint="action",
                state_hint="disabled",
                href_short="",
                robust_selectors=["button.disabled"],
                visible=True,
                disabled=True
            )
        ]
        
        self.executor.current_catalog = ElementCatalog(
            version="test_v1",
            url="https://example.com",
            title="Test Page",
            short_summary="Test page with form elements",
            nav_detected=False,
            abbreviated_entries=[],
            index_map={entry.index: entry for entry in entries}
        )
    
    def test_valid_index_resolution(self):
        """Test resolving valid index to selector"""
        result = self.executor._resolve_index_target("index=0")
        
        self.assertNotIn("error", result)
        self.assertIn("selector", result)
        self.assertEqual(result["selector"], "[data-testid='submit-btn']")
    
    def test_invalid_index_format(self):
        """Test handling of invalid index format"""
        result = self.executor._resolve_index_target("index=abc")
        
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], "INVALID_INDEX")
    
    def test_index_not_found(self):
        """Test handling of non-existent index"""
        result = self.executor._resolve_index_target("index=99")
        
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], "ELEMENT_NOT_FOUND")
        self.assertIn("available_indices", result["error"]["details"])
    
    def test_disabled_element_handling(self):
        """Test handling of disabled elements"""
        result = self.executor._resolve_index_target("index=2")
        
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], "ELEMENT_NOT_INTERACTABLE")
        self.assertIn("disabled", result["error"]["message"].lower())
    
    def test_catalog_version_verification(self):
        """Test catalog version verification"""
        payload = {"actions": [{"action": "click", "target": "index=0"}]}
        
        # Test with matching version
        with patch.object(self.executor, '_update_catalog_if_needed', return_value={"success": True}):
            processed = self.executor._preprocess_actions(payload, "test_v1")
            self.assertNotIn("error", processed)
        
        # Test with mismatched version
        with patch.object(self.executor, '_update_catalog_if_needed', return_value={"success": True}):
            processed = self.executor._preprocess_actions(payload, "wrong_version")
            self.assertIn("error", processed)
            self.assertEqual(processed["error"]["code"], "CATALOG_OUTDATED")
    
    def test_action_preprocessing(self):
        """Test preprocessing of actions with index targets"""
        action = {"action": "click", "target": "index=0"}
        
        result = self.executor._process_action(action)
        
        self.assertIn("action", result)
        processed_action = result["action"]
        self.assertEqual(processed_action["action"], "click")
        self.assertEqual(processed_action["target"], "[data-testid='submit-btn']")
    
    def test_backward_compatibility(self):
        """Test that CSS and XPath selectors still work"""
        action = {"action": "click", "target": "css=button.submit"}
        
        result = self.executor._process_action(action)
        
        self.assertIn("action", result)
        self.assertEqual(result["action"]["target"], "css=button.submit")  # Unchanged
    
    def test_refresh_catalog_action(self):
        """Test refresh_catalog action handling"""
        action = {"action": "refresh_catalog"}
        
        with patch.object(self.executor, '_force_catalog_refresh') as mock_refresh:
            result = self.executor._process_action(action)
            
            self.assertIn("action", result)
            # Should be converted to a no-op eval_js action
            self.assertEqual(result["action"]["action"], "eval_js")
    
    def test_scroll_to_text_action(self):
        """Test scroll_to_text action handling"""
        action = {"action": "scroll_to_text", "text": "Submit"}
        
        result = self.executor._process_action(action)
        
        self.assertIn("action", result)
        processed_action = result["action"]
        self.assertEqual(processed_action["action"], "scroll")
        self.assertEqual(processed_action["target"], "text=Submit")
    
    def test_enhanced_wait_actions(self):
        """Test enhanced wait action handling"""
        # Test wait_network
        action = {"action": "wait_network", "timeout": 5000}
        result = self.executor._process_action(action)
        
        self.assertIn("action", result)
        self.assertEqual(result["action"]["action"], "wait")
        self.assertEqual(result["action"]["ms"], 5000)
        
        # Test wait_selector
        action = {"action": "wait_selector", "selector": "button", "timeout": 3000}
        result = self.executor._process_action(action)
        
        self.assertIn("action", result)
        self.assertEqual(result["action"]["action"], "wait_for_selector")
        self.assertEqual(result["action"]["target"], "button")
    
    @patch('agent.dsl_executor.vnc_execute_dsl')
    def test_structured_response_creation(self, mock_vnc):
        """Test creation of structured responses"""
        # Mock successful execution
        mock_vnc.return_value = {
            "html": "<html>test</html>",
            "warnings": []
        }
        
        payload = {"actions": [{"action": "click", "target": "index=0"}], "complete": True}
        
        with patch.object(self.executor, '_update_catalog_if_needed', return_value={"success": True}):
            response = self.executor.execute_dsl(payload)
        
        self.assertIsInstance(response, DSLResponse)
        self.assertTrue(response.success)
        self.assertTrue(response.complete)
        self.assertTrue(response.is_done)
        self.assertIsNotNone(response.observation)
    
    @patch('agent.dsl_executor.vnc_execute_dsl')
    def test_error_response_creation(self, mock_vnc):
        """Test creation of error responses"""
        # Mock failed execution
        mock_vnc.return_value = {
            "html": "",
            "warnings": ["ERROR:auto:Element not found"]
        }
        
        payload = {"actions": [{"action": "click", "target": "index=0"}]}
        
        with patch.object(self.executor, '_update_catalog_if_needed', return_value={"success": True}):
            response = self.executor.execute_dsl(payload)
        
        self.assertIsInstance(response, DSLResponse)
        self.assertFalse(response.success)
        self.assertIsNotNone(response.error)
        self.assertEqual(response.error["code"], "ELEMENT_NOT_FOUND")
    
    def test_error_categorization(self):
        """Test error message categorization"""
        test_cases = [
            ("Element not found", "ELEMENT_NOT_FOUND"),
            ("Request timeout", "NAVIGATION_TIMEOUT"),
            ("not clickable", "ELEMENT_NOT_INTERACTABLE"),
            ("catalog outdated", "CATALOG_OUTDATED"),
            ("unsupported action", "UNSUPPORTED_ACTION"),
            ("unknown error", "EXECUTION_ERROR")
        ]
        
        for message, expected_code in test_cases:
            code = self.executor._categorize_error(message)
            self.assertEqual(code, expected_code, f"Failed for message: {message}")
    
    def test_no_catalog_available(self):
        """Test handling when no catalog is available"""
        self.executor.current_catalog = None
        
        result = self.executor._resolve_index_target("index=0")
        
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], "CATALOG_NOT_AVAILABLE")
    
    @patch('agent.dsl_executor.INDEX_MODE', False)
    def test_legacy_mode(self):
        """Test operation in legacy mode (INDEX_MODE=false)"""
        payload = {"actions": [{"action": "click", "target": "css=button"}]}
        
        with patch('agent.dsl_executor.vnc_execute_dsl') as mock_vnc:
            mock_vnc.return_value = {"html": "test", "warnings": []}
            
            response = self.executor.execute_dsl(payload)
        
        self.assertIsInstance(response, DSLResponse)
        # Should have used legacy executor
        mock_vnc.assert_called_once()


class TestDSLResponse(unittest.TestCase):
    
    def test_response_serialization(self):
        """Test DSL response serialization to dict"""
        response = DSLResponse(
            success=True,
            error=None,
            observation={"url": "https://example.com", "title": "Test"},
            is_done=True,
            complete=True,
            html="<html></html>",
            warnings=["INFO: Test warning"]
        )
        
        result_dict = response.to_dict()
        
        expected_keys = ["success", "html", "warnings", "observation", "is_done", "complete"]
        for key in expected_keys:
            self.assertIn(key, result_dict)
        
        self.assertTrue(result_dict["success"])
        self.assertTrue(result_dict["is_done"])
        self.assertTrue(result_dict["complete"])
        self.assertEqual(result_dict["html"], "<html></html>")
    
    def test_error_response_serialization(self):
        """Test error response serialization"""
        error = {
            "code": "ELEMENT_NOT_FOUND",
            "message": "Element not found",
            "details": {"index": 5}
        }
        
        response = DSLResponse(
            success=False,
            error=error,
            observation={"url": "https://example.com"},
            html="",
            warnings=["ERROR: Element not found"]
        )
        
        result_dict = response.to_dict()
        
        self.assertFalse(result_dict["success"])
        self.assertIn("error", result_dict)
        self.assertEqual(result_dict["error"]["code"], "ELEMENT_NOT_FOUND")
        self.assertIn("observation", result_dict)


if __name__ == "__main__":
    unittest.main()