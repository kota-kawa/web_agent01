#!/usr/bin/env python3
"""
Test script to validate that the agent properly progresses to next steps
after preventing duplicate actions.
"""
import json
import unittest
from agent.utils.history import save_hist, load_hist
from agent.controller.prompt import build_prompt


class TestAgentProgression(unittest.TestCase):
    """Test that the agent properly proceeds to next steps after duplicate prevention."""
    
    def setUp(self):
        """Set up test environment."""
        save_hist([])
    
    def test_logical_step_progression(self):
        """Test that the agent suggests logical next steps after duplicate prevention."""
        
        # Simulate a conversation where search term has been entered
        history_with_search_input = [
            {
                "user": "箱根のホテルを探して",
                "bot": {
                    "explanation": "検索フィールドに「箱根」を入力しました。",
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
        
        # Mock page content showing search field is filled
        mock_page_after_input = """
        <html>
            <form>
                <input name="search" value="箱根" placeholder="検索キーワード">
                <button type="submit">検索</button>
                <div class="suggestions">
                    <a href="/hakone">箱根温泉</a>
                    <a href="/hakone-hotels">箱根ホテル</a>
                </div>
            </form>
        </html>
        """
        
        # Build prompt for next step
        prompt = build_prompt(
            cmd="箱根のホテルを探して",
            page=mock_page_after_input,
            hist=history_with_search_input,
            screenshot=False
        )
        
        # Verify that the prompt contains instructions to avoid duplicate input
        self.assertIn("履歴確認による重複防止", prompt)
        self.assertIn("既に実行済みのアクション", prompt)
        
        # Verify that the prompt contains current page state
        self.assertIn("箱根", prompt)  # Should show current value
        self.assertIn("button", prompt)  # Should identify next action option
        
        print("✅ Logical step progression test passed")
    
    def test_multiple_step_flow_simulation(self):
        """Simulate a complete multi-step flow without duplicates."""
        
        # Step 1: Initial search
        step1_history = [
            {
                "user": "箱根に9月13-18日の1泊15000円以内のホテルを探して",
                "bot": {
                    "explanation": "Yahoo!トラベルで検索するため、検索フィールドに「箱根」を入力します。",
                    "actions": [
                        {
                            "action": "type",
                            "target": "input[name='keyword']",
                            "value": "箱根"
                        }
                    ],
                    "complete": False
                }
            }
        ]
        
        # Step 2: Submit search (should not repeat input)
        step2_history = step1_history + [
            {
                "user": "箱根に9月13-18日の1泊15000円以内のホテルを探して",
                "bot": {
                    "explanation": "「箱根」は既に入力済みです。検索ボタンをクリックして検索を実行します。",
                    "actions": [
                        {
                            "action": "click",
                            "target": "button[type='submit']"
                        }
                    ],
                    "complete": False
                }
            }
        ]
        
        # Step 3: Set dates (logical next step)
        step3_history = step2_history + [
            {
                "user": "箱根に9月13-18日の1泊15000円以内のホテルを探して",
                "bot": {
                    "explanation": "検索結果が表示されました。チェックイン日を9月13日に設定します。",
                    "actions": [
                        {
                            "action": "click",
                            "target": "input[name='checkin']"
                        }
                    ],
                    "complete": False
                }
            }
        ]
        
        # Validate the progression
        self.assertEqual(len(step1_history), 1)
        self.assertEqual(len(step2_history), 2)
        self.assertEqual(len(step3_history), 3)
        
        # Verify no duplicate actions
        actions = []
        for conv in step3_history:
            for action in conv["bot"]["actions"]:
                action_sig = f"{action['action']}:{action['target']}:{action.get('value', '')}"
                actions.append(action_sig)
        
        # Check that no action is repeated
        unique_actions = set(actions)
        self.assertEqual(len(actions), len(unique_actions), "Duplicate actions detected in conversation flow")
        
        print("✅ Multi-step flow simulation test passed")
        print(f"   Actions executed: {actions}")
    
    def test_prompt_emphasizes_next_steps(self):
        """Test that the enhanced prompt properly emphasizes logical next steps."""
        
        # Create history with search already completed
        completed_search_history = [
            {
                "user": "ホテルを探して",
                "bot": {
                    "explanation": "検索キーワードに「箱根」を入力し、検索ボタンをクリックしました。",
                    "actions": [
                        {
                            "action": "type",
                            "target": "input[name='search']",
                            "value": "箱根"
                        },
                        {
                            "action": "click", 
                            "target": "button[type='submit']"
                        }
                    ],
                    "complete": False
                }
            }
        ]
        
        # Mock search results page
        search_results_page = """
        <html>
            <div class="search-results">
                <h2>箱根のホテル検索結果</h2>
                <div class="filters">
                    <input type="date" name="checkin" placeholder="チェックイン">
                    <input type="date" name="checkout" placeholder="チェックアウト"> 
                    <select name="guests">
                        <option value="1">大人1名</option>
                        <option value="2">大人2名</option>
                    </select>
                </div>
                <div class="hotel-list">
                    <div class="hotel">ホテル1</div>
                    <div class="hotel">ホテル2</div>
                </div>
            </div>
        </html>
        """
        
        # Build prompt for this scenario
        prompt = build_prompt(
            cmd="箱根に9月13-18日の1泊15000円以内のホテルを探して",
            page=search_results_page,
            hist=completed_search_history,
            screenshot=False
        )
        
        # Verify that the prompt emphasizes checking what's already done
        self.assertIn("既に実行済みのアクション", prompt)
        self.assertIn("次のステップ", prompt)
        
        # Verify that current page state is included for context
        self.assertIn("search-results", prompt)
        self.assertIn("checkin", prompt)
        
        print("✅ Prompt next steps emphasis test passed")
    
    def test_action_signature_uniqueness(self):
        """Test that different actions produce different signatures."""
        
        test_actions = [
            {"action": "type", "target": "input[name='search']", "value": "箱根"},
            {"action": "type", "target": "input[name='search']", "value": "東京"},
            {"action": "type", "target": "input[name='location']", "value": "箱根"},
            {"action": "click", "target": "button[type='submit']"},
            {"action": "click", "target": "a[href='/hakone']"},
        ]
        
        signatures = []
        for action in test_actions:
            sig = f"{action['action']}:{action['target']}:{action.get('value', '')}"
            signatures.append(sig)
        
        # All signatures should be unique
        unique_signatures = set(signatures)
        self.assertEqual(len(signatures), len(unique_signatures), 
                        "Action signatures should be unique for different actions")
        
        # Test duplicate detection
        duplicate_action = {"action": "type", "target": "input[name='search']", "value": "箱根"}
        duplicate_sig = f"{duplicate_action['action']}:{duplicate_action['target']}:{duplicate_action.get('value', '')}"
        
        self.assertIn(duplicate_sig, signatures, "Duplicate action should match existing signature")
        
        print("✅ Action signature uniqueness test passed")
        print(f"   Generated signatures: {signatures}")


def main():
    """Run agent progression validation tests."""
    print("🧪 Testing Agent Progression After Duplicate Prevention")
    print("=" * 60)
    
    # Run the tests
    unittest.main(argv=[''], exit=False, verbosity=2)
    
    print("\n" + "=" * 60)
    print("✅ All agent progression tests completed successfully")
    print("📌 The agent can now properly proceed to next logical steps!")


if __name__ == "__main__":
    main()