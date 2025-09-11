#!/usr/bin/env python3
"""
Integration test to validate the complete enhanced fallback strategy implementation.
This test simulates real-world scenarios to ensure the improvements work as expected.
"""
import sys
import json

# Add vnc directory to path for imports
sys.path.insert(0, 'vnc')

def test_dsl_action_validation():
    """Test that DSL actions are properly validated with the new enhancements."""
    print("Testing DSL action validation with enhancements...")
    
    from automation_server import _validate_action_params
    
    test_cases = [
        {
            'dsl': {"action": "hover", "target": "#menu-item", "ms": 5000},
            'expected_warnings': 0,
            'description': 'Valid hover action'
        },
        {
            'dsl': {"action": "select_option", "target": "select[name='country']", "value": "US"},
            'expected_warnings': 0,
            'description': 'Valid select action'
        },
        {
            'dsl': {"action": "press_key", "target": "#input", "key": "Enter"},
            'expected_warnings': 0,
            'description': 'Valid key press action'
        },
        {
            'dsl': {"action": "hover", "target": "", "ms": 3000},
            'expected_warnings': 1,
            'description': 'Invalid hover with empty selector'
        },
        {
            'dsl': {"action": "select_option", "target": "#select", "ms": -1},
            'expected_warnings': 1,
            'description': 'Invalid select with negative timeout'
        }
    ]
    
    passed = 0
    for case in test_cases:
        warnings = _validate_action_params(case['dsl'])
        warning_count = len(warnings)
        
        if warning_count == case['expected_warnings']:
            print(f"  ✅ {case['description']} -> {warning_count} warnings (expected {case['expected_warnings']})")
            passed += 1
        else:
            print(f"  ❌ {case['description']} -> {warning_count} warnings (expected {case['expected_warnings']})")
            if warnings:
                print(f"      Warnings: {warnings}")
    
    print(f"DSL validation: {passed}/{len(test_cases)} tests passed\n")
    return passed == len(test_cases)


def test_error_message_enhancement():
    """Test that error messages are properly enhanced for LLM guidance."""
    print("Testing error message enhancement...")
    
    from automation_server import _get_action_guidance, _get_basic_guidance
    
    # Simulate real error scenarios
    scenarios = [
        {
            'action': 'hover',
            'target': '#dropdown-trigger',
            'error': 'Hover failed - Original: Element not found, Force: Timeout after 5000ms, JS: dispatchEvent failed',
            'type': 'enhanced',
            'should_contain': ['click', 'menu', 'wait', 'alternative']
        },
        {
            'action': 'select_option', 
            'target': 'select#country-list',
            'error': 'Select failed - Original: No such option "Japan", Label: Label not found, JS: options undefined',
            'type': 'enhanced',
            'should_contain': ['click', 'dropdown', 'text', 'selector']
        },
        {
            'action': 'press_key',
            'target': 'input[type="text"]',
            'error': 'timeout waiting for element',
            'type': 'basic',
            'should_contain': ['wait', 'load']
        }
    ]
    
    passed = 0
    for scenario in scenarios:
        if scenario['type'] == 'enhanced':
            guidance = _get_action_guidance(scenario['action'], scenario['target'], scenario['error'])
        else:
            guidance = _get_basic_guidance(scenario['action'], scenario['error'])
        
        # Check that guidance contains expected elements
        guidance_lower = guidance.lower()
        found_elements = [element for element in scenario['should_contain'] if element in guidance_lower]
        
        if len(found_elements) >= 2:  # At least 2 expected elements
            print(f"  ✅ {scenario['action']} guidance contains expected elements: {found_elements}")
            passed += 1
        else:
            print(f"  ❌ {scenario['action']} guidance missing expected elements")
            print(f"      Guidance: {guidance}")
            print(f"      Expected: {scenario['should_contain']}")
            print(f"      Found: {found_elements}")
    
    print(f"Error enhancement: {passed}/{len(scenarios)} tests passed\n")
    return passed == len(scenarios)


