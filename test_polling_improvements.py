#!/usr/bin/env python3
"""
Test script to validate improvements to execution status polling.
"""
import sys
import os
import time
import logging

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

def test_health_endpoint():
    """Test that the health endpoint is working and returns useful information."""
    print("=== Testing Health Endpoint ===")
    
    try:
        # Import Flask app (this requires the web app dependencies)
        from web.app import app
        
        with app.test_client() as client:
            response = client.get('/health')
            
            print(f"Health endpoint status: {response.status_code}")
            
            if response.status_code in [200, 206]:  # 206 = degraded but functional
                data = response.get_json()
                print(f"Health status: {data.get('status', 'unknown')}")
                print(f"Components: {data.get('components', {})}")
                print(f"Metrics: {data.get('metrics', {})}")
                return True
            else:
                print(f"Health check failed with status {response.status_code}")
                print(f"Response: {response.get_data(as_text=True)}")
                return False
                
    except Exception as e:
        print(f"Health endpoint test failed: {e}")
        return False

def test_async_executor_robustness():
    """Test that the async executor handles edge cases properly."""
    print("\n=== Testing Async Executor Robustness ===")
    
    try:
        from agent.controller.async_executor import AsyncExecutor
        
        # Create executor
        executor = AsyncExecutor(max_workers=2)
        
        # Test basic task creation
        task_id = executor.create_task()
        print(f"Created test task: {task_id}")
        
        # Test status retrieval
        status = executor.get_task_status(task_id)
        if status and status.get('status') == 'pending':
            print("✓ Task status retrieval working")
        else:
            print("✗ Task status retrieval failed")
            return False
        
        # Test cleanup functionality
        executor.cleanup_old_tasks()
        print("✓ Cleanup function working")
        
        return True
        
    except Exception as e:
        print(f"Async executor test failed: {e}")
        return False

def test_javascript_syntax():
    """Validate that our JavaScript changes don't have syntax errors."""
    print("\n=== Testing JavaScript Syntax ===")
    
    try:
        js_file = os.path.join(os.path.dirname(__file__), 'web', 'static', 'browser_executor.js')
        
        if not os.path.exists(js_file):
            print(f"✗ JavaScript file not found: {js_file}")
            return False
            
        # Read the file to check for basic syntax issues
        with open(js_file, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Basic checks
        if 'attemptGracefulFallback' not in content:
            print("✗ Missing new attemptGracefulFallback function")
            return False
            
        if 'completed_via_fallback' not in content:
            print("✗ Missing new fallback status handling")
            return False
            
        # Check for unmatched braces (basic check)
        open_braces = content.count('{')
        close_braces = content.count('}')
        
        if open_braces != close_braces:
            print(f"✗ Unmatched braces: {open_braces} open, {close_braces} close")
            return False
            
        print(f"✓ JavaScript file looks good ({len(content)} chars)")
        print(f"✓ Found new fallback functionality")
        
        return True
        
    except Exception as e:
        print(f"JavaScript syntax test failed: {e}")
        return False

def run_tests():
    """Run all tests and return overall result."""
    print("Testing polling improvements for execution status failures...")
    print("=" * 60)
    
    tests = [
        ("Health Endpoint", test_health_endpoint),
        ("Async Executor", test_async_executor_robustness), 
        ("JavaScript Syntax", test_javascript_syntax),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"Test {test_name} crashed: {e}")
            results.append((test_name, False))
    
    print("\n" + "=" * 60)
    print("Test Results Summary:")
    print("=" * 60)
    
    all_passed = True
    for test_name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"{test_name}: {status}")
        if not result:
            all_passed = False
    
    print("=" * 60)
    overall = "ALL TESTS PASSED" if all_passed else "SOME TESTS FAILED"
    print(f"\nOverall: {overall}")
    
    return all_passed

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)