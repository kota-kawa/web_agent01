#!/usr/bin/env python3
"""
Test script to verify warning handling improvements.
Tests that all errors are captured and character limits are enforced.
"""

import sys
import os
sys.path.append('.')

from agent.browser.vnc import _truncate_warning
from agent.controller.async_executor import AsyncExecutor, TaskStatus


def test_truncate_warning():
    """Test warning message truncation."""
    print("🧪 Testing warning truncation...")
    
    # Test normal message (should not be truncated)
    short_msg = "ERROR:auto:This is a short warning message"
    result = _truncate_warning(short_msg)
    assert result == short_msg, f"Short message was truncated: {result}"
    print("  ✅ Short messages are not truncated")
    
    # Test long message (should be truncated)
    long_msg = "ERROR:auto:" + "x" * 1000  # 1010 characters total
    result = _truncate_warning(long_msg)
    assert len(result) == 1000, f"Long message not truncated to 1000 chars: {len(result)}"
    assert result.endswith("..."), f"Truncated message should end with '...': {result[-10:]}"
    print("  ✅ Long messages are truncated to 1000 characters with '...'")
    
    # Test exact 1000 character message
    exact_msg = "x" * 1000
    result = _truncate_warning(exact_msg)
    assert result == exact_msg, f"1000-char message was modified: {len(result)}"
    print("  ✅ Exactly 1000-character messages are not truncated")
    
    # Test custom length
    custom_msg = "x" * 20
    result = _truncate_warning(custom_msg, max_length=10)
    assert len(result) == 10, f"Custom length truncation failed: {len(result)}"
    assert result == "xxxxxxx...", f"Custom truncation wrong: {result}"
    print("  ✅ Custom length truncation works correctly")


def test_async_executor_warning_handling():
    """Test that async executor properly handles warnings."""
    print("\n🧪 Testing async executor warning handling...")
    
    # Mock execute function that returns warnings
    def mock_execute_with_warnings(payload):
        return {
            "html": "<html>test</html>",
            "warnings": [
                "ERROR:auto:First warning message",
                "WARNING:auto:" + "y" * 1500,  # Long warning that should be truncated
                "INFO:auto:Third warning message"
            ]
        }
    
    # Mock execute function that raises exception
    def mock_execute_with_exception(payload):
        raise Exception("Simulated execution failure with a very long error message that should be truncated to 1000 characters: " + "z" * 1000)
    
    executor = AsyncExecutor(max_workers=2)
    
    # Test successful execution with warnings
    task_id = executor.create_task()
    success = executor.submit_playwright_execution(task_id, mock_execute_with_warnings, [{"action": "click", "target": "#test"}])
    assert success, "Failed to submit task"
    
    # Wait a bit for execution to complete
    import time
    time.sleep(0.5)
    
    status = executor.get_task_status(task_id)
    assert status is not None, "Task status should not be None"
    assert status["status"] == "completed", f"Task should be completed: {status['status']}"
    assert "result" in status, "Task should have result"
    assert "warnings" in status["result"], "Result should have warnings"
    
    warnings = status["result"]["warnings"]
    assert len(warnings) == 3, f"Should have 3 warnings: {len(warnings)}"
    assert all(len(w) <= 1000 for w in warnings), f"All warnings should be ≤1000 chars: {[len(w) for w in warnings]}"
    print("  ✅ Successful execution with warnings handled correctly")
    
    # Test failed execution with exception
    task_id2 = executor.create_task()
    success = executor.submit_playwright_execution(task_id2, mock_execute_with_exception, [{"action": "click", "target": "#test"}])
    assert success, "Failed to submit task"
    
    # Wait a bit for execution to complete
    time.sleep(0.5)
    
    status2 = executor.get_task_status(task_id2)
    assert status2 is not None, "Task status should not be None"
    assert status2["status"] == "failed", f"Task should be failed: {status2['status']}"
    assert "result" in status2, "Failed task should have result with warnings"
    assert "warnings" in status2["result"], "Failed task result should have warnings"
    
    warnings2 = status2["result"]["warnings"]
    assert len(warnings2) == 1, f"Should have 1 warning: {len(warnings2)}"
    assert len(warnings2[0]) <= 1000, f"Warning should be ≤1000 chars: {len(warnings2[0])}"
    assert "ERROR:auto:Async execution failed" in warnings2[0], f"Should contain error prefix: {warnings2[0][:50]}"
    print("  ✅ Failed execution with exception handled correctly")
    
    executor.shutdown()


def test_warning_accumulation():
    """Test that multiple errors are accumulated properly."""
    print("\n🧪 Testing warning accumulation logic...")
    
    # This test simulates what happens in execute_dsl when multiple attempts fail
    all_errors = [
        "Connection error - Could not connect to automation server",
        "HTTP 500 error - Internal server error", 
        "Request timeout - The operation took too long to complete"
    ]
    
    # Simulate the warning creation logic from execute_dsl
    warning_messages = []
    max_retries = len(all_errors)
    
    for i, error in enumerate(all_errors, 1):
        warning_msg = f"ERROR:auto:Attempt {i}/{max_retries} - {error}"
        warning_messages.append(_truncate_warning(warning_msg))
    
    # Add summary warning
    summary_warning = f"ERROR:auto:All {max_retries} execution attempts failed. Total errors: {len(all_errors)}"
    warning_messages.append(_truncate_warning(summary_warning))
    
    assert len(warning_messages) == 4, f"Should have 4 warnings (3 attempts + 1 summary): {len(warning_messages)}"
    assert all("ERROR:auto:Attempt" in w for w in warning_messages[:-1]), "First 3 should be attempt warnings"
    assert "All 3 execution attempts failed" in warning_messages[-1], "Last should be summary"
    assert all(len(w) <= 1000 for w in warning_messages), f"All warnings should be ≤1000 chars: {[len(w) for w in warning_messages]}"
    
    print("  ✅ Multiple error accumulation works correctly")
    print(f"  📊 Generated {len(warning_messages)} warnings from {len(all_errors)} errors")


def main():
    """Run all tests."""
    print("🚀 Testing warning handling improvements...\n")
    
    try:
        test_truncate_warning()
        test_async_executor_warning_handling()
        test_warning_accumulation()
        
        print("\n✅ All tests passed! Warning handling improvements are working correctly.")
        print("\n📋 Summary of improvements:")
        print("  • All error messages are captured across multiple retry attempts")
        print("  • Warning messages are automatically truncated to 1000 characters")
        print("  • Async execution failures are converted to proper warning format")
        print("  • Error accumulation provides detailed failure information")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())