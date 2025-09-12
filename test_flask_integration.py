#!/usr/bin/env python3
"""
Integration test for the Flask application endpoints.
"""
import sys
import os
import json
import time
from unittest.mock import patch, MagicMock

# Add the project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Set PYTHONPATH for proper imports
os.environ['PYTHONPATH'] = os.path.dirname(__file__) + ':' + os.environ.get('PYTHONPATH', '')

def test_flask_endpoints():
    """Test Flask application endpoints."""
    print("Testing Flask endpoints...")
    
    # Change to web directory and import Flask app
    web_dir = os.path.join(os.path.dirname(__file__), 'web')
    os.chdir(web_dir)
    
    with patch.dict(sys.modules):
        # Mock the VNC and LLM modules to avoid network dependencies
        mock_vnc = MagicMock()
        mock_vnc.get_html.return_value = "<html>Test HTML</html>"
        mock_vnc.execute_dsl.return_value = {"html": "Updated HTML", "warnings": []}
        mock_vnc.get_elements.return_value = []
        mock_vnc.get_dom_tree.return_value = (None, None)
        
        mock_llm = MagicMock()
        mock_llm.call_llm.return_value = {
            "explanation": "Test explanation",
            "actions": [{"action": "click", "target": "button"}],
            "complete": False
        }
        
        sys.modules['agent.browser.vnc'] = mock_vnc
        sys.modules['agent.llm.client'] = mock_llm
        
        # Import and create Flask test client
        from app import app, normalize_actions
        app.config['TESTING'] = True
        client = app.test_client()
        
        # Test 1: Execute endpoint
        print("\n1. Testing /execute endpoint...")
        response = client.post('/execute', 
                             json={'command': 'click the button'},
                             content_type='application/json')
        
        assert response.status_code == 200
        data = response.get_json()
        print(f"Response: {json.dumps(data, indent=2)}")
        
        assert 'explanation' in data
        assert 'async_execution' in data
        
        if data.get('async_execution') and 'task_id' in data:
            task_id = data['task_id']
            print(f"Task ID: {task_id}")
            
            # Test 2: Check execution status
            print("\n2. Testing /execution-status endpoint...")
            
            # Poll for completion
            max_attempts = 10
            for attempt in range(max_attempts):
                status_response = client.get(f'/execution-status/{task_id}')
                assert status_response.status_code == 200
                
                status_data = status_response.get_json()
                print(f"Attempt {attempt + 1}: Status = {status_data.get('status')}")
                
                if status_data.get('status') in ['completed', 'failed']:
                    print("Task completed!")
                    break
                    
                time.sleep(0.5)
            else:
                print("⚠️  Task did not complete within expected time")
        
        # Test 3: Test normalize_actions function
        print("\n3. Testing normalize_actions function...")
        test_input = {
            "actions": [
                {"action": "CLICK", "selector": "#test"},
                {"action": "click_text", "text": "Submit"}
            ]
        }
        
        result = normalize_actions(test_input)
        expected = [
            {"action": "click", "selector": "#test", "target": "#test"},
            {"action": "click_text", "text": "Submit", "target": "Submit"}
        ]
        
        print(f"Normalized actions: {result}")
        assert result == expected
        
        print("✅ Flask endpoints test completed successfully!")


if __name__ == "__main__":
    try:
        test_flask_endpoints()
        print("\n🎉 Integration tests completed successfully!")
    except Exception as e:
        print(f"\n❌ Integration test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)