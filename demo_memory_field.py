#!/usr/bin/env python3
"""
Demo script to show memory field functionality in action.
This can be run to see how the memory field works with sample LLM responses.
"""
import sys
import os
import json

# Mock dependencies to avoid import errors
class MockModule:
    def __getattr__(self, name):
        return MockModule()
    def __call__(self, *args, **kwargs):
        return MockModule()

sys.modules['google.generativeai'] = MockModule()
sys.modules['google'] = MockModule()
sys.modules['groq'] = MockModule()

# Add current directory to path
sys.path.insert(0, '/home/runner/work/web_agent01/web_agent01')

from agent.llm.client import _post_process

def demo_memory_field():
    """Demonstrate memory field functionality with realistic examples."""
    print("🧠 Memory Field Functionality Demo")
    print("=" * 50)
    
    examples = [
        {
            "title": "Product Research - Page 1",
            "response": '''I found the first product on this page.

```json
{
    "explanation": "Located Product A details on the catalog page.",
    "actions": [
        {"action": "click", "target": "#product-b-link"}
    ],
    "complete": false,
    "memory": "Product A: Widget Pro - $19.99, 4.5 stars (120 reviews), free shipping over $25"
}
```'''
        },
        {
            "title": "Product Research - Page 2", 
            "response": '''Found the second product information.

```json
{
    "explanation": "Retrieved Product B specifications and pricing.",
    "actions": [
        {"action": "click", "target": "#compare-products"}
    ],
    "complete": false,
    "memory": "Product A: Widget Pro - $19.99, 4.5 stars (120 reviews), free shipping over $25\\nProduct B: Widget Deluxe - $29.99, 4.8 stars (89 reviews), free shipping included"
}
```'''
        },
        {
            "title": "Final Comparison",
            "response": '''I have completed the product comparison research.

```json
{
    "explanation": "Based on my research, I found two products. Product A (Widget Pro) costs $19.99 with 4.5 stars from 120 reviews. Product B (Widget Deluxe) costs $29.99 with 4.8 stars from 89 reviews and includes free shipping. Product B has better ratings and free shipping, but costs $10 more.",
    "actions": [],
    "complete": true
}
```'''
        },
        {
            "title": "Simple Task (No Memory)",
            "response": '''Navigation completed successfully.

```json
{
    "explanation": "Successfully navigated to the home page.",
    "actions": [],
    "complete": true
}
```'''
        }
    ]
    
    for i, example in enumerate(examples, 1):
        print(f"\n{i}. {example['title']}")
        print("-" * 30)
        
        result = _post_process(example['response'])
        
        print(f"Explanation: {result['explanation']}")
        print(f"Actions: {len(result['actions'])} action(s)")
        print(f"Complete: {result['complete']}")
        
        if 'memory' in result:
            print(f"Memory: {result['memory']}")
            print("📝 Memory field present - information stored for later use")
        else:
            print("Memory: Not provided")
            print("💭 No memory needed for this interaction")
    
    print("\n" + "=" * 50)
    print("✅ Demo complete! The memory field allows LLMs to store")
    print("   important information across multiple page interactions.")


if __name__ == "__main__":
    demo_memory_field()