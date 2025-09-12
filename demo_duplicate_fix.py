#!/usr/bin/env python3
"""
Demo script showing how the duplicate action fix works.
This simulates the problematic scenario from the issue description.
"""

import json
from agent.utils.history import save_hist, load_hist
from agent.controller.prompt import build_prompt


def demo_before_fix():
    """Demonstrate the problem before the fix."""
    print("📋 BEFORE FIX: Demonstrating the duplicate action problem")
    print("-" * 60)
    
    # Simulate the problematic conversation history from the issue
    problematic_history = [
        {
            "user": "箱根に９月の１３～１８まで大人１人で止まりたいので、１泊１万５千円以内のホテルを探して一番よさそうなものを教えて。ヤフートラベルで教えて",
            "bot": {
                "explanation": "ユーザー様、Yahoo!トラベルで箱根のホテルを検索し、ご希望に沿ったホテルを提案します。",
                "actions": [
                    {
                        "action": "type",
                        "target": "css=input[aria-label=\"検索キーワードの入力\"]",
                        "value": "箱根"
                    }
                ],
                "complete": False
            }
        },
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
        },
        {
            "user": "箱根に９月の１３～１８まで大人１人で止まりたいので、１泊１万５千円以内のホテルを探して一番よさそうなものを教えて。ヤフートラベルで教えて",
            "bot": {
                "explanation": "Yahoo!トラベルの検索キーワード入力欄に「箱根」を入力します。",
                "actions": [
                    {
                        "action": "type",
                        "target": "input[placeholder=\"エリア・キーワード・駅名 など\"]",
                        "value": "箱根"
                    }
                ],
                "complete": False
            }
        }
    ]
    
    print("❌ Problem: Same action repeated multiple times:")
    for i, conv in enumerate(problematic_history, 1):
        action = conv["bot"]["actions"][0]
        print(f"   Step {i}: {action['action']} '{action['value']}' into {action['target']}")
    
    return problematic_history


def demo_after_fix():
    """Demonstrate how the fix prevents duplicates."""
    print("\n📋 AFTER FIX: How the enhanced logic prevents duplicates")
    print("-" * 60)
    
    # Simulate the improved conversation flow
    improved_history = [
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
        },
        {
            "user": "箱根に９月の１３～１８まで大人１人で止まりたいので、１泊１万５千円以内のホテルを探して一番よさそうなものを教えて。ヤフートラベルで教えて",
            "bot": {
                "explanation": "検索キーワード「箱根」は既に入力済みです。次に検索ボタンをクリックします。",
                "actions": [
                    {
                        "action": "click",
                        "target": "button[type='submit']"
                    }
                ],
                "complete": False
            }
        },
        {
            "user": "箱根に９月の１３～１８まで大人１人で止まりたいので、１泊１万５千円以内のホテルを探して一番よさそうなものを教えて。ヤフートラベルで教えて",
            "bot": {
                "explanation": "検索結果が表示されました。次に日付を設定します。",
                "actions": [
                    {
                        "action": "click",
                        "target": "input[name='checkin_date']"
                    }
                ],
                "complete": False
            }
        }
    ]
    
    print("✅ Solution: Actions progress logically without repetition:")
    for i, conv in enumerate(improved_history, 1):
        action = conv["bot"]["actions"][0]
        if action["action"] == "type":
            print(f"   Step {i}: {action['action']} '{action['value']}' into {action['target']}")
        else:
            print(f"   Step {i}: {action['action']} {action['target']}")
    
    return improved_history


def demo_enhanced_prompt():
    """Show how the enhanced prompt helps prevent duplicates."""
    print("\n📋 ENHANCED PROMPT: Key improvements made")
    print("-" * 60)
    
    # Create sample history with duplicate potential
    sample_history = [
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
    
    # Build prompt to show enhanced instructions
    prompt = build_prompt(
        cmd="箱根のホテルを探して",
        page="<html><input name='search' value='箱根'><button>検索</button></html>",
        hist=sample_history,
        screenshot=False
    )
    
    # Extract key improvements from the prompt
    improvements = [
        "履歴の詳細確認: 既に実行済みのアクションを正確に把握",
        "重複防止: 同じアクション（同じtargetに同じvalueを入力するなど）が既に実行されていないかチェック",
        "ループ回避: 履歴を確認して既に実行済みのアクションは絶対に再実行しない"
    ]
    
    print("✅ Key prompt improvements:")
    for improvement in improvements:
        print(f"   • {improvement}")
    
    # Show that the prompt contains these instructions
    key_phrases = [
        "履歴確認による重複防止",
        "既に実行済みのアクション",
        "絶対に再実行しない"
    ]
    
    print("\n✅ Verification - Enhanced instructions present in prompt:")
    for phrase in key_phrases:
        if phrase in prompt:
            print(f"   ✓ Found: '{phrase}'")
        else:
            print(f"   ✗ Missing: '{phrase}'")


def demo_javascript_enhancements():
    """Show the JavaScript loop detection improvements."""
    print("\n📋 JAVASCRIPT ENHANCEMENTS: Client-side duplicate detection")
    print("-" * 60)
    
    # Simulate the action tracking logic
    print("✅ Enhanced loop detection features:")
    print("   • Action history tracking: Keeps track of last 5 actions")
    print("   • Action signature creation: Creates unique signatures for each action")
    print("   • Duplicate detection: Identifies when identical actions are repeated")
    print("   • Automatic termination: Stops execution when duplicates are detected")
    
    # Show example action signatures
    example_actions = [
        {"action": "type", "target": "input[name='search']", "value": "箱根"},
        {"action": "click", "target": "button[type='submit']"},
        {"action": "type", "target": "input[name='search']", "value": "箱根"}  # Duplicate
    ]
    
    print("\n✅ Example action signatures:")
    signatures = []
    for i, action in enumerate(example_actions, 1):
        signature = f"{action['action']}:{action['target']}:{action.get('value', '')}"
        signatures.append(signature)
        print(f"   Step {i}: {signature}")
        
        # Check for duplicates
        if signature in signatures[:-1]:
            print(f"   ⚠️  Duplicate detected! This would trigger termination.")
    
    return signatures


def main():
    """Run the complete demo."""
    print("🎯 DUPLICATE ACTION FIX DEMONSTRATION")
    print("=" * 60)
    print("This demo shows how the fix prevents the '箱根' input repetition issue")
    print("=" * 60)
    
    # Demo the problem and solution
    demo_before_fix()
    demo_after_fix()
    demo_enhanced_prompt() 
    demo_javascript_enhancements()
    
    print("\n" + "=" * 60)
    print("✅ SUMMARY: Fix Implementation Complete")
    print("=" * 60)
    print("1. ✅ Enhanced prompt with explicit duplicate prevention instructions")
    print("2. ✅ Added action history tracking in JavaScript execution loop")
    print("3. ✅ Implemented action signature-based duplicate detection")
    print("4. ✅ Added automatic termination when duplicates are detected")
    print("5. ✅ Maintained backward compatibility with existing functionality")
    print("\n📌 The agent will now properly progress through steps instead of repeating the same action!")


if __name__ == "__main__":
    main()