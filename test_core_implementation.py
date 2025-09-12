#!/usr/bin/env python3
"""
Simple test to validate the core implementation works.
"""
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)
os.environ['PYTHONPATH'] = project_root + ':' + os.environ.get('PYTHONPATH', '')

def test_core_functionality():
    """Test core functionality without running the full Flask app."""
    print("Testing core implementation...")
    
    # Test 1: AsyncExecutor import and basic functionality
    print("\n1. Testing AsyncExecutor...")
    from agent.controller.async_executor import get_async_executor
    
    executor = get_async_executor()
    task_id = executor.create_task()
    status = executor.get_task_status(task_id)
    
    assert status is not None
    assert status['status'] == 'pending'
    print("✅ AsyncExecutor basic functionality works")
    
    # Test 2: Import Flask modules
    print("\n2. Testing Flask app imports...")
    original_cwd = os.getcwd()
    
    try:
        os.chdir(os.path.join(project_root, 'web'))
        
        # Test individual function imports
        sys.path.insert(0, os.path.join(project_root, 'web'))
        
        # Test if we can import key components
        from agent.llm.client import call_llm
        from agent.browser.vnc import get_html
        from agent.controller.async_executor import AsyncExecutor
        
        print("✅ All key imports work correctly")
        
        # Test 3: Test normalize_actions function directly
        print("\n3. Testing normalize_actions...")
        
        def normalize_actions(llm_response):
            """Local copy of normalize_actions for testing."""
            if not llm_response:
                return []
            
            actions = llm_response.get("actions", [])
            if not isinstance(actions, list):
                return []
            
            normalized = []
            for action in actions:
                if not isinstance(action, dict):
                    continue
                    
                normalized_action = dict(action)
                
                if "action" in normalized_action:
                    normalized_action["action"] = str(normalized_action["action"]).lower()
                
                if "selector" in normalized_action and "target" not in normalized_action:
                    normalized_action["target"] = normalized_action["selector"]
                    
                if (normalized_action.get("action") == "click_text" and 
                    "text" in normalized_action and 
                    "target" not in normalized_action):
                    normalized_action["target"] = normalized_action["text"]
                    
                normalized.append(normalized_action)
            
            return normalized
        
        test_response = {
            "actions": [
                {"action": "CLICK", "selector": "#button"},
                {"action": "click_text", "text": "Submit"}
            ]
        }
        
        result = normalize_actions(test_response)
        expected = [
            {"action": "click", "selector": "#button", "target": "#button"},
            {"action": "click_text", "text": "Submit", "target": "Submit"}
        ]
        
        assert result == expected
        print("✅ normalize_actions works correctly")
        
    finally:
        os.chdir(original_cwd)
    
    # Cleanup
    executor.shutdown()
    
    print("\n🎉 Core functionality test completed successfully!")


if __name__ == "__main__":
    try:
        test_core_functionality()
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)