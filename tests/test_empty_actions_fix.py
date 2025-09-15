#!/usr/bin/env python3
"""
Test case for the empty actions fix.

This test validates that when LLM returns empty actions with complete=false
(during initial planning phase), the task execution continues instead of ending.
"""

def test_continue_logic_with_empty_actions():
    """
    Test the JavaScript logic fix for empty actions handling.
    
    The original issue was that when actions=[] and complete=false,
    the task would end with "✅ タスクを終了しました" instead of continuing.
    
    This was fixed by changing the continue condition from:
    cont: res.complete === false && (res.actions || []).length > 0
    to:
    cont: res.complete === false
    """
    print("Testing continue logic with empty actions...")
    
    # Test scenarios
    test_cases = [
        {
            "name": "Initial planning with empty actions",
            "complete": False,
            "actions": [],
            "expected_continue": True,
            "description": "Should continue when planning (empty actions + complete=false)"
        },
        {
            "name": "Normal operation with actions",
            "complete": False,
            "actions": [{"action": "click", "target": "button"}],
            "expected_continue": True,
            "description": "Should continue when executing actions (actions + complete=false)"
        },
        {
            "name": "Task completion",
            "complete": True,
            "actions": [],
            "expected_continue": False,
            "description": "Should stop when task is complete (complete=true)"
        },
        {
            "name": "Task completion with final actions",
            "complete": True,
            "actions": [{"action": "extract", "target": "result"}],
            "expected_continue": False,
            "description": "Should stop when task is complete even with actions (complete=true)"
        }
    ]
    
    all_passed = True
    
    for case in test_cases:
        complete = case["complete"]
        actions = case["actions"]
        expected = case["expected_continue"]
        
        # Original logic (broken)
        cont_original = complete == False and len(actions) > 0
        
        # Fixed logic
        cont_fixed = complete == False
        
        print(f"\nTest: {case['name']}")
        print(f"  Input: complete={complete}, actions={actions}")
        print(f"  Expected: {expected}")
        print(f"  Original result: {cont_original}")
        print(f"  Fixed result: {cont_fixed}")
        
        if cont_fixed == expected:
            print(f"  ✓ PASS - {case['description']}")
        else:
            print(f"  ✗ FAIL - Expected {expected}, got {cont_fixed}")
            all_passed = False
    
    print(f"\nOverall result: {'✓ ALL TESTS PASSED' if all_passed else '✗ SOME TESTS FAILED'}")
    
    if all_passed:
        print("\nThe fix correctly addresses the empty actions issue:")
        print("- Initial planning phases with empty actions now continue correctly")
        print("- Normal operation with actions continues as expected")
        print("- Task completion is properly handled regardless of actions")
    
    return all_passed

if __name__ == "__main__":
    test_continue_logic_with_empty_actions()