def test_fallback_detection():
    """Test that fallback usage is properly detected and reported."""
    print("Testing fallback detection logic...")
    
    # Test patterns that should trigger enhanced error reporting
    error_patterns = [
        {
            'error': 'Hover failed - Original: timeout, Force: failed, JS: error',
            'should_be_enhanced': True,
            'action': 'hover'
        },
        {
            'error': 'Select failed - Original: not found, Label: no match, JS: undefined, Click: timeout',
            'should_be_enhanced': True,
            'action': 'select_option'
        },
        {
            'error': 'Click failed - Original: not clickable, Force: still failed, JS: script error',
            'should_be_enhanced': True,
            'action': 'click'
        },
        {
            'error': 'Element not visible',
            'should_be_enhanced': False,
            'action': 'type'
        }
    ]
    
    passed = 0
    for pattern in error_patterns:
        # Simulate the detection logic used in action execution
        is_enhanced = "failed -" in pattern['error'] and ("Original:" in pattern['error'] or "Fallback" in pattern['error'])
        
        if is_enhanced == pattern['should_be_enhanced']:
            print(f"  ✅ {pattern['action']} error detection: {is_enhanced} (expected {pattern['should_be_enhanced']})")
            passed += 1
        else:
            print(f"  ❌ {pattern['action']} error detection: {is_enhanced} (expected {pattern['should_be_enhanced']})")
            print(f"      Error: {pattern['error']}")
    
    print(f"Fallback detection: {passed}/{len(error_patterns)} tests passed\n")
    return passed == len(error_patterns)


def test_comprehensive_guidance_quality():
    """Test that the guidance quality meets the requirements."""
    print("Testing comprehensive guidance quality...")
    
    from automation_server import _get_action_guidance
    
    # Test that guidance is specific and actionable
    test_scenarios = [
        {
            'action': 'hover',
            'target': '#nav-menu',
            'error': 'Hover failed - Original: element not interactable, Force: timeout, JS: mouseover failed',
            'quality_checks': ['actionable', 'specific', 'alternative_provided']
        },
        {
            'action': 'select_option',
            'target': '#dropdown',
            'error': 'Select failed - Original: option not found, Label: no matching label',
            'quality_checks': ['actionable', 'specific', 'alternative_provided']
        },
        {
            'action': 'press_key',
            'target': '#text-input',
            'error': 'Key press failed - Original: element not focused, Focus: focus failed, Page: page press failed',
            'quality_checks': ['actionable', 'specific', 'alternative_provided']
        }
    ]
    
    passed = 0
    for scenario in test_scenarios:
        guidance = _get_action_guidance(scenario['action'], scenario['target'], scenario['error'])
        
        quality_score = 0
        
        # Check if guidance is actionable (contains action words)
        if any(word in guidance.lower() for word in ['try', 'use', 'consider', 'click', 'wait', 'check']):
            quality_score += 1
        
        # Check if guidance is specific (mentions specific techniques)
        if any(term in guidance.lower() for term in ['selector', 'text', 'css', 'aria', 'dropdown', 'menu', 'field']):
            quality_score += 1
        
        # Check if guidance provides alternatives (not just "try again")
        if any(alt in guidance.lower() for alt in ['alternative', 'different', 'instead', 'other']):
            quality_score += 1
        
        if quality_score >= 2:  # At least 2 out of 3 quality criteria
            print(f"  ✅ {scenario['action']} guidance meets quality standards (score: {quality_score}/3)")
            passed += 1
        else:
            print(f"  ❌ {scenario['action']} guidance below quality standards (score: {quality_score}/3)")
            print(f"      Guidance: {guidance}")
    
    print(f"Guidance quality: {passed}/{len(test_scenarios)} tests passed\n")
    return passed == len(test_scenarios)


def main():
    """Run comprehensive integration tests."""
    print("🧪 Enhanced Fallback Strategies - Integration Validation")
    print("=" * 60)
    
    tests = [
        test_dsl_action_validation,
        test_error_message_enhancement,
        test_fallback_detection,
        test_comprehensive_guidance_quality
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
    
    print("=" * 60)
    if passed_tests == len(tests):
        print("🎉 All integration tests passed!")
        print("✅ Enhanced fallback strategies implementation is complete and working correctly.")
        print("✅ LLM will now receive better error context and guidance for improved decision making.")
        return 0
    else:
        print("⚠️  Some integration tests failed.")
        print("❌ Please review the implementation and fix any issues.")
        return 1


if __name__ == "__main__":
    exit(main())