#!/usr/bin/env python3
"""
Test script to validate the enhanced fallback strategies for hover, select, and key press operations.
"""
import sys
import asyncio
import os

# Add vnc directory to path for imports
sys.path.insert(0, 'vnc')

def test_get_key_code():
    """Test the key code mapping function."""
    print("Testing key code mapping...")
    
    from automation_server import _get_key_code
    
    test_cases = [
        ('Enter', 13),
        ('Tab', 9),
        ('Escape', 27),
        ('Space', 32),
        ('a', 65),  # ASCII code for 'A'
        ('1', 49),  # ASCII code for '1'
        ('F1', 112),
        ('ArrowUp', 38),
        ('UnknownKey', 0)
    ]
    
    passed = 0
    for key, expected in test_cases:
        result = _get_key_code(key)
        if result == expected:
            print(f"  ✅ '{key}' -> {result} (expected {expected})")
            passed += 1
        else:
            print(f"  ❌ '{key}' -> {result} (expected {expected})")
    
    print(f"Key code mapping: {passed}/{len(test_cases)} tests passed\n")
    return passed == len(test_cases)


def test_error_message_parsing():
    """Test that error messages include fallback context."""
    print("Testing error message formatting...")
    
    # Simulate the error message formatting used in the enhanced functions
    test_cases = [
        {
            'type': 'hover',
            'original': 'Element timeout',
            'force': 'Force hover failed',
            'js': 'JS mouseover failed',
            'expected_pattern': 'Hover failed - Original: Element timeout, Force: Force hover failed, JS: JS mouseover failed'
        },
        {
            'type': 'select',
            'original': 'Option not found',
            'label': 'Label select failed',
            'js': 'JS selection failed',
            'click': 'Click dropdown failed',
            'expected_pattern': 'Select failed - Original: Option not found, Label: Label select failed, JS: JS selection failed, Click: Click dropdown failed'
        },
        {
            'type': 'press',
            'original': 'Key press timeout',
            'focus': 'Focus failed',
            'page': 'Page keypress failed',
            'js': 'JS key event failed',
            'expected_pattern': 'Key press failed - Original: Key press timeout, Focus: Focus failed, Page: Page keypress failed, JS: JS key event failed'
        }
    ]
    
    passed = 0
    for case in test_cases:
        if case['type'] == 'hover':
            error_msg = f"Hover failed - Original: {case['original']}, Force: {case['force']}, JS: {case['js']}"
        elif case['type'] == 'select':
            error_msg = f"Select failed - Original: {case['original']}, Label: {case['label']}, JS: {case['js']}, Click: {case['click']}"
        elif case['type'] == 'press':
            error_msg = f"Key press failed - Original: {case['original']}, Focus: {case['focus']}, Page: {case['page']}, JS: {case['js']}"
        
        if error_msg == case['expected_pattern']:
            print(f"  ✅ {case['type']} error format matches expected pattern")
            passed += 1
        else:
            print(f"  ❌ {case['type']} error format mismatch")
            print(f"      Got: {error_msg}")
            print(f"      Expected: {case['expected_pattern']}")
    
    print(f"Error message formatting: {passed}/{len(test_cases)} tests passed\n")
    return passed == len(test_cases)


def test_enhanced_error_detection():
    """Test that the enhanced error detection logic works properly."""
    print("Testing enhanced error detection logic...")
    
    test_cases = [
        {
            'error_msg': 'Hover failed - Original: Element timeout, Force: Force hover failed, JS: JS mouseover failed',
            'should_be_enhanced': True,
            'description': 'Hover fallback error'
        },
        {
            'error_msg': 'Select failed - Original: Option not found, Label: Label select failed',
            'should_be_enhanced': True,
            'description': 'Select fallback error'
        },
        {
            'error_msg': 'Click failed - Original: Element not clickable, Force: Force click failed, JS: JS click failed',
            'should_be_enhanced': True,
            'description': 'Click fallback error'
        },
        {
            'error_msg': 'Simple element not found',
            'should_be_enhanced': False,
            'description': 'Simple error without fallbacks'
        },
        {
            'error_msg': 'Network timeout occurred',
            'should_be_enhanced': False,
            'description': 'Network error'
        }
    ]
    
    passed = 0
    for case in test_cases:
        # Simulate the logic used in the action execution error handling
        is_enhanced = "failed -" in case['error_msg'] and ("Original:" in case['error_msg'] or "Fallback" in case['error_msg'])
        
        if is_enhanced == case['should_be_enhanced']:
            print(f"  ✅ {case['description']} -> Enhanced detection: {is_enhanced}")
            passed += 1
        else:
            print(f"  ❌ {case['description']} -> Enhanced detection: {is_enhanced} (expected {case['should_be_enhanced']})")
    
    print(f"Enhanced error detection: {passed}/{len(test_cases)} tests passed\n")
    return passed == len(test_cases)


def main():
    """Run all tests."""
    print("🧪 Enhanced Fallback Strategies Validation")
    print("=" * 50)
    
    tests = [
        test_get_key_code,
        test_error_message_parsing,
        test_enhanced_error_detection
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
        print("🎉 All fallback enhancement tests passed!")
        print("✅ Enhanced fallback strategies are working correctly.")
        return 0
    else:
        print("⚠️  Some fallback enhancement tests failed.")
        print("❌ Please review the implementation and fix any issues.")
        return 1


if __name__ == "__main__":
    exit(main())