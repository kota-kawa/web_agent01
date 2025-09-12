#!/usr/bin/env python3
"""
Test script to verify enhanced Playwright error capture improvements.
This tests the enhanced error handling for both JSON warnings and prompt error_line.
"""

import sys
import json
import unittest.mock as mock
sys.path.append('.')

from agent.browser.vnc import execute_dsl, _truncate_warning
from agent.controller.prompt import build_prompt, _extract_recent_warnings


def test_enhanced_playwright_error_capture():
    """Test enhanced error capture with Playwright-specific patterns."""
    print("🧪 Testing enhanced Playwright error capture...")
    
    import requests
    
    with mock.patch('agent.browser.vnc.requests.post') as mock_post:
        # Mock a response that contains Playwright-specific error information
        error_response = mock.Mock()
        error_response.raise_for_status.return_value = None
        error_response.json.return_value = {
            "html": "<html><body>Error page</body></html>",
            "warnings": [
                "WARNING:playwright:Element not visible after 2s wait",
                "INFO:playwright:locator=button#submit"
            ],
            "execution_info": "Element state: detached, selector: button#submit, timeout: 30000ms",
            "console_errors": ["TypeError: Cannot read property 'click' of null"]
        }
        
        mock_post.return_value = error_response
        
        # Execute DSL
        payload = {"actions": [{"action": "click", "target": "#submit"}]}
        result = execute_dsl(payload, timeout=30)
        
        # Check the result has comprehensive warnings
        assert "warnings" in result, f"Result should have warnings: {result}"
        warnings = result["warnings"]
        
        # Should have original warnings plus enhanced Playwright error detection
        assert len(warnings) >= 2, f"Should have multiple warnings: {warnings}"
        
        # Check for enhanced error information
        playwright_warnings = [w for w in warnings if "playwright" in w.lower()]
        assert len(playwright_warnings) >= 1, f"Should have Playwright-specific warnings: {playwright_warnings}"
        
        # Check for execution info capture
        exec_info_warnings = [w for w in warnings if "execution_info" in w]
        assert len(exec_info_warnings) >= 1, f"Should capture execution_info: {exec_info_warnings}"
        
        # Check for console errors capture
        console_warnings = [w for w in warnings if "console_error" in w]
        assert len(console_warnings) >= 1, f"Should capture console errors: {console_warnings}"
        
        print(f"  ✅ Enhanced error capture working with {len(warnings)} warnings")
        print(f"  📝 Warnings: {json.dumps(warnings, indent=2)}")


def test_enhanced_network_error_details():
    """Test enhanced network error capture with detailed information."""
    print("\n🧪 Testing enhanced network error details...")
    
    import requests
    
    with mock.patch('agent.browser.vnc.requests.post') as mock_post:
        # Mock HTTP error with response body
        http_error = requests.HTTPError("500 Server Error")
        error_response = mock.Mock()
        error_response.status_code = 500
        error_response.text = '{"error": "Playwright automation server crashed", "details": "Memory exceeded during action execution"}'
        http_error.response = error_response
        
        mock_post.side_effect = [http_error, http_error]  # Fail both attempts
        
        # Execute DSL
        payload = {"actions": [{"action": "navigate", "target": "https://example.com"}]}
        result = execute_dsl(payload, timeout=30)
        
        # Check the result
        assert "warnings" in result, f"Result should have warnings: {result}"
        warnings = result["warnings"]
        
        # Should have detailed HTTP error information including response body
        http_warnings = [w for w in warnings if "HTTP 500" in w and "Playwright automation server crashed" in w]
        assert len(http_warnings) >= 1, f"Should have detailed HTTP error: {http_warnings}"
        
        print(f"  ✅ Enhanced network error details captured in {len(warnings)} warnings")
        print(f"  📝 Sample warning: {warnings[0][:200]}...")


def test_enhanced_connection_error_classification():
    """Test enhanced connection error classification."""
    print("\n🧪 Testing enhanced connection error classification...")
    
    import requests
    
    test_cases = [
        (requests.ConnectionError("Connection refused"), "Connection refused"),
        (requests.ConnectionError("Failed to resolve 'localhost'"), "DNS resolution failed"),
        (requests.ConnectionError("Network is unreachable"), "Network unreachable"),
        (requests.ConnectionError("Connection timed out"), "Connection timeout"),
    ]
    
    for error, expected_type in test_cases:
        with mock.patch('agent.browser.vnc.requests.post') as mock_post:
            mock_post.side_effect = [error, error]
            
            payload = {"actions": [{"action": "click", "target": "#test"}]}
            result = execute_dsl(payload, timeout=30)
            
            warnings = result["warnings"]
            matching_warnings = [w for w in warnings if expected_type in w]
            assert len(matching_warnings) >= 1, f"Should classify {expected_type}: {warnings}"
    
    print("  ✅ Connection error classification working correctly")


def test_enhanced_error_line_processing():
    """Test enhanced error_line processing in prompt.py."""
    print("\n🧪 Testing enhanced error_line processing...")
    
    # Create test error data with various Playwright error types
    test_errors = [
        "ERROR:auto:Element not visible after timeout",
        "WARNING:playwright:selector resolved to 0 elements", 
        "INFO:context:at playwright.click (automation.js:123)",
        "RECENT:ERROR:auto:Previous execution failed with timeout",
        "playwright: waiting for selector button#submit to be visible",
        "execution context was destroyed",
        "page closed unexpectedly",
        "locator.click: Timeout 30000ms exceeded"
    ]
    
    # Create mock conversation history with warnings
    hist = [
        {
            "user": "Click submit button",
            "bot": {
                "explanation": "Trying to click",
                "actions": [],
                "warnings": [
                    "ERROR:auto:Element not clickable",
                    "INFO:playwright:selector=button#submit"
                ],
                "complete": False
            }
        }
    ]
    
    # Build prompt with errors and history
    prompt = build_prompt(
        cmd="Try again",
        page="<html><body><button id='submit'>Submit</button></body></html>",
        hist=hist,
        screenshot=False,
        elements=None,
        error=test_errors
    )
    
    # Check that error_line contains comprehensive information
    assert "現在のエラー状況" in prompt, "Prompt should have error section"
    
    # Extract error section
    error_section_start = prompt.find("## 現在のエラー状況")
    if error_section_start == -1:
        print(f"ERROR: Could not find error section in prompt")
        print(f"Prompt preview: {prompt[-500:]}")
        assert False, "Error section not found"
    
    error_section = prompt[error_section_start:error_section_start + 2000]
    
    print(f"  📋 Error section content:\n{error_section[:800]}")
    
    # Should contain various error types
    assert "not visible" in error_section, f"Should include visibility errors. Content: {error_section[:400]}"
    assert "playwright" in error_section.lower(), "Should include Playwright errors"
    assert "RECENT:" in error_section, "Should include recent warnings from history"
    assert "timeout" in error_section.lower(), "Should include timeout errors"
    
    print("  ✅ Enhanced error_line processing working correctly")
    print(f"  📝 Error section preview: {error_section[:300]}...")


def test_recent_warnings_extraction():
    """Test extraction of recent warnings from conversation history."""
    print("\n🧪 Testing recent warnings extraction...")
    
    # Create test conversation history
    hist = [
        {
            "user": "Navigate to site",
            "bot": {
                "explanation": "Navigating",
                "actions": [],
                "warnings": ["ERROR:auto:Connection timeout", "INFO:retry:Attempting retry"],
                "complete": False
            }
        },
        {
            "user": "Click button", 
            "bot": {
                "explanation": "Clicking",
                "actions": [],
                "warnings": ["WARNING:playwright:Element not visible", "ERROR:auto:Action failed"],
                "complete": False
            }
        }
    ]
    
    warnings = _extract_recent_warnings(hist, max_warnings=5)
    
    # Should extract warnings with RECENT: prefix
    assert len(warnings) >= 2, f"Should extract multiple warnings: {warnings}"
    assert all(w.startswith("RECENT:") for w in warnings), f"All warnings should have RECENT prefix: {warnings}"
    assert any("Connection timeout" in w for w in warnings), f"Should include connection timeout: {warnings}"
    assert any("Element not visible" in w for w in warnings), f"Should include element visibility: {warnings}"
    
    print(f"  ✅ Recent warnings extraction working with {len(warnings)} warnings")
    print(f"  📝 Warnings: {warnings}")


def main():
    """Run all enhanced error capture tests."""
    print("🚀 Running enhanced error capture tests...\n")
    
    try:
        test_enhanced_playwright_error_capture()
        test_enhanced_network_error_details() 
        test_enhanced_connection_error_classification()
        test_enhanced_error_line_processing()
        test_recent_warnings_extraction()
        
        print("\n✅ All enhanced error capture tests passed!")
        print("\n📋 Verified enhancements:")
        print("  • Comprehensive Playwright error pattern detection")
        print("  • Enhanced network and connection error classification")
        print("  • Detailed HTTP error response capture")
        print("  • Improved error_line processing with more patterns")
        print("  • Recent warnings integration from conversation history")
        print("  • Minor error capture for better LLM understanding")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Enhanced error capture test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())