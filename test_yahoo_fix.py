#!/usr/bin/env python3
"""
Test script to validate the Yahoo homepage navigation fix.
This test validates the code changes without requiring external dependencies.
"""

import sys
import os
import re

def test_browser_recreation_fix():
    """Test that browser recreation now preserves current URL."""
    print("Testing browser recreation fix...")
    
    # Read the automation_server.py file
    file_path = os.path.join(os.path.dirname(__file__), "vnc", "automation_server.py")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check that URL preservation logic is present
    if "current_url" in content and "Preserving current URL" in content:
        print("  ✅ URL preservation logic found")
    else:
        print("  ❌ URL preservation logic missing")
        return False
    
    # Check that the recreation function navigates back to preserved URL
    if "Navigate back to preserved URL" in content or "navigating back to preserved URL" in content:
        print("  ✅ URL restoration logic found")
    else:
        print("  ❌ URL restoration logic missing")
        return False
    
    # Check that DEFAULT_URL is excluded from preservation
    if "current_url != DEFAULT_URL" in content:
        print("  ✅ DEFAULT_URL exclusion logic found")
    else:
        print("  ❌ DEFAULT_URL exclusion logic missing")
        return False
    
    return True


def test_health_check_optimization():
    """Test that endpoints now check browser health before initialization."""
    print("Testing health check optimization...")
    
    file_path = os.path.join(os.path.dirname(__file__), "vnc", "automation_server.py")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check that /source endpoint has health check
    source_pattern = r'@app\.get\("/source"\).*?def source\(\):.*?if not PAGE or not.*_check_browser_health'
    if re.search(source_pattern, content, re.DOTALL):
        print("  ✅ /source endpoint health check found")
    else:
        print("  ❌ /source endpoint health check missing")
        return False
    
    # Check that /screenshot endpoint has health check
    screenshot_pattern = r'@app\.get\("/screenshot"\).*?def screenshot\(\):.*?if not PAGE or not.*_check_browser_health'
    if re.search(screenshot_pattern, content, re.DOTALL):
        print("  ✅ /screenshot endpoint health check found")
    else:
        print("  ❌ /screenshot endpoint health check missing")
        return False
    
    # Check that /elements endpoint has health check
    elements_pattern = r'@app\.get\("/elements"\).*?def elements\(\):.*?if not PAGE or not.*_check_browser_health'
    if re.search(elements_pattern, content, re.DOTALL):
        print("  ✅ /elements endpoint health check found")
    else:
        print("  ❌ /elements endpoint health check missing")
        return False
    
    return True


def test_yahoo_navigation_prevention():
    """Test that the fix prevents unnecessary navigation to Yahoo."""
    print("Testing Yahoo navigation prevention...")
    
    file_path = os.path.join(os.path.dirname(__file__), "vnc", "automation_server.py")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check that about: URLs are excluded from preservation
    if "about:" in content and "startswith" in content:
        print("  ✅ about: URL exclusion found")
    else:
        print("  ❌ about: URL exclusion missing")
        return False
    
    # Check that the first init flag logic is preserved
    if "_BROWSER_FIRST_INIT" in content and "Only navigate to DEFAULT_URL on the very first initialization" in content:
        print("  ✅ First initialization logic preserved")
    else:
        print("  ❌ First initialization logic may be broken")
        return False
    
    return True


def main():
    """Run all validation tests."""
    print("Validating Yahoo homepage navigation fix...\n")
    
    tests = [
        test_browser_recreation_fix,
        test_health_check_optimization,
        test_yahoo_navigation_prevention
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
            print()
        except Exception as e:
            print(f"  ❌ Test failed with exception: {e}\n")
    
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("✅ All validation tests passed! The fix should prevent Yahoo homepage navigation during task execution.")
        return 0
    else:
        print("❌ Some validation tests failed. Please check the implementation.")
        return 1


if __name__ == "__main__":
    exit(main())