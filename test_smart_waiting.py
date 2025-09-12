#!/usr/bin/env python3
"""
Test to validate that smart waiting mechanisms are working correctly.
This test checks that fixed delays have been eliminated and replaced with Playwright's auto-waiting.
"""
import sys
import os
import re

# Add the project root to Python path
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)

def test_no_fixed_delays_in_automation_server():
    """Test that fixed delays have been eliminated from automation_server.py"""
    print("Testing elimination of fixed delays in automation_server.py...")
    
    with open("vnc/automation_server.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # Check for problematic patterns
    problematic_patterns = [
        r'await asyncio\.sleep\([0-9]+\.?[0-9]*\)',  # Fixed asyncio.sleep with hardcoded values
        r'time\.sleep\([0-9]+\.?[0-9]*\)',           # Fixed time.sleep with hardcoded values
        r'await.*wait_for_timeout\([0-9]+\)',       # Fixed wait_for_timeout calls (some are acceptable in fallbacks)
    ]
    
    issues_found = []
    
    for pattern in problematic_patterns:
        matches = re.finditer(pattern, content)
        for match in matches:
            line_number = content[:match.start()].count('\n') + 1
            matched_text = match.group(0)
            
            # Check if this is in a fallback or error handling context (acceptable)
            context_start = max(0, match.start() - 200)
            context_end = min(len(content), match.end() + 200)
            context = content[context_start:context_end].lower()
            
            # These patterns indicate acceptable use in fallbacks
            acceptable_contexts = [
                'fallback',
                'except',
                'last resort',
                'minimal',
                'emergency',
                'final attempt',
                'error',
                'catch'
            ]
            
            is_acceptable = any(ctx in context for ctx in acceptable_contexts)
            
            if not is_acceptable:
                issues_found.append(f"Line {line_number}: {matched_text}")
    
    if issues_found:
        print("❌ Found fixed delays that should be replaced:")
        for issue in issues_found:
            print(f"  - {issue}")
        return False
    else:
        print("✅ No problematic fixed delays found in automation_server.py")
        return True


def test_smart_waiting_implementations():
    """Test that smart waiting mechanisms are properly implemented"""
    print("\nTesting smart waiting implementations...")
    
    with open("vnc/automation_server.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # Check for smart waiting patterns
    smart_patterns = [
        r'wait_for_load_state\("networkidle"',
        r'wait_for_load_state\("domcontentloaded"',
        r'wait_for\(state="visible"',
        r'wait_for\(state="attached"',
        r'wait_for_selector.*state=',
    ]
    
    smart_waiting_found = []
    
    for pattern in smart_patterns:
        matches = list(re.finditer(pattern, content))
        if matches:
            smart_waiting_found.append(f"{pattern}: {len(matches)} occurrences")
    
    if smart_waiting_found:
        print("✅ Smart waiting patterns found:")
        for pattern in smart_waiting_found:
            print(f"  - {pattern}")
        return True
    else:
        print("❌ No smart waiting patterns found")
        return False


def test_vnc_client_adaptive_waiting():
    """Test that VNC client has adaptive waiting instead of fixed delays"""
    print("\nTesting VNC client adaptive waiting...")
    
    with open("agent/browser/vnc.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # Check for adaptive waiting patterns
    adaptive_patterns = [
        r'adaptive_wait',
        r'_check_health\(\)',
        r'base_wait.*\*.*0\.[0-9]',  # Patterns like base_wait * 0.5
        r'wait_time = base_wait',     # Assignment from base_wait
        r'wait_time.*\*.*0\.[0-9]',  # Patterns like wait_time calculation
    ]
    
    adaptive_found = []
    
    for pattern in adaptive_patterns:
        matches = list(re.finditer(pattern, content))
        if matches:
            adaptive_found.append(f"{pattern}: {len(matches)} occurrences")
    
    # Check that fixed sleeps are only used with adaptive calculations
    fixed_sleep_pattern = r'time\.sleep\([^)]+\)'
    fixed_sleeps = list(re.finditer(fixed_sleep_pattern, content))
    
    # Count how many are using adaptive waits (more comprehensive check)
    adaptive_sleep_count = 0
    for match in fixed_sleeps:
        context_start = max(0, match.start() - 200)
        context_end = min(len(content), match.end() + 100)
        context = content[context_start:context_end]
        
        # More comprehensive adaptive patterns
        adaptive_indicators = [
            'adaptive', 'base_wait', '_check_health()', 
            'wait_time =', 'base_wait *', 'attempt *',
            'min(', 'max('  # Mathematical calculations
        ]
        
        if any(indicator in context for indicator in adaptive_indicators):
            adaptive_sleep_count += 1
    
    print(f"Found {len(fixed_sleeps)} sleep calls, {adaptive_sleep_count} are adaptive")
    
    if adaptive_found and adaptive_sleep_count == len(fixed_sleeps):
        print("✅ VNC client uses adaptive waiting")
        for pattern in adaptive_found:
            print(f"  - {pattern}")
        return True
    else:
        print("❌ VNC client still has non-adaptive waiting")
        return False


def test_locator_utils_smart_waiting():
    """Test that locator_utils.py uses smart waiting"""
    print("\nTesting locator utils smart waiting...")
    
    with open("vnc/locator_utils.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # Check for Playwright state waiting
    playwright_patterns = [
        r'wait_for\(state="visible"',
        r'wait_for\(state="attached"',
    ]
    
    playwright_found = []
    
    for pattern in playwright_patterns:
        matches = list(re.finditer(pattern, content))
        if matches:
            playwright_found.append(f"{pattern}: {len(matches)} occurrences")
    
    # Check that custom JavaScript polling has been replaced
    polling_pattern = r'setTimeout\(check.*[0-9]+'
    polling_matches = list(re.finditer(polling_pattern, content))
    
    if playwright_found and len(polling_matches) == 0:
        print("✅ Locator utils uses Playwright smart waiting")
        for pattern in playwright_found:
            print(f"  - {pattern}")
        return True
    else:
        print("❌ Locator utils still uses polling or lacks smart waiting")
        if polling_matches:
            print(f"  Found {len(polling_matches)} polling patterns")
        return False


def main():
    """Run all smart waiting tests"""
    print("🔍 Testing Smart Waiting Implementation")
    print("=" * 50)
    
    tests = [
        test_no_fixed_delays_in_automation_server,
        test_smart_waiting_implementations,
        test_vnc_client_adaptive_waiting,
        test_locator_utils_smart_waiting,
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test {test.__name__} failed with error: {e}")
            results.append(False)
    
    print("\n" + "=" * 50)
    print("📊 Test Results Summary:")
    passed = sum(results)
    total = len(results)
    
    if passed == total:
        print(f"✅ All {total} tests passed! Smart waiting implementation is successful.")
        return True
    else:
        print(f"❌ {passed}/{total} tests passed. Some issues need to be addressed.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)