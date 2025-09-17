#!/usr/bin/env python3
"""
Simple E2E test for index-based element specification workflow
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import unittest
from unittest.mock import Mock, patch
from agent.dsl_executor import execute_enhanced_dsl


class TestIndexE2E(unittest.TestCase):
    """End-to-end test for index-based workflow"""
    
    def test_index_workflow_e2e(self):
        """Test complete workflow: scroll_to_text → refresh_catalog → click index"""
        
        # This is a simplified E2E test that demonstrates the workflow
        # In a real environment, this would run against actual browser automation
        
        # Test 1: scroll_to_text action
        with patch('agent.dsl_executor._executor') as mock_executor:
            from agent.dsl_executor import DSLResponse
            
            mock_executor.execute_dsl.return_value = DSLResponse(
                success=True,
                observation={
                    "url": "https://example.com",
                    "title": "E2E Test Page",
                    "short_summary": "Scrolled to target text",
                    "catalog_version": "e2e_v1",
                    "nav_detected": False
                },
                html="<html>...</html>",
                warnings=["INFO:auto:Scrolled to text 'Submit'"]
            )
            
            result1 = execute_enhanced_dsl({
                "actions": [{"action": "scroll_to_text", "text": "Submit"}]
            })
            
            self.assertTrue(result1["success"])
            self.assertIn("observation", result1)
        
        # Test 2: refresh_catalog action
        with patch('agent.dsl_executor._executor') as mock_executor:
            from agent.dsl_executor import DSLResponse
            
            mock_executor.execute_dsl.return_value = DSLResponse(
                success=True,
                observation={
                    "url": "https://example.com",
                    "title": "E2E Test Page",
                    "short_summary": "1 interactive element (1 button)",
                    "catalog_version": "e2e_v1_updated",
                    "nav_detected": False
                },
                html="<html>...</html>",
                warnings=["INFO:auto:Element catalog refreshed"]
            )
            
            result2 = execute_enhanced_dsl({
                "actions": [{"action": "refresh_catalog"}]
            })
            
            self.assertTrue(result2["success"])
            self.assertIn("observation", result2)
        
        # Test 3: click index=0 action (successful completion)
        with patch('agent.dsl_executor._executor') as mock_executor:
            from agent.dsl_executor import DSLResponse
            
            mock_executor.execute_dsl.return_value = DSLResponse(
                success=True,
                observation={
                    "url": "https://example.com/submitted",
                    "title": "Success Page",
                    "short_summary": "Form submitted successfully",
                    "catalog_version": "e2e_v1_final",
                    "nav_detected": True
                },
                is_done=True,
                complete=True,
                html="<html>Success!</html>",
                warnings=["INFO:auto:Button clicked successfully"]
            )
            
            result3 = execute_enhanced_dsl({
                "actions": [{"action": "click", "target": "index=0"}],
                "complete": True
            })
            
            self.assertTrue(result3["success"])
            self.assertTrue(result3["complete"])
            self.assertTrue(result3["is_done"])
            self.assertIn("observation", result3)
            self.assertTrue(result3["observation"]["nav_detected"])
    
    def test_error_recovery_workflow(self):
        """Test error recovery: CATALOG_OUTDATED → refresh_catalog → retry"""
        
        with patch('agent.dsl_executor._executor') as mock_executor:
            from agent.dsl_executor import DSLResponse
            
            # Step 1: Try to click with outdated catalog
            def mock_execute_outdated(payload, expected_version=None, timeout=120):
                return DSLResponse(
                    success=False,
                    error={
                        "code": "CATALOG_OUTDATED",
                        "message": "Catalog version mismatch. Please execute refresh_catalog action.",
                        "details": {
                            "expected": "v1",
                            "current": "v2"
                        }
                    },
                    observation={
                        "url": "https://example.com",
                        "title": "Test Page",
                        "short_summary": "Catalog outdated",
                        "catalog_version": "v2",
                        "nav_detected": False
                    }
                )
            
            mock_executor.execute_dsl.side_effect = mock_execute_outdated
            
            result1 = execute_enhanced_dsl({
                "actions": [{"action": "click", "target": "index=0"}]
            }, expected_catalog_version="v1")
            
            self.assertFalse(result1["success"])
            self.assertEqual(result1["error"]["code"], "CATALOG_OUTDATED")
            self.assertIn("refresh_catalog", result1["error"]["message"])
            
            # Step 2: Refresh catalog and retry
            def mock_execute_success(payload, expected_version=None, timeout=120):
                if payload["actions"][0]["action"] == "refresh_catalog":
                    return DSLResponse(
                        success=True,
                        observation={
                            "url": "https://example.com",
                            "title": "Test Page", 
                            "short_summary": "Catalog updated",
                            "catalog_version": "v3",
                            "nav_detected": False
                        },
                        html="<html>...</html>",
                        warnings=["INFO:auto:Catalog refreshed"]
                    )
                elif payload["actions"][0]["action"] == "click":
                    return DSLResponse(
                        success=True,
                        observation={
                            "url": "https://example.com",
                            "title": "Test Page",
                            "short_summary": "Click successful",
                            "catalog_version": "v3",
                            "nav_detected": False
                        },
                        html="<html>Success</html>",
                        warnings=["INFO:auto:Element clicked"]
                    )
                return DSLResponse(success=False)
            
            mock_executor.execute_dsl.side_effect = mock_execute_success
            
            # Refresh catalog
            result2 = execute_enhanced_dsl({
                "actions": [{"action": "refresh_catalog"}]
            })
            
            self.assertTrue(result2["success"])
            
            # Retry click with updated catalog
            result3 = execute_enhanced_dsl({
                "actions": [{"action": "click", "target": "index=0"}]
            }, expected_catalog_version="v3")
            
            self.assertTrue(result3["success"])
    
    def test_legacy_fallback(self):
        """Test fallback to legacy CSS selector when index fails"""
        
        with patch('agent.dsl_executor._executor') as mock_executor:
            from agent.dsl_executor import DSLResponse
            
            # Mock ELEMENT_NOT_FOUND for index, then fallback to CSS
            def mock_execute_fallback(payload, expected_version=None, timeout=120):
                action = payload["actions"][0]
                if action["target"] == "index=99":
                    return DSLResponse(
                        success=False,
                        error={
                            "code": "ELEMENT_NOT_FOUND",
                            "message": "Element with index 99 not found in catalog.",
                            "details": {
                                "index": 99,
                                "available_indices": [0, 1, 2]
                            }
                        },
                        observation={"url": "https://example.com"}
                    )
                elif action["target"] == "css=button.submit":
                    return DSLResponse(
                        success=True,
                        observation={
                            "url": "https://example.com",
                            "title": "Test Page",
                            "short_summary": "CSS fallback successful",
                            "nav_detected": False
                        },
                        html="<html>Success</html>",
                        warnings=["INFO:auto:CSS selector worked"]
                    )
                return DSLResponse(success=False)
            
            mock_executor.execute_dsl.side_effect = mock_execute_fallback
            
            # Try index first (fails)
            result1 = execute_enhanced_dsl({
                "actions": [{"action": "click", "target": "index=99"}]
            })
            
            self.assertFalse(result1["success"])
            self.assertEqual(result1["error"]["code"], "ELEMENT_NOT_FOUND")
            
            # Fallback to CSS (succeeds)
            result2 = execute_enhanced_dsl({
                "actions": [{"action": "click", "target": "css=button.submit"}]
            })
            
            self.assertTrue(result2["success"])
            self.assertIn("CSS selector worked", result2["warnings"][0])


if __name__ == "__main__":
    unittest.main()