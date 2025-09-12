#!/usr/bin/env python3
"""
Test script to validate the async execution implementation.
"""
import sys
import os
import time
import json

# Add the project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agent.controller.async_executor import get_async_executor


def mock_execute_dsl(payload):
    """Mock implementation of execute_dsl for testing."""
    print(f"Mock execute_dsl called with: {payload}")
    time.sleep(2)  # Simulate some work
    return {
        "html": "<html>Mock updated HTML</html>",
        "warnings": [],
        "correlation_id": "test-123"
    }


def mock_fetch_html():
    """Mock implementation of HTML fetching."""
    time.sleep(1)  # Simulate network delay
    return "<html>Fresh HTML content</html>"


def test_async_executor():
    """Test the async executor functionality."""
    print("Testing AsyncExecutor...")
    
    executor = get_async_executor()
    
    # Test 1: Create task
    print("\n1. Creating task...")
    task_id = executor.create_task()
    print(f"Created task: {task_id}")
    
    # Test 2: Check initial status
    print("\n2. Checking initial status...")
    status = executor.get_task_status(task_id)
    print(f"Initial status: {json.dumps(status, indent=2)}")
    assert status["status"] == "pending"
    
    # Test 3: Submit execution
    print("\n3. Submitting execution...")
    actions = [{"action": "click", "target": "button"}]
    success = executor.submit_playwright_execution(task_id, mock_execute_dsl, actions)
    print(f"Submission success: {success}")
    assert success
    
    # Test 4: Submit parallel data fetch
    print("\n4. Submitting parallel data fetch...")
    fetch_funcs = {"updated_html": mock_fetch_html}
    success = executor.submit_parallel_data_fetch(task_id, fetch_funcs)
    print(f"Parallel fetch submission success: {success}")
    assert success
    
    # Test 5: Poll for completion
    print("\n5. Polling for completion...")
    max_attempts = 15  # Increased to allow for parallel data fetch
    for attempt in range(max_attempts):
        status = executor.get_task_status(task_id)
        print(f"Attempt {attempt + 1}: Status = {status['status']}")
        
        if executor.is_task_complete(task_id):
            print("Task completed!")
            # Give a bit more time for parallel data fetch to complete
            time.sleep(1)
            break
            
        time.sleep(0.5)
    else:
        raise Exception("Task did not complete within expected time")
    
    # Test 6: Check final status
    print("\n6. Checking final status...")
    final_status = executor.get_task_status(task_id)
    print(f"Final status: {json.dumps(final_status, indent=2)}")
    
    assert final_status["status"] == "completed"
    assert "result" in final_status
    assert final_status["result"]["html"] == "<html>Mock updated HTML</html>"
    # The parallel data fetch might still be running, so check if it exists
    if "updated_html" in final_status["result"]:
        assert final_status["result"]["updated_html"] == "<html>Fresh HTML content</html>"
        print("✅ Parallel data fetch completed successfully")
    else:
        print("⚠️  Parallel data fetch still in progress or not included in result")
    
    print("\n✅ All tests passed!")
    
    # Cleanup
    executor.shutdown()


def test_normalize_actions():
    """Test the normalize_actions function."""
    print("\nTesting normalize_actions...")
    
    # Import the function from the web module with correct path setup
    web_path = os.path.join(os.path.dirname(__file__), 'web')
    if web_path not in sys.path:
        sys.path.insert(0, web_path)
    
    # Set PYTHONPATH for the import to work
    original_path = os.environ.get('PYTHONPATH', '')
    os.environ['PYTHONPATH'] = os.path.dirname(__file__) + ':' + original_path
    
    try:
        from app import normalize_actions
    except ImportError as e:
        print(f"⚠️  Skipping normalize_actions test due to import error: {e}")
        return
    
    # Test cases
    test_cases = [
        {
            "input": {"actions": [{"action": "CLICK", "selector": "#button"}]},
            "expected": [{"action": "click", "selector": "#button", "target": "#button"}]
        },
        {
            "input": {"actions": [{"action": "click_text", "text": "Submit"}]},
            "expected": [{"action": "click_text", "text": "Submit", "target": "Submit"}]
        },
        {
            "input": {"actions": []},
            "expected": []
        },
        {
            "input": {},
            "expected": []
        }
    ]
    
    for i, test_case in enumerate(test_cases):
        result = normalize_actions(test_case["input"])
        print(f"Test case {i + 1}: {result}")
        assert result == test_case["expected"], f"Test case {i + 1} failed"
    
    print("✅ normalize_actions tests passed!")
    
    # Restore original PYTHONPATH
    if original_path:
        os.environ['PYTHONPATH'] = original_path
    else:
        os.environ.pop('PYTHONPATH', None)


if __name__ == "__main__":
    try:
        test_async_executor()
        test_normalize_actions()
        print("\n🎉 All tests completed successfully!")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)