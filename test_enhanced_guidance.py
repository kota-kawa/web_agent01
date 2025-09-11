#!/usr/bin/env python3
"""
Test script to validate the enhanced guidance functions for better LLM decision making.
"""
import sys

# Add vnc directory to path for imports
sys.path.insert(0, 'vnc')

def test_action_guidance():
    """Test the action-specific guidance function."""
    print("Testing action-specific guidance...")
    
    from automation_server import _get_action_guidance
    
    test_cases = [
        {
            'action': 'hover',
            'target': '#menu-button',
            'error_msg': 'Hover failed - Original: Element timeout, Force: Force hover failed, JS: JS mouseover failed',
            'expected_keywords': ['click', 'menu', 'wait', 'stabilize']
        },
        {
            'action': 'select_option',
            'target': '#dropdown',
            'error_msg': 'Select failed - Original: timeout waiting for element, Label: Label select failed',
            'expected_keywords': ['click', 'dropdown', 'selector', 'timeout', 'wait']
        },
        {
            'action': 'press_key',
            'target': '#input-field',
            'error_msg': 'Key press failed - Original: element not visible, Focus: Focus failed',
            'expected_keywords': ['type', 'click', 'focused', 'visible']
        },
        {
            'action': 'click',
            'target': '.submit-btn',
            'error_msg': 'Click failed - Original: not found in timeout, Force: timeout',
            'expected_keywords': ['selector', 'text', 'wait', 'timeout']
        }
    ]
    
    passed = 0
    for case in test_cases:
        guidance = _get_action_guidance(case['action'], case['target'], case['error_msg'])
        
        # Check if guidance contains expected keywords
        guidance_lower = guidance.lower()
        found_keywords = [kw for kw in case['expected_keywords'] if kw in guidance_lower]
        
        if len(found_keywords) >= 2:  # At least 2 relevant keywords
            print(f"  ✅ {case['action']} guidance contains relevant keywords: {found_keywords}")
            passed += 1
        else:
            print(f"  ❌ {case['action']} guidance lacks expected keywords")
            print(f"      Guidance: {guidance}")
            print(f"      Expected keywords: {case['expected_keywords']}")
            print(f"      Found: {found_keywords}")
    
    print(f"Action guidance: {passed}/{len(test_cases)} tests passed\n")
    return passed == len(test_cases)


def test_basic_guidance():
    """Test the basic guidance function for simpler errors."""
    print("Testing basic guidance...")
    
    from automation_server import _get_basic_guidance
    
    test_cases = [
        {
            'action': 'click',
            'error_msg': 'timeout waiting for element',
            'expected_keywords': ['wait', 'load']
        },
        {
            'action': 'type',
            'error_msg': 'element not found',
            'expected_keywords': ['selector', 'text', 'css']
        },
        {
            'action': 'hover',
            'error_msg': 'network connection failed',
            'expected_keywords': ['network', 'retry']
        },
        {
            'action': 'select_option',
            'error_msg': 'some generic error',
            'expected_keywords': ['alternative', 'stabilize']
        }
    ]
    
    passed = 0
    for case in test_cases:
        guidance = _get_basic_guidance(case['action'], case['error_msg'])
        
        # Check if guidance contains expected keywords
        guidance_lower = guidance.lower()
        found_keywords = [kw for kw in case['expected_keywords'] if kw in guidance_lower]
        
        if len(found_keywords) >= 1:  # At least 1 relevant keyword
            print(f"  ✅ {case['action']} basic guidance contains relevant keywords: {found_keywords}")
            passed += 1
        else:
            print(f"  ❌ {case['action']} basic guidance lacks expected keywords")
            print(f"      Guidance: {guidance}")
            print(f"      Expected keywords: {case['expected_keywords']}")
            print(f"      Found: {found_keywords}")
    
    print(f"Basic guidance: {passed}/{len(test_cases)} tests passed\n")
    return passed == len(test_cases)


def test_guidance_integration():
    """Test that guidance functions integrate properly with error reporting."""
    print("Testing guidance integration...")
    
    from automation_server import _get_action_guidance, _get_basic_guidance
    
    # Test comprehensive guidance for different scenarios
    scenarios = [
        {
            'type': 'enhanced',
            'action': 'hover',
            'target': '#tooltip-trigger',
            'error': 'Hover failed - Original: element not interactable, Force: force failed, JS: script error',
            'description': 'Enhanced hover failure'
        },
        {
            'type': 'basic', 
            'action': 'click',
            'error': 'element timeout',
            'description': 'Basic click timeout'
        },
        {
            'type': 'enhanced',
            'action': 'select_option',
            'target': 'select[name="country"]',
            'error': 'Select failed - Original: option not found, Label: label not found, JS: no options',
            'description': 'Enhanced select failure'
        }
    ]
    
    passed = 0
    for scenario in scenarios:
        try:
            if scenario['type'] == 'enhanced':
                guidance = _get_action_guidance(scenario['action'], scenario['target'], scenario['error'])
            else:
                guidance = _get_basic_guidance(scenario['action'], scenario['error'])
            
            # Check that guidance is meaningful (not empty, has useful content)
            if guidance and len(guidance) > 20 and ('try' in guidance.lower() or 'consider' in guidance.lower()):
                print(f"  ✅ {scenario['description']} -> meaningful guidance provided")
                passed += 1
            else:
                print(f"  ❌ {scenario['description']} -> guidance too generic or empty")
                print(f"      Guidance: {guidance}")
        except Exception as e:
            print(f"  ❌ {scenario['description']} -> exception: {e}")
    
    print(f"Guidance integration: {passed}/{len(scenarios)} tests passed\n")
    return passed == len(scenarios)


def main():
    """Run all guidance tests."""
    print("🧪 Enhanced Guidance Functions Validation")
    print("=" * 50)
    
    tests = [
        test_action_guidance,
        test_basic_guidance,
        test_guidance_integration
    ]
    
    passed_tests = 0
    for test_func in tests:
        try:
            if test_func():
                passed_tests += 1
            else:
                print(f"❌ {test_func.__name__} failed")
        except Exception as e:
            print(f"❌ {test_func.__name__} failed with exception: {e}")
    
    print("=" * 50)
    if passed_tests == len(tests):
        print("🎉 All enhanced guidance tests passed!")
        print("✅ LLM guidance functions are working correctly.")
        return 0
    else:
        print("⚠️  Some enhanced guidance tests failed.")
        print("❌ Please review the implementation and fix any issues.")
        return 1


if __name__ == "__main__":
    exit(main())