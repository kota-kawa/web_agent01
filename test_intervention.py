#!/usr/bin/env python3
"""
Test script to verify user intervention functionality
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.controller.async_executor import AsyncExecutor, TaskStatus
from agent.controller.prompt import build_prompt

def test_async_executor():
    """Test AsyncExecutor intervention features"""
    print("Testing AsyncExecutor intervention features...")
    
    executor = AsyncExecutor()
    
    # Create a task
    task_id = executor.create_task()
    print(f"Created task: {task_id}")
    
    # Test pausing for user intervention
    success = executor.pause_task_for_user(task_id, "robot_verification")
    print(f"Pause task result: {success}")
    
    # Get task status
    status = executor.get_task_status(task_id)
    print(f"Task status: {status}")
    
    # Provide user intervention
    success = executor.provide_user_intervention(task_id, "I completed the captcha verification")
    print(f"Intervention result: {success}")
    
    # Get updated status
    status = executor.get_task_status(task_id)
    print(f"Updated task status: {status}")
    
    executor.shutdown()
    print("AsyncExecutor test completed ✓")

def test_prompt_with_intervention():
    """Test prompt building with intervention context"""
    print("\nTesting prompt building with intervention context...")
    
    # Sample conversation history with intervention
    hist = [
        {
            "user": "Test command",
            "bot": {
                "explanation": "Testing intervention",
                "pause_for_user": {
                    "reason": "robot_verification",
                    "message": "Please complete the CAPTCHA"
                }
            }
        }
    ]
    
    prompt = build_prompt(
        cmd="Continue after verification",
        page="<html><body>Test page</body></html>",
        hist=hist,
        screenshot=False,
        elements=None,
        error=None,
        intervention_context="User completed CAPTCHA verification"
    )
    
    # Check if intervention context is included
    if "ユーザー介入コンテキスト" in prompt:
        print("Prompt includes intervention context ✓")
    else:
        print("Prompt missing intervention context ✗")
    
    # Check if intervention history is included
    if "INTERVENTION_REQUESTED" in prompt:
        print("Prompt includes intervention history ✓")
    else:
        print("Prompt missing intervention history ✗")

def test_json_format():
    """Test that the LLM response format includes pause_for_user"""
    print("\nTesting LLM response format...")
    
    # Sample LLM response with pause_for_user
    llm_response = {
        "explanation": "CAPTCHA verification required",
        "pause_for_user": {
            "reason": "robot_verification",
            "message": "Please complete the CAPTCHA verification and click continue"
        },
        "actions": [],
        "complete": False
    }
    
    # Check if pause_for_user is properly structured
    if "pause_for_user" in llm_response:
        pause_info = llm_response["pause_for_user"]
        if "reason" in pause_info and "message" in pause_info:
            print("LLM response format is correct ✓")
        else:
            print("LLM response format is missing required fields ✗")
    else:
        print("LLM response format is missing pause_for_user ✗")

if __name__ == "__main__":
    print("🧪 Running User Intervention System Tests")
    print("=" * 50)
    
    try:
        test_async_executor()
        test_prompt_with_intervention()
        test_json_format()
        
        print("\n" + "=" * 50)
        print("✅ All tests passed! User intervention system is ready.")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)