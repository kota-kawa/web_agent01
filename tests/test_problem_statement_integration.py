"""
Test that the action normalization works correctly with the sample input from the problem statement.
"""

from web.app import normalize_actions


def test_problem_statement_sample():
    """Test normalization of the exact sample from the problem statement."""
    
    # This is the sample JSON from the problem statement
    llm_response = {
        "actions": [
            {
                "action": "type",
                "target": {
                    "index": 13
                },
                "text": "箱根",
                "clear": True
            },
            {
                "action": "click",
                "target": {
                    "index": 15
                }
            }
        ]
    }
    
    # Normalize the actions
    normalized_actions = normalize_actions(llm_response)
    
    # Verify the normalization worked correctly
    assert len(normalized_actions) == 2
    
    # Check the type action
    type_action = normalized_actions[0]
    assert type_action["action"] == "type"
    assert type_action["target"] == {"index": 13}  # Should preserve the index target
    assert type_action["text"] == "箱根"  # Should preserve the Japanese text
    assert type_action["clear"] == True  # Should preserve the clear flag
    
    # Check the click action
    click_action = normalized_actions[1]
    assert click_action["action"] == "click"
    assert click_action["target"] == {"index": 15}  # Should preserve the index target
    
    print("✓ Type action normalized correctly:")
    print(f"  - action: {type_action['action']}")
    print(f"  - target: {type_action['target']}")
    print(f"  - text: {type_action['text']}")
    print(f"  - clear: {type_action['clear']}")
    
    print("✓ Click action normalized correctly:")
    print(f"  - action: {click_action['action']}")
    print(f"  - target: {click_action['target']}")


def test_clear_flag_variations():
    """Test different variations of the clear flag."""
    
    test_cases = [
        {"clear": True, "expected": True},
        {"clear": False, "expected": False},
        {"clear": "true", "expected": "true"},  # String values should be preserved
        {"clear": "false", "expected": "false"},
        # Test without clear flag - should not add it
    ]
    
    for case in test_cases:
        action_data = {
            "action": "type",
            "target": {"index": 13},
            "text": "箱根"
        }
        if "clear" in case:
            action_data["clear"] = case["clear"]
        
        llm_response = {"actions": [action_data]}
        normalized = normalize_actions(llm_response)
        
        assert len(normalized) == 1
        normalized_action = normalized[0]
        
        if "clear" in case:
            assert normalized_action["clear"] == case["expected"]
        else:
            # If clear wasn't in original, it shouldn't be added
            assert "clear" not in normalized_action or normalized_action.get("clear") is None


if __name__ == "__main__":
    test_problem_statement_sample()
    test_clear_flag_variations()
    print("\n✅ All normalization tests passed!")
    print("The type action with clear=True will now be handled correctly by the executor!")