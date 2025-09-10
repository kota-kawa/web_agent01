#!/usr/bin/env python3
"""
Validation script to test the DSL error handling implementation.
Run this script to verify the core functionality works as expected.
"""

import json
import sys
from urllib.parse import urlparse


def test_url_validation():
    """Test URL validation function."""
    print("Testing URL validation...")
    
    # Import the validation function
    sys.path.insert(0, 'vnc')
    from automation_server import _validate_url
    
    # Test cases
    test_cases = [
        ("https://example.com", True),
        ("http://test.org", True),
        ("", False),
        ("   ", False),
        ("not-a-url", False),
        ("javascript:alert(1)", False),
        ("ftp://files.example.com", True),
        ("https://sub.domain.com/path?query=value", True),
    ]
    
    passed = 0
    for url, expected in test_cases:
        result = _validate_url(url)
        if result == expected:
            print(f"  ✅ '{url}' -> {result} (expected {expected})")
            passed += 1
        else:
            print(f"  ❌ '{url}' -> {result} (expected {expected})")
    
    print(f"URL validation: {passed}/{len(test_cases)} tests passed\n")
    return passed == len(test_cases)


def test_selector_validation():
    """Test selector validation function."""
    print("Testing selector validation...")
    
    sys.path.insert(0, 'vnc')
    from automation_server import _validate_selector
    
    test_cases = [
        ("#button", True),
        (".class-name", True),
        ("button[type='submit']", True),
        ("", False),
        ("   ", False),
        ("text=Click me", True),
        ("css=.my-class || text=Fallback", True),
    ]
    
    passed = 0
    for selector, expected in test_cases:
        result = _validate_selector(selector)
        if result == expected:
            print(f"  ✅ '{selector}' -> {result} (expected {expected})")
            passed += 1
        else:
            print(f"  ❌ '{selector}' -> {result} (expected {expected})")
    
    print(f"Selector validation: {passed}/{len(test_cases)} tests passed\n")
    return passed == len(test_cases)


def test_error_classification():
    """Test error classification function."""
    print("Testing error classification...")
    
    sys.path.insert(0, 'vnc')
    from automation_server import _classify_error
    
    test_cases = [
        ("net::ERR_NAME_NOT_RESOLVED", "ネットワークエラー - サイトに接続できません", False),
        ("timeout waiting for element", "操作がタイムアウトしました - ページの応答が遅い可能性があります", True),
        ("element not found", "要素が見つかりませんでした - セレクタを確認するか、ページの読み込みを待ってください", True),
        ("403 Forbidden", "アクセス拒否 - サイトがアクセスを拒否しました", False),
        ("page is navigating and changing content", "ページが読み込み中です - しばらく待ってから再試行してください", True),
        ("Internal error", "内部処理エラー - Internal error", True),
    ]
    
    passed = 0
    for error, expected_msg, expected_internal in test_cases:
        msg, is_internal = _classify_error(error)
        if expected_msg in msg and is_internal == expected_internal:
            print(f"  ✅ '{error}' -> '{msg[:50]}...', internal={is_internal}")
            passed += 1
        else:
            print(f"  ❌ '{error}' -> '{msg[:50]}...', internal={is_internal} (expected internal={expected_internal})")
    
    print(f"Error classification: {passed}/{len(test_cases)} tests passed\n")
    return passed == len(test_cases)


def test_domain_security():
    """Test domain allowlist/blocklist functionality."""
    print("Testing domain security...")
    
    import os
    
    # Backup original values
    original_allowed = os.getenv("ALLOWED_DOMAINS")
    original_blocked = os.getenv("BLOCKED_DOMAINS")
    
    try:
        # Set test configuration
        os.environ["ALLOWED_DOMAINS"] = "example.com,trusted.org"
        os.environ["BLOCKED_DOMAINS"] = "malicious.com,phishing.net"
        
        # Reload the module to pick up new config
        sys.path.insert(0, 'vnc')
        if 'automation_server' in sys.modules:
            del sys.modules['automation_server']
        
        from automation_server import _is_domain_allowed
        
        test_cases = [
            ("https://example.com", True, ""),
            ("https://trusted.org/path", True, ""),
            ("https://sub.example.com", True, ""),  # subdomain should be allowed
            ("https://malicious.com", False, "blocked"),
            ("https://phishing.net", False, "blocked"),
            ("https://other.com", False, "allowlist"),  # not in allowlist
        ]
        
        passed = 0
        for url, should_allow, expected_error in test_cases:
            allowed, msg = _is_domain_allowed(url)
            if allowed == should_allow and (not expected_error or expected_error in msg.lower()):
                print(f"  ✅ '{url}' -> allowed={allowed}")
                passed += 1
            else:
                print(f"  ❌ '{url}' -> allowed={allowed}, msg='{msg}'")
        
        print(f"Domain security: {passed}/{len(test_cases)} tests passed\n")
        return passed == len(test_cases)
        
    finally:
        # Restore original values
        if original_allowed is not None:
            os.environ["ALLOWED_DOMAINS"] = original_allowed
        elif "ALLOWED_DOMAINS" in os.environ:
            del os.environ["ALLOWED_DOMAINS"]
            
        if original_blocked is not None:
            os.environ["BLOCKED_DOMAINS"] = original_blocked
        elif "BLOCKED_DOMAINS" in os.environ:
            del os.environ["BLOCKED_DOMAINS"]


def test_action_validation():
    """Test action parameter validation."""
    print("Testing action parameter validation...")
    
    sys.path.insert(0, 'vnc')
    from automation_server import _validate_action_params
    
    test_cases = [
        ({"action": "navigate", "target": "https://example.com"}, 0),  # Valid
        ({"action": "navigate", "target": ""}, 1),  # Invalid URL
        ({"action": "wait_for_selector", "target": "#button"}, 0),  # Valid
        ({"action": "wait_for_selector", "target": ""}, 1),  # Empty selector
        ({"action": "click", "target": "button", "ms": "invalid"}, 1),  # Invalid timeout
        ({"action": "click", "target": "button", "retry": 0}, 1),  # Invalid retry count
        ({"action": "wait", "ms": 1000}, 0),  # Valid wait
    ]
    
    passed = 0
    for action, expected_warnings in test_cases:
        warnings = _validate_action_params(action)
        if len(warnings) == expected_warnings:
            print(f"  ✅ {action['action']} -> {len(warnings)} warnings (expected {expected_warnings})")
            passed += 1
        else:
            print(f"  ❌ {action['action']} -> {len(warnings)} warnings (expected {expected_warnings})")
            for w in warnings:
                print(f"      {w}")
    
    print(f"Action validation: {passed}/{len(test_cases)} tests passed\n")
    return passed == len(test_cases)


def main():
    """Run all validation tests."""
    print("🧪 DSL Error Handling Implementation Validation\n")
    print("=" * 50)
    
    all_passed = True
    
    # Run individual test suites
    tests = [
        test_url_validation,
        test_selector_validation,
        test_error_classification,
        test_domain_security,
        test_action_validation,
    ]
    
    for test_func in tests:
        try:
            if not test_func():
                all_passed = False
        except Exception as e:
            print(f"❌ {test_func.__name__} failed with exception: {e}\n")
            all_passed = False
    
    print("=" * 50)
    if all_passed:
        print("🎉 All validation tests passed!")
        print("✅ DSL error handling implementation is working correctly.")
        return 0
    else:
        print("⚠️  Some validation tests failed.")
        print("❌ Please review the implementation and fix any issues.")
        return 1


if __name__ == "__main__":
    sys.exit(main())