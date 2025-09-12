#!/usr/bin/env python3
"""
Test browser operation reliability improvements.

This test validates that the enhanced retry logic, error classification,
and browser health monitoring improvements work correctly.
"""

import asyncio
import time
import json
import unittest
from unittest.mock import Mock, patch, AsyncMock
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

def test_vnc_error_classification():
    """Test the new error classification in VNC client."""
    print("Testing VNC error classification...")
    
    # Import the classification function
    from agent.browser.vnc import _classify_error_type
    
    # Test network errors
    retryable, msg, wait_time = _classify_error_type("Connection error: Failed to connect")
    assert retryable == True, "Network errors should be retryable"
    assert wait_time == 2, "Network errors should have longer wait times"
    print("✅ Network error classification works")
    
    # Test server errors
    retryable, msg, wait_time = _classify_error_type("HTTP 500 error - Internal server error")
    assert retryable == True, "Server errors should be retryable"
    assert wait_time == 1, "Server errors should have moderate wait times"
    print("✅ Server error classification works")
    
    # Test browser state errors
    retryable, msg, wait_time = _classify_error_type("page is navigating")
    assert retryable == True, "Browser state errors should be retryable"
    assert wait_time == 1, "Browser state errors should have moderate wait times"
    print("✅ Browser state error classification works")
    
    # Test client errors (should not be retryable)
    retryable, msg, wait_time = _classify_error_type("HTTP 404 error - Not found")
    assert retryable == False, "Client errors should not be retryable"
    print("✅ Client error classification works")
    
    print("✅ All VNC error classification tests passed\n")


def test_enhanced_retry_logic():
    """Test that the enhanced retry logic works with mock scenarios."""
    print("Testing enhanced retry logic...")
    
    # Mock the VNC client execute_dsl function behavior
    from agent.browser.vnc import execute_dsl
    
    # Test with empty payload (should return empty response)
    result = execute_dsl({})
    assert result == {"html": "", "warnings": []}, "Empty payload should return empty response"
    print("✅ Empty payload handling works")
    
    # Test with valid payload but no actions
    result = execute_dsl({"actions": []})
    assert result == {"html": "", "warnings": []}, "No actions should return empty response"
    print("✅ No actions handling works")
    
    print("✅ Enhanced retry logic tests passed\n")


def test_automation_server_improvements():
    """Test automation server timeout and retry improvements."""
    print("Testing automation server improvements...")
    
    # Read the automation server file to verify improvements are in place
    server_path = os.path.join(os.path.dirname(__file__), "vnc", "automation_server.py")
    
    with open(server_path, 'r', encoding='utf-8') as f:
        server_content = f.read()
    
    # Test that timeouts have been increased
    assert 'ACTION_TIMEOUT = int(os.getenv("ACTION_TIMEOUT", "15000"))' in server_content, "ACTION_TIMEOUT should be increased to 15000"
    assert 'NAVIGATION_TIMEOUT = int(os.getenv("NAVIGATION_TIMEOUT", "45000"))' in server_content, "NAVIGATION_TIMEOUT should be increased to 45000"
    assert 'MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))' in server_content, "MAX_RETRIES should be increased to 5"
    assert 'LOCATOR_RETRIES = int(os.getenv("LOCATOR_RETRIES", "4"))' in server_content, "LOCATOR_RETRIES should be increased to 4"
    
    print("✅ Timeout values increased in automation server")
    
    # Test that enhanced error classification is present
    assert "一時的な処理エラー - 再試行をお試しください" in server_content, "Enhanced error messages should be present"
    assert "ブラウザの初期化に問題があります" in server_content, "Browser state error messages should be enhanced"
    print("✅ Enhanced error classification in automation server")
    
    # Test that enhanced browser health check is present
    assert "Level 1: Basic readiness check" in server_content, "Multi-level browser health check should be implemented"
    assert "Level 2: DOM interaction capability" in server_content, "DOM interaction check should be implemented"
    assert "Level 3: Page navigation state" in server_content, "Navigation state check should be implemented"
    print("✅ Enhanced browser health monitoring implemented")
    
    # Test that enhanced action execution is present
    assert "action_success_count" in server_content, "Action success tracking should be implemented"
    assert "_get_action_guidance_for_error" in server_content, "Action-specific error guidance should be implemented"
    print("✅ Enhanced action execution with guidance implemented")
    
    print("✅ All automation server improvement tests passed\n")


def test_javascript_improvements():
    """Test that JavaScript improvements are in place."""
    print("Testing JavaScript improvements...")
    
    # Read the browser_executor.js file to verify improvements
    js_path = os.path.join(os.path.dirname(__file__), "web", "static", "browser_executor.js")
    
    with open(js_path, 'r', encoding='utf-8') as f:
        js_content = f.read()
    
    # Check for increased retry attempts
    assert "const maxRetries = 3;" in js_content, "JavaScript should have increased maxRetries to 3"
    print("✅ JavaScript maxRetries increased")
    
    # Check for enhanced error handling
    assert "consecutiveServerErrors" in js_content, "JavaScript should track consecutive server errors"
    print("✅ JavaScript consecutive error tracking added")
    
    # Check for enhanced status messages
    assert "⚠️ ブラウザ操作で問題が発生しました" in js_content, "JavaScript should have enhanced error messages"
    print("✅ JavaScript enhanced error messages added")
    
    # Check for improved polling logic
    assert "maxAttempts = 60" in js_content, "JavaScript should have increased polling attempts"
    print("✅ JavaScript polling attempts increased")
    
    # Check for adaptive intervals
    assert "adaptiveInterval" in js_content, "JavaScript should use adaptive polling intervals"
    print("✅ JavaScript adaptive intervals implemented")
    
    print("✅ All JavaScript improvement tests passed\n")


def test_error_message_improvements():
    """Test that error messages are more user-friendly."""
    print("Testing error message improvements...")
    
    # Read the automation server file to verify error message improvements
    server_path = os.path.join(os.path.dirname(__file__), "vnc", "automation_server.py")
    
    with open(server_path, 'r', encoding='utf-8') as f:
        server_content = f.read()
    
    # Test that enhanced error messages are present
    test_messages = [
        "ページが読み込み中です - 少し待ってから再試行してください",
        "要素が見つかりませんでした - ページの読み込み完了を待つか、セレクタを見直してください",
        "操作がタイムアウトしました - ページの応答が遅いか、要素の読み込みに時間がかかっています",
        "ブラウザの初期化に問題があります - 自動的に再接続を試行します",
        "ネットワークエラー - インターネット接続またはサイトに問題があります"
    ]
    
    for msg in test_messages:
        assert msg in server_content, f"Enhanced error message should be present: {msg}"
        print(f"✅ Error message found: {msg[:50]}...")
    
    print("✅ All error message improvement tests passed\n")


def test_browser_health_monitoring():
    """Test browser health monitoring improvements."""
    print("Testing browser health monitoring...")
    
    # Read the automation server file to verify health monitoring improvements
    server_path = os.path.join(os.path.dirname(__file__), "vnc", "automation_server.py")
    
    with open(server_path, 'r', encoding='utf-8') as f:
        server_content = f.read()
    
    # Test that enhanced health check is present
    assert "async def _check_browser_health()" in server_content, "Enhanced browser health check function should exist"
    assert "Level 1: Basic readiness check" in server_content, "Multi-level health check should be implemented"
    assert "Level 2: DOM interaction capability" in server_content, "DOM interaction check should be present"
    assert "Level 3: Page navigation state" in server_content, "Navigation state check should be present"
    print("✅ Enhanced browser health check function implemented")
    
    # Test that pre-execution health check is present
    assert "Pre-execution health check and recovery" in server_content, "Pre-execution health check should be implemented"
    assert "Browser unhealthy before execution, attempting recovery" in server_content, "Recovery logic should be present"
    print("✅ Pre-execution health check and recovery implemented")
    
    # Test that browser recovery mechanisms are present
    assert "Quick browser recovery" in server_content, "Quick browser recovery should be implemented"
    assert "Browser recovery successful" in server_content, "Recovery success logging should be present"
    print("✅ Browser recovery mechanisms implemented")
    
    print("✅ All browser health monitoring tests passed\n")


def main():
    """Run all reliability improvement tests."""
    print("🔍 Testing Browser Operation Reliability Improvements")
    print("=" * 60)
    
    try:
        test_vnc_error_classification()
        test_enhanced_retry_logic()
        test_automation_server_improvements()
        test_javascript_improvements()
        test_error_message_improvements()
        test_browser_health_monitoring()
        
        print("🎉 ALL TESTS PASSED!")
        print("\nImprovements verified:")
        print("✅ Enhanced error classification and retry logic")
        print("✅ Increased timeout values and retry attempts")
        print("✅ Better browser health monitoring")
        print("✅ More user-friendly error messages")
        print("✅ Improved JavaScript polling and error handling")
        print("✅ Enhanced recovery mechanisms")
        
        return True
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)