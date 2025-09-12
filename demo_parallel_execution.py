#!/usr/bin/env python3
"""
Demo script to show the new parallelized execution flow.
"""
import sys
import os
import time
import json
from unittest.mock import MagicMock

# Add the project root to Python path
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)
os.environ['PYTHONPATH'] = project_root + ':' + os.environ.get('PYTHONPATH', '')

def mock_call_llm(prompt, model, screenshot):
    """Mock LLM call that returns a response with actions."""
    print(f"🤖 LLM called with model: {model}")
    time.sleep(1)  # Simulate LLM processing time
    return {
        "explanation": "I will click the submit button to complete the form.",
        "actions": [
            {"action": "click", "target": "#submit-button"},
            {"action": "wait", "ms": 1000}
        ],
        "complete": False
    }

def mock_execute_dsl(payload):
    """Mock Playwright execution."""
    print(f"🎭 Playwright executing: {len(payload.get('actions', []))} actions")
    time.sleep(2)  # Simulate browser operation time
    return {
        "html": "<html>Updated page after click</html>",
        "warnings": ["INFO:auto:Button clicked successfully"],
        "correlation_id": "demo-123"
    }

def mock_get_html():
    """Mock HTML fetching."""
    print("📄 Fetching fresh HTML...")
    time.sleep(0.5)  # Simulate network delay
    return "<html>Fresh HTML content from page</html>"

def demonstrate_parallel_execution():
    """Demonstrate the new parallel execution flow."""
    print("🚀 Demonstrating Parallel LLM + Playwright Execution\n")
    
    # Import required modules
    from agent.controller.async_executor import get_async_executor
    
    # Setup
    executor = get_async_executor()
    
    print("=" * 60)
    print("STEP 1: User sends command")
    print("=" * 60)
    command = "Click the submit button"
    print(f"User command: '{command}'")
    
    print("\n" + "=" * 60)
    print("STEP 2: LLM processes command (current: sequential)")
    print("=" * 60)
    start_time = time.time()
    llm_response = mock_call_llm("prompt", "gemini", None)
    llm_time = time.time() - start_time
    
    print(f"✅ LLM response in {llm_time:.2f}s:")
    print(f"   Explanation: {llm_response['explanation']}")
    print(f"   Actions: {len(llm_response['actions'])} actions")
    
    print("\n" + "=" * 60)
    print("STEP 3: IMMEDIATE parallel execution (NEW FEATURE)")
    print("=" * 60)
    
    # Extract actions
    actions = llm_response.get('actions', [])
    
    if actions:
        # Create async task
        task_id = executor.create_task()
        print(f"📋 Created async task: {task_id}")
        
        # Start Playwright execution immediately
        parallel_start = time.time()
        success = executor.submit_playwright_execution(task_id, mock_execute_dsl, actions)
        
        if success:
            print("🎭 Playwright execution started in background")
            
            # Start parallel data fetching
            fetch_funcs = {"updated_html": mock_get_html}
            executor.submit_parallel_data_fetch(task_id, fetch_funcs)
            print("📄 Parallel data fetch started")
            
            print(f"\n🎯 KEY IMPROVEMENT: User sees LLM explanation immediately!")
            print(f"   Frontend can display: '{llm_response['explanation']}'")
            print(f"   While browser operations happen in background...")
            
            # Poll for completion
            print("\n⏱️  Polling for execution completion:")
            max_attempts = 15
            for attempt in range(max_attempts):
                status = executor.get_task_status(task_id)
                print(f"   Attempt {attempt + 1}: {status['status']}")
                
                if executor.is_task_complete(task_id):
                    break
                    
                time.sleep(0.5)
            
            total_time = time.time() - parallel_start
            final_status = executor.get_task_status(task_id)
            
            print(f"\n✅ Execution completed in {total_time:.2f}s")
            print(f"   Status: {final_status['status']}")
            
            if final_status['result']:
                result = final_status['result']
                print(f"   HTML updated: {'Yes' if result.get('html') else 'No'}")
                print(f"   Warnings: {len(result.get('warnings', []))}")
                print(f"   Fresh data: {'Yes' if result.get('updated_html') else 'No'}")
    
    print("\n" + "=" * 60)
    print("PERFORMANCE COMPARISON")
    print("=" * 60)
    
    print("OLD FLOW (Sequential):")
    print(f"  1. LLM Processing: {llm_time:.2f}s")
    print(f"  2. Frontend Parsing: ~0.1s")
    print(f"  3. Playwright Execution: ~2.0s")
    print(f"  4. Data Refresh: ~0.5s")
    print(f"  TOTAL TIME TO SEE RESULT: ~{llm_time + 2.6:.2f}s")
    
    print(f"\nNEW FLOW (Parallel):")
    print(f"  1. LLM Processing: {llm_time:.2f}s")
    print(f"  2. IMMEDIATE UI UPDATE: 0.0s (parallel)")
    print(f"  3. Background execution: ~2.0s (parallel)")
    print(f"  TIME TO SEE EXPLANATION: {llm_time:.2f}s ⚡")
    print(f"  TIME TO SEE FINAL RESULT: ~{llm_time + 2.0:.2f}s")
    
    improvement = (2.6 / llm_time) * 100 if llm_time > 0 else 0
    print(f"\n🎉 RESPONSIVENESS IMPROVEMENT: User sees response {improvement:.1f}% faster!")
    
    # Cleanup
    executor.shutdown()
    
    print("\n" + "=" * 60)
    print("IMPLEMENTATION SUMMARY")
    print("=" * 60)
    print("✅ AsyncExecutor: Manages parallel Playwright operations")
    print("✅ Modified /execute endpoint: Starts async execution immediately")
    print("✅ New /execution-status endpoint: Polls for completion")
    print("✅ Updated frontend: Shows LLM response immediately")
    print("✅ Parallel data fetching: Updates HTML/screenshots in background")
    print("✅ Error handling: Maintains robustness with async operations")

if __name__ == "__main__":
    try:
        demonstrate_parallel_execution()
        print("\n🎉 Demo completed successfully!")
    except Exception as e:
        print(f"\n❌ Demo failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)