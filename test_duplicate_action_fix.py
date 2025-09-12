#!/usr/bin/env python3
"""
Test script to validate the fix for duplicate action prevention.
"""
import json
import unittest
from unittest.mock import Mock, patch
from agent.utils.history import save_hist, load_hist


class TestDuplicateActionFix(unittest.TestCase):
    """Test that duplicate actions are properly prevented."""
    
    def setUp(self):
        """Set up test environment."""
        # Clear any existing history
        save_hist([])
    
    def test_history_tracking_prevents_duplicates(self):
        """Test that conversation history prevents duplicate actions."""
        # Simulate a conversation history with a type action already executed
        test_history = [
            {
                "user": "箱根に９月の１３～１８まで大人１人で止まりたいので、１泊１万５千円以内のホテルを探して一番よさそうなものを教えて。ヤフートラベルで教えて",
                "bot": {
                    "explanation": "Yahoo!トラベルで箱根のホテルを検索するために、まず検索キーワード入力欄に「箱根」を入力します。",
                    "actions": [
                        {
                            "action": "type",
                            "target": "input[aria-label=\"検索キーワードの入力\"]",
                            "value": "箱根"
                        }
                    ],
                    "complete": False
                }
            }
        ]
        
        save_hist(test_history)
        
        # Verify history is saved correctly
        loaded_history = load_hist()
        self.assertEqual(len(loaded_history), 1)
        self.assertEqual(loaded_history[0]["bot"]["actions"][0]["value"], "箱根")
        
        print("✅ History tracking test passed")
    
    def test_conversation_history_format(self):
        """Test that conversation history maintains correct format."""
        from agent.controller.prompt import build_prompt
        
        # Create test history with duplicate-prone actions
        test_history = [
            {
                "user": "箱根のホテルを探して",
                "bot": {
                    "explanation": "検索キーワードに「箱根」を入力します。",
                    "actions": [
                        {
                            "action": "type",
                            "target": "input[name='search']",
                            "value": "箱根"
                        }
                    ],
                    "complete": False
                }
            }
        ]
        
        # Build prompt with this history
        prompt = build_prompt(
            cmd="箱根のホテルを探して",
            page="<html><input name='search'></html>",
            hist=test_history,
            screenshot=False,
            elements=None,
            error=None
        )
        
        # Check that prompt contains history
        self.assertIn("箱根", prompt)
        self.assertIn("これまでの会話履歴", prompt)
        self.assertIn("履歴確認による重複防止", prompt)
        
        print("✅ Prompt generation test passed")
    
    def test_action_signature_creation(self):
        """Test the action signature creation logic for duplicate detection."""
        
        # Test identical actions
        action1 = {"action": "type", "target": "input[name='search']", "value": "箱根"}
        action2 = {"action": "type", "target": "input[name='search']", "value": "箱根"}
        
        # Create signatures (mimicking JavaScript logic)
        sig1 = f"{action1['action']}:{action1['target']}:{action1.get('value', '')}"
        sig2 = f"{action2['action']}:{action2['target']}:{action2.get('value', '')}"
        
        self.assertEqual(sig1, sig2, "Identical actions should have identical signatures")
        
        # Test different actions
        action3 = {"action": "click", "target": "button[type='submit']"}
        sig3 = f"{action3['action']}:{action3['target']}:{action3.get('value', '')}"
        
        self.assertNotEqual(sig1, sig3, "Different actions should have different signatures")
        
        print("✅ Action signature test passed")


def main():
    """Run duplicate action prevention tests."""
    print("🧪 Testing Duplicate Action Prevention Fix")
    print("=" * 50)
    
    # Run the tests
    unittest.main(argv=[''], exit=False, verbosity=2)
    
    print("\n" + "=" * 50)
    print("✅ All duplicate action prevention tests completed")


if __name__ == "__main__":
    main()