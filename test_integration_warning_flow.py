#!/usr/bin/env python3
"""
Integration test for warning handling across the complete execution flow.
This test simulates actual execution scenarios that would generate warnings.
"""

import sys
import json
import unittest.mock as mock
sys.path.append('.')

from agent.browser.vnc import execute_dsl, _truncate_warning
from agent.controller.async_executor import get_async_executor


def test_execute_dsl_multiple_failures():
    """Test execute_dsl with multiple failure scenarios."""
    print("🧪 Testing execute_dsl with simulated multiple failures...")
    
    # Mock the VNC API to simulate different failure scenarios
    import requests
    
    with mock.patch('agent.browser.vnc.requests.post') as mock_post, \
         mock.patch('agent.browser.vnc._check_health') as mock_health:
        
        # Configure health check to fail initially then pass
        mock_health.side_effect = [False, True]  # Fail first, pass second
        
        # Configure POST to fail multiple times with different errors
        # First attempt: Connection error
        mock_post.side_effect = [
            requests.ConnectionError("Connection refused"),
            requests.HTTPError("HTTP 500 Internal Server Error")
        ]
        
        # Execute DSL with actions
        payload = {
            "actions": [
                {"action": "click", "target": "#submit"},
                {"action": "type", "target": "#input", "value": "test"}
            ]
        }
        
        result = execute_dsl(payload, timeout=30)
        
        # Check the result
        assert "warnings" in result, f"Result should have warnings: {result}"
        assert len(result["warnings"]) >= 2, f"Should have multiple warnings: {result['warnings']}"
        
        warnings = result["warnings"]
        
        # Should have attempt-specific warnings
        attempt_warnings = [w for w in warnings if "Attempt" in w]
        assert len(attempt_warnings) >= 2, f"Should have at least 2 attempt warnings: {attempt_warnings}"
        
        # Should have summary warning
        summary_warnings = [w for w in warnings if "All" in w and "attempts failed" in w]
        assert len(summary_warnings) == 1, f"Should have exactly 1 summary warning: {summary_warnings}"
        
        # All warnings should be within character limit
        assert all(len(w) <= 1000 for w in warnings), f"All warnings should be ≤1000 chars: {[len(w) for w in warnings]}"
        
        print(f"  ✅ Generated {len(warnings)} warnings from multiple failure attempts")
        print(f"  📝 Warnings: {json.dumps(warnings, indent=2)}")


def test_execute_dsl_with_retry_success():
    """Test execute_dsl that fails first but succeeds on retry."""
    print("\n🧪 Testing execute_dsl with retry success...")
    
    import requests
    
    with mock.patch('agent.browser.vnc.requests.post') as mock_post, \
         mock.patch('agent.browser.vnc._check_health') as mock_health:
        
        # Health check passes on retry
        mock_health.return_value = True
        
        # First attempt fails, second succeeds
        # First attempt: Timeout
        timeout_error = requests.Timeout("Request timeout")
        
        # Second attempt: Success with some warnings from server
        success_response = mock.Mock()
        success_response.raise_for_status.return_value = None
        success_response.json.return_value = {
            "html": "<html><body>Success page</body></html>",
            "warnings": [
                "WARNING:auto:Element not immediately visible, waited 2s",
                "INFO:auto:Successfully completed action"
            ]
        }
        
        mock_post.side_effect = [timeout_error, success_response]
        
        # Execute DSL
        payload = {"actions": [{"action": "click", "target": "#button"}]}
        result = execute_dsl(payload, timeout=30)
        
        # Check the result
        assert "html" in result, f"Result should have html: {result}"
        assert "warnings" in result, f"Result should have warnings: {result}"
        
        warnings = result["warnings"]
        
        # Should have original server warnings plus retry information
        assert len(warnings) >= 2, f"Should have multiple warnings: {warnings}"
        
        # Should have retry attempt warning
        retry_warnings = [w for w in warnings if "Retry attempt" in w]
        assert len(retry_warnings) >= 1, f"Should have retry warnings: {retry_warnings}"
        
        # Should have success message
        success_messages = [w for w in warnings if "succeeded on retry" in w]
        assert len(success_messages) == 1, f"Should have success message: {success_messages}"
        
        # All warnings should be within character limit
        assert all(len(w) <= 1000 for w in warnings), f"All warnings should be ≤1000 chars: {[len(w) for w in warnings]}"
        
        print(f"  ✅ Retry success scenario handled correctly with {len(warnings)} warnings")
        print(f"  📝 Warnings: {json.dumps(warnings, indent=2)}")


def test_character_limit_enforcement():
    """Test that extremely long error messages are properly truncated."""
    print("\n🧪 Testing character limit enforcement with very long messages...")
    
    import requests
    
    with mock.patch('agent.browser.vnc.requests.post') as mock_post:
        
        # Create a very long error message (over 2000 characters)
        long_error_msg = "Connection failed: " + "x" * 2000 + " - end of error"
        
        # Mock connection error with very long message
        conn_error = requests.ConnectionError(long_error_msg)
        mock_post.side_effect = [conn_error, conn_error]  # Fail both attempts
        
        # Execute DSL
        payload = {"actions": [{"action": "navigate", "target": "https://example.com"}]}
        result = execute_dsl(payload, timeout=30)
        
        # Check the result
        assert "warnings" in result, f"Result should have warnings: {result}"
        warnings = result["warnings"]
        
        # All warnings should be within character limit
        for warning in warnings:
            assert len(warning) <= 1000, f"Warning exceeds 1000 chars ({len(warning)}): {warning[:100]}..."
            
        # Check that truncated warnings end with "..."
        long_warnings = [w for w in warnings if len(w) == 1000]
        for warning in long_warnings:
            assert warning.endswith("..."), f"Long warning should end with '...': {warning[-10:]}"
        
        print(f"  ✅ Character limit enforced correctly on {len(warnings)} warnings")
        print(f"  📏 Warning lengths: {[len(w) for w in warnings]}")


def test_empty_and_edge_cases():
    """Test edge cases for warning handling."""
    print("\n🧪 Testing edge cases...")
    
    # Test empty payload
    result = execute_dsl({}, timeout=30)
    assert result == {"html": "", "warnings": []}, f"Empty payload should return empty result: {result}"
    print("  ✅ Empty payload handled correctly")
    
    # Test payload with no actions
    result = execute_dsl({"actions": []}, timeout=30)
    assert result == {"html": "", "warnings": []}, f"No actions should return empty result: {result}"
    print("  ✅ No actions handled correctly")
    
    # Test truncation edge cases
    exactly_1000 = "x" * 1000
    result = _truncate_warning(exactly_1000)
    assert result == exactly_1000, "Exactly 1000 chars should not be truncated"
    print("  ✅ Exactly 1000 character messages not truncated")
    
    just_over_1000 = "x" * 1001
    result = _truncate_warning(just_over_1000)
    assert len(result) == 1000, f"1001 chars should be truncated to 1000: {len(result)}"
    assert result.endswith("..."), "Truncated message should end with '...'"
    print("  ✅ 1001+ character messages properly truncated")


def main():
    """Run all integration tests."""
    print("🚀 Running integration tests for warning handling improvements...\n")
    
    try:
        test_execute_dsl_multiple_failures()
        test_execute_dsl_with_retry_success()
        test_character_limit_enforcement()
        test_empty_and_edge_cases()
        
        print("\n✅ All integration tests passed!")
        print("\n📋 Verified improvements:")
        print("  • Multiple execution failures generate individual attempt warnings")
        print("  • Retry scenarios include both failure and success information")
        print("  • Character limit (1000 chars) is enforced on all warning messages")
        print("  • Edge cases (empty payloads, exact limits) are handled correctly")
        print("  • Warning accumulation provides comprehensive failure information")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Integration test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())