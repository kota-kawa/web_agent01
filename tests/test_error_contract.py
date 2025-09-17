#!/usr/bin/env python3
"""
Unit tests for error contract and response format verification
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import unittest
from unittest.mock import Mock, patch
from agent.dsl_executor import execute_enhanced_dsl, DSLResponse


class TestErrorContract(unittest.TestCase):
    
    def test_catalog_outdated_error(self):
        """Test CATALOG_OUTDATED error format"""
        payload = {"actions": [{"action": "click", "target": "index=0"}]}
        
        with patch('agent.dsl_executor._executor') as mock_executor:
            # Mock catalog version mismatch
            mock_response = DSLResponse(
                success=False,
                error={
                    "code": "CATALOG_OUTDATED",
                    "message": "Catalog version mismatch. Please execute refresh_catalog action.",
                    "details": {
                        "expected": "v1",
                        "current": "v2"
                    }
                },
                observation={"url": "https://example.com", "title": "Test Page"}
            )
            mock_executor.execute_dsl.return_value = mock_response
            
            result = execute_enhanced_dsl(payload, expected_catalog_version="v1")
        
        self.assertFalse(result["success"])
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], "CATALOG_OUTDATED")
        self.assertIn("catalog", result["error"]["message"].lower())
        self.assertIn("expected", result["error"]["details"])
        self.assertIn("current", result["error"]["details"])
        self.assertIn("observation", result)
    
    def test_element_not_found_error(self):
        """Test ELEMENT_NOT_FOUND error format"""
        with patch('agent.dsl_executor._executor') as mock_executor:
            mock_response = DSLResponse(
                success=False,
                error={
                    "code": "ELEMENT_NOT_FOUND",
                    "message": "Element with index 5 not found in catalog.",
                    "details": {
                        "index": 5,
                        "available_indices": [0, 1, 2, 3]
                    }
                },
                observation={"url": "https://example.com"}
            )
            mock_executor.execute_dsl.return_value = mock_response
            
            payload = {"actions": [{"action": "click", "target": "index=5"}]}
            result = execute_enhanced_dsl(payload)
        
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "ELEMENT_NOT_FOUND")
        self.assertIn("index", result["error"]["details"])
        self.assertIn("available_indices", result["error"]["details"])
        self.assertIsInstance(result["error"]["details"]["available_indices"], list)
    
    def test_element_not_interactable_error(self):
        """Test ELEMENT_NOT_INTERACTABLE error format"""
        with patch('agent.dsl_executor._executor') as mock_executor:
            mock_response = DSLResponse(
                success=False,
                error={
                    "code": "ELEMENT_NOT_INTERACTABLE",
                    "message": "Element at index 2 is disabled.",
                    "details": {
                        "index": 2,
                        "label": "Disabled Button",
                        "state": "disabled"
                    }
                },
                observation={"url": "https://example.com"}
            )
            mock_executor.execute_dsl.return_value = mock_response
            
            payload = {"actions": [{"action": "click", "target": "index=2"}]}
            result = execute_enhanced_dsl(payload)
        
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "ELEMENT_NOT_INTERACTABLE")
        self.assertIn("index", result["error"]["details"])
        self.assertIn("label", result["error"]["details"])
        self.assertIn("state", result["error"]["details"])
    
    def test_navigation_timeout_error(self):
        """Test NAVIGATION_TIMEOUT error format"""
        with patch('agent.dsl_executor._executor') as mock_executor:
            mock_response = DSLResponse(
                success=False,
                error={
                    "code": "NAVIGATION_TIMEOUT",
                    "message": "Navigation timeout after 30 seconds",
                    "details": {"timeout": 30000}
                },
                observation={"url": "https://example.com"}
            )
            mock_executor.execute_dsl.return_value = mock_response
            
            payload = {"actions": [{"action": "navigate", "target": "https://slow-site.com"}]}
            result = execute_enhanced_dsl(payload)
        
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "NAVIGATION_TIMEOUT")
        self.assertIn("timeout", result["error"]["message"].lower())
    
    def test_unsupported_action_error(self):
        """Test UNSUPPORTED_ACTION error format"""
        with patch('agent.dsl_executor._executor') as mock_executor:
            mock_response = DSLResponse(
                success=False,
                error={
                    "code": "UNSUPPORTED_ACTION",
                    "message": "Action 'unknown_action' is not supported",
                    "details": {"action": "unknown_action"}
                },
                observation={"url": "https://example.com"}
            )
            mock_executor.execute_dsl.return_value = mock_response
            
            payload = {"actions": [{"action": "unknown_action"}]}
            result = execute_enhanced_dsl(payload)
        
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "UNSUPPORTED_ACTION")
        self.assertIn("action", result["error"]["details"])
    
    def test_successful_response_format(self):
        """Test successful response format"""
        with patch('agent.dsl_executor._executor') as mock_executor:
            mock_response = DSLResponse(
                success=True,
                error=None,
                observation={
                    "url": "https://example.com",
                    "title": "Example Page",
                    "short_summary": "5 interactive elements (3 buttons, 2 inputs)",
                    "catalog_version": "abc123",
                    "nav_detected": False
                },
                is_done=False,
                complete=False,
                html="<html>...</html>",
                warnings=["INFO:auto:Action completed successfully"]
            )
            mock_executor.execute_dsl.return_value = mock_response
            
            payload = {"actions": [{"action": "click", "target": "index=0"}]}
            result = execute_enhanced_dsl(payload)
        
        # Verify required top-level fields
        required_fields = ["success", "html", "warnings"]
        for field in required_fields:
            self.assertIn(field, result)
        
        # Verify response structure
        self.assertTrue(result["success"])
        self.assertIsNone(result.get("error"))
        self.assertIn("observation", result)
        self.assertIn("is_done", result)
        self.assertIn("complete", result)
        
        # Verify observation structure
        obs = result["observation"]
        self.assertIn("url", obs)
        self.assertIn("title", obs)
        self.assertIn("short_summary", obs)
        self.assertIn("catalog_version", obs)
        self.assertIn("nav_detected", obs)
        
        # Verify data types
        self.assertIsInstance(result["success"], bool)
        self.assertIsInstance(result["is_done"], bool)
        self.assertIsInstance(result["complete"], bool)
        self.assertIsInstance(result["html"], str)
        self.assertIsInstance(result["warnings"], list)
        self.assertIsInstance(obs["nav_detected"], bool)
    
    def test_backward_compatibility_fields(self):
        """Test that backward compatibility fields are present"""
        with patch('agent.dsl_executor._executor') as mock_executor:
            mock_response = DSLResponse(
                success=True,
                complete=True,
                is_done=True,
                html="<html></html>",
                warnings=[]
            )
            mock_executor.execute_dsl.return_value = mock_response
            
            payload = {"actions": [], "complete": True}
            result = execute_enhanced_dsl(payload)
        
        # Both old and new completion fields should be present
        self.assertIn("complete", result)
        self.assertIn("is_done", result)
        self.assertEqual(result["complete"], result["is_done"])
        
        # Legacy response format fields
        self.assertIn("html", result)
        self.assertIn("warnings", result)
    
    def test_error_details_optional(self):
        """Test that error details are optional"""
        with patch('agent.dsl_executor._executor') as mock_executor:
            mock_response = DSLResponse(
                success=False,
                error={
                    "code": "EXECUTION_ERROR",
                    "message": "General execution error"
                    # No details field
                },
                observation={"url": "https://example.com"}
            )
            mock_executor.execute_dsl.return_value = mock_response
            
            payload = {"actions": [{"action": "click", "target": "invalid"}]}
            result = execute_enhanced_dsl(payload)
        
        self.assertFalse(result["success"])
        self.assertIn("error", result)
        self.assertIn("code", result["error"])
        self.assertIn("message", result["error"])
        # Details field is optional
        # self.assertIn("details", result["error"])  # This should NOT be required
    
    def test_warnings_format(self):
        """Test warnings array format"""
        with patch('agent.dsl_executor._executor') as mock_executor:
            mock_response = DSLResponse(
                success=True,
                html="<html></html>",
                warnings=[
                    "INFO:auto:Element catalog refreshed",
                    "WARNING:auto:Slow network detected",
                    "ERROR:auto:Minor issue occurred"
                ]
            )
            mock_executor.execute_dsl.return_value = mock_response
            
            payload = {"actions": [{"action": "refresh_catalog"}]}
            result = execute_enhanced_dsl(payload)
        
        self.assertIn("warnings", result)
        self.assertIsInstance(result["warnings"], list)
        self.assertEqual(len(result["warnings"]), 3)
        
        # Check warning format
        for warning in result["warnings"]:
            self.assertIsInstance(warning, str)
            # Warnings should have severity prefixes
            self.assertTrue(any(warning.startswith(prefix) for prefix in ["INFO:", "WARNING:", "ERROR:"]))
    
    def test_observation_required_fields(self):
        """Test that observation contains required fields"""
        with patch('agent.dsl_executor._executor') as mock_executor:
            mock_response = DSLResponse(
                success=True,
                observation={
                    "url": "https://example.com",
                    "title": "Test Page",
                    "short_summary": "Test summary",
                    "nav_detected": True
                },
                html="<html></html>",
                warnings=[]
            )
            mock_executor.execute_dsl.return_value = mock_response
            
            payload = {"actions": []}
            result = execute_enhanced_dsl(payload)
        
        self.assertIn("observation", result)
        obs = result["observation"]
        
        # Required observation fields
        required_obs_fields = ["url", "title", "short_summary", "nav_detected"]
        for field in required_obs_fields:
            self.assertIn(field, obs)
        
        # Optional observation fields
        optional_obs_fields = ["catalog_version"]
        # These may or may not be present, but if present should be correct type
        if "catalog_version" in obs:
            self.assertIsInstance(obs["catalog_version"], str)
    
    def test_complete_task_response(self):
        """Test response when task is complete"""
        with patch('agent.dsl_executor._executor') as mock_executor:
            mock_response = DSLResponse(
                success=True,
                error=None,
                observation={
                    "url": "https://example.com/success",
                    "title": "Success Page",
                    "short_summary": "Task completed successfully",
                    "nav_detected": True
                },
                is_done=True,
                complete=True,
                html="<html>Success!</html>",
                warnings=["INFO:auto:Task completed successfully"]
            )
            mock_executor.execute_dsl.return_value = mock_response
            
            payload = {"actions": [], "complete": True}
            result = execute_enhanced_dsl(payload)
        
        self.assertTrue(result["success"])
        self.assertTrue(result["is_done"])
        self.assertTrue(result["complete"])
        self.assertIsNone(result.get("error"))
        self.assertIn("observation", result)


if __name__ == "__main__":
    unittest.main()