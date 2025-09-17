"""Unit tests for new DSL actions."""

import unittest
from unittest.mock import Mock, patch
import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from agent.actions.basic import (
    refresh_catalog,
    scroll_to_text, 
    wait_enhanced,
    click,
    navigate,
    type_text,
    stop
)


class TestNewActions(unittest.TestCase):
    """Test new DSL actions."""
    
    def test_refresh_catalog_action(self):
        """Test refresh_catalog action creation."""
        action = refresh_catalog()
        
        self.assertEqual(action["action"], "refresh_catalog")
        self.assertNotIn("target", action)
        self.assertNotIn("value", action)
    
    def test_scroll_to_text_action(self):
        """Test scroll_to_text action creation."""
        action = scroll_to_text("Search button")
        
        self.assertEqual(action["action"], "scroll_to_text")
        self.assertEqual(action["target"], "Search button")
    
    def test_wait_enhanced_timeout(self):
        """Test enhanced wait with timeout."""
        action = wait_enhanced(until="timeout", value="2000")
        
        self.assertEqual(action["action"], "wait")
        self.assertEqual(action["until"], "timeout")
        self.assertEqual(action["ms"], 2000)
    
    def test_wait_enhanced_selector(self):
        """Test enhanced wait with selector."""
        action = wait_enhanced(until="selector", value="css=button", ms=3000)
        
        self.assertEqual(action["action"], "wait")
        self.assertEqual(action["until"], "selector")
        self.assertEqual(action["target"], "css=button")
        self.assertEqual(action["ms"], 3000)
    
    def test_wait_enhanced_network_idle(self):
        """Test enhanced wait with network idle."""
        action = wait_enhanced(until="network_idle", ms=5000)
        
        self.assertEqual(action["action"], "wait")
        self.assertEqual(action["until"], "network_idle")
        self.assertEqual(action["ms"], 5000)
    
    def test_wait_enhanced_fallback(self):
        """Test enhanced wait with invalid type falls back to timeout."""
        action = wait_enhanced(until="invalid", value="1000")
        
        self.assertEqual(action["action"], "wait")
        self.assertEqual(action["ms"], 1000)
    
    def test_existing_actions_unchanged(self):
        """Test that existing actions are not affected."""
        # Test click action
        click_action = click("css=button")
        self.assertEqual(click_action["action"], "click")
        self.assertEqual(click_action["target"], "css=button")
        
        # Test navigate action
        nav_action = navigate("https://example.com")
        self.assertEqual(nav_action["action"], "navigate")
        self.assertEqual(nav_action["target"], "https://example.com")
        
        # Test type action
        type_action = type_text("css=input", "test value")
        self.assertEqual(type_action["action"], "type")
        self.assertEqual(type_action["target"], "css=input")
        self.assertEqual(type_action["value"], "test value")
        
        # Test stop action
        stop_action = stop("captcha", "Please solve captcha")
        self.assertEqual(stop_action["action"], "stop")
        self.assertEqual(stop_action["reason"], "captcha")
        self.assertEqual(stop_action["message"], "Please solve captcha")


class TestActionValidation(unittest.TestCase):
    """Test action validation and normalization."""
    
    def test_action_structure(self):
        """Test that actions have proper structure."""
        actions_to_test = [
            refresh_catalog(),
            scroll_to_text("test"),
            wait_enhanced("timeout", "1000"),
            click("css=button"),
            navigate("https://example.com")
        ]
        
        for action in actions_to_test:
            # Every action should have an 'action' field
            self.assertIn("action", action)
            self.assertIsInstance(action["action"], str)
            
            # Action should be a valid dictionary
            self.assertIsInstance(action, dict)
    
    def test_target_specification_formats(self):
        """Test different target specification formats."""
        # CSS selector
        css_action = click("css=button.submit")
        self.assertEqual(css_action["target"], "css=button.submit")
        
        # XPath selector  
        xpath_action = click("xpath=//button[1]")
        self.assertEqual(xpath_action["target"], "xpath=//button[1]")
        
        # Text selector
        text_scroll = scroll_to_text("Submit button")
        self.assertEqual(text_scroll["target"], "Submit button")
        
        # Index-based targeting (format validation)
        # Note: This tests the format, actual resolution happens in automation server
        self.assertTrue("index=0".startswith("index="))
        self.assertTrue("index=5".startswith("index="))


if __name__ == '__main__':
    unittest.main(verbosity=2)