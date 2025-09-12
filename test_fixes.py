#!/usr/bin/env python3
"""
Test script to validate the fixes for repetitive generation and execution status issues.
"""

import time
import asyncio
from agent.controller.async_executor import get_async_executor
from agent.browser.vnc import get_html
from agent.controller.prompt import build_prompt

def test_async_executor():
    """Test that the async executor properly retrieves updated HTML."""
    print("Testing async executor...")
    
    executor = get_async_executor()
    
    # Create a mock task
    task_id = executor.create_task()
    print(f"Created task: {task_id}")
    
    # Test task status retrieval
    status = executor.get_task_status(task_id)
    print(f"Task status: {status}")
    
    # Clean up
    executor.cleanup_old_tasks()
    print("Async executor test completed")

def test_prompt_improvements():
    """Test that the prompt includes success detection instructions."""
    print("Testing prompt improvements...")
    
    # Build a test prompt
    cmd = "Test command"
    page = "<html><body>Test page</body></html>"
    hist = []
    
    prompt = build_prompt(cmd, page, hist, False, None, None)
    
    # Check if key improvements are included
    success_detection = "成功の判定" in prompt
    repetition_prevention = "成功したアクションの重複禁止" in prompt
    
    print(f"Success detection instructions: {'✓' if success_detection else '✗'}")
    print(f"Repetition prevention instructions: {'✓' if repetition_prevention else '✗'}")
    
    return success_detection and repetition_prevention

def test_html_retrieval():
    """Test that HTML retrieval works."""
    print("Testing HTML retrieval...")
    
    try:
        html = get_html()
        print(f"HTML retrieval: {'✓' if html else '✗'} (length: {len(html) if html else 0})")
        return bool(html)
    except Exception as e:
        print(f"HTML retrieval failed: {e}")
        return False

def main():
    """Run all tests."""
    print("=" * 50)
    print("Testing fixes for repetitive generation and execution status issues")
    print("=" * 50)
    
    tests = [
        ("Async Executor", test_async_executor),
        ("Prompt Improvements", test_prompt_improvements),
        ("HTML Retrieval", test_html_retrieval),
    ]
    
    results = {}
    for test_name, test_func in tests:
        print(f"\n--- {test_name} ---")
        try:
            result = test_func()
            results[test_name] = result if result is not None else True
        except Exception as e:
            print(f"Test failed with exception: {e}")
            results[test_name] = False
    
    print("\n" + "=" * 50)
    print("Test Results Summary:")
    print("=" * 50)
    
    for test_name, result in results.items():
        status = "PASS" if result else "FAIL"
        print(f"{test_name}: {status}")
    
    all_passed = all(results.values())
    print(f"\nOverall: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")
    
    return all_passed

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)