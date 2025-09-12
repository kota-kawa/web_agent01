#!/usr/bin/env python3
"""
Demonstration of enhanced Playwright error capture improvements.
This script shows how the improved system now captures comprehensive error information
for both JSON warnings and prompt error_line, including minor errors.
"""

import sys
import json
sys.path.append('.')

from agent.browser.vnc import execute_dsl
from agent.controller.prompt import build_prompt


def demo_comprehensive_error_capture():
    """Demonstrate comprehensive Playwright error capture."""
    print("🎭 DEMONSTRATION: Enhanced Playwright Error Capture")
    print("=" * 70)
    print()
    
    print("📋 IMPROVEMENTS IMPLEMENTED:")
    print("1. Enhanced error pattern detection in vnc.py")
    print("2. Comprehensive Playwright-specific error capture") 
    print("3. Improved error_line processing in prompt.py")
    print("4. Recent warnings integration from conversation history")
    print("5. Minor error capture for better LLM understanding")
    print()
    
    # Example 1: Show enhanced JSON warning capture
    print("🔍 EXAMPLE 1: Enhanced JSON Warning Capture")
    print("-" * 50)
    
    # Simulate a successful Playwright response with various error indicators
    mock_response = {
        "html": "<html><body>Test page</body></html>",
        "warnings": [
            "WARNING:playwright:Element not immediately visible, waited 1.5s",
            "INFO:playwright:locator=button#submit"
        ],
        "execution_info": "Element state: detached after click, selector: button#submit",
        "console_errors": ["TypeError: Cannot read property 'style' of null"]
    }
    
    print("📤 Simulated Playwright server response:")
    print(json.dumps(mock_response, indent=2))
    print()
    
    print("📥 Enhanced warnings that would be generated:")
    # Simulate the enhanced warning processing
    enhanced_warnings = [
        "WARNING:playwright:Element not immediately visible, waited 1.5s",
        "INFO:playwright:locator=button#submit", 
        "INFO:playwright:execution_info=Element state: detached after click, selector: button#submit",
        "ERROR:playwright:console_errors=[\"TypeError: Cannot read property 'style' of null\"]"
    ]
    
    for i, warning in enumerate(enhanced_warnings, 1):
        print(f"  {i}. {warning}")
    print()
    
    # Example 2: Show enhanced error_line processing
    print("🔍 EXAMPLE 2: Enhanced Error_Line Processing")
    print("-" * 50)
    
    # Sample errors that would be captured
    sample_errors = [
        "ERROR:auto:Connection timeout after 30 seconds",
        "WARNING:playwright:selector resolved to 0 elements",
        "INFO:context:at playwright.click (automation.js:123:45)",
        "playwright: waiting for selector button#submit to be visible",
        "locator.click: Timeout 30000ms exceeded",
        "execution context was destroyed",
        "page closed unexpectedly"
    ]
    
    # Sample conversation history with warnings
    sample_history = [
        {
            "user": "Click the submit button",
            "bot": {
                "explanation": "Attempting to click the submit button",
                "actions": [{"action": "click", "target": "#submit"}],
                "warnings": [
                    "ERROR:auto:Element not clickable - covered by overlay",
                    "INFO:playwright:selector=#submit"
                ],
                "complete": False
            }
        }
    ]
    
    print("📤 Input errors and history:")
    print("Errors:", sample_errors[:3], "... (and more)")
    print("Recent warnings from history:", [
        "ERROR:auto:Element not clickable - covered by overlay",
        "INFO:playwright:selector=#submit"
    ])
    print()
    
    # Build prompt to show error_line integration
    prompt = build_prompt(
        cmd="Try clicking the submit button again",
        page="<html><body><button id='submit'>Submit</button></body></html>",
        hist=sample_history,
        screenshot=False,
        elements=None,
        error=sample_errors
    )
    
    # Extract the error section
    error_section_start = prompt.find("## 現在のエラー状況")
    if error_section_start != -1:
        error_section_end = prompt.find("\n)", error_section_start)
        if error_section_end == -1:
            error_section_end = len(prompt)
        error_section = prompt[error_section_start:error_section_end]
        
        print("📥 Enhanced error_line in LLM prompt:")
        print(error_section[:800] + "..." if len(error_section) > 800 else error_section)
    print()
    
    # Example 3: Network error classification
    print("🔍 EXAMPLE 3: Enhanced Network Error Classification")
    print("-" * 50)
    
    network_error_examples = [
        ("Connection refused", "Connection refused - Automation server not accepting connections"),
        ("Failed to resolve hostname", "DNS resolution failed - Cannot resolve automation server hostname"),
        ("Network unreachable", "Network unreachable - Cannot reach automation server"),
        ("Connection timeout", "Connection timeout - Server not responding"),
        ("HTTP 500 with response", "HTTP 500 error - Internal Server Error - Response: {\"error\": \"Memory exceeded\"}")
    ]
    
    print("📤 Network error improvements:")
    for original, enhanced in network_error_examples:
        print(f"  Before: {original}")
        print(f"  After:  {enhanced}")
        print()
    
    print("✅ BENEFITS FOR LLM UNDERSTANDING:")
    print("=" * 70)
    print("• Comprehensive error context including minor issues")
    print("• Detailed Playwright-specific error patterns")
    print("• Enhanced network error classification")
    print("• Integration of recent warnings from conversation history")  
    print("• Better error recovery guidance with specific error types")
    print("• Improved debugging information for complex scenarios")
    print()
    
    print("🎯 RESULT: The LLM now receives much more detailed and actionable")
    print("   error information, enabling better understanding and recovery")
    print("   from both major and minor Playwright automation issues.")


if __name__ == "__main__":
    demo_comprehensive_error_capture()