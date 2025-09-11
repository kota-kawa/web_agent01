# Memory Field Implementation

## Overview

The LLM response processing system now supports an optional "memory" field that allows the AI to store important information across multiple page interactions. This is particularly useful when gathering information from multiple pages that needs to be remembered for the final user response.

## Usage

The memory field can be included in LLM JSON responses alongside the existing fields:

```json
{
    "explanation": "I found the product information on this page.",
    "actions": [
        {"action": "click", "target": "#next-page"}
    ],
    "complete": false,
    "memory": "Product A: $19.99, 5-star rating. Product B: $29.99, 4-star rating. User needs price comparison for final decision."
}
```

## Key Features

### Optional Field
- The memory field is completely optional
- Only include it when there's important information to remember
- Responses without memory work exactly as before

### Flexible Content
- Memory can contain any text content
- Supports multi-line text and special characters
- Ideal for storing key facts, URLs, prices, or any relevant data

### Backward Compatibility
- All existing LLM responses continue to work unchanged
- No modification needed to existing prompts or responses
- The memory field is simply ignored if not provided

## Example Use Cases

### Product Comparison
```json
{
    "explanation": "Found product details on the pricing page.",
    "actions": [{"action": "navigate", "url": "/products/compare"}],
    "complete": false,
    "memory": "Product A: $19.99, free shipping, 4.5 stars (120 reviews). Product B: $24.99, $5 shipping, 4.8 stars (89 reviews)."
}
```

### Information Gathering
```json
{
    "explanation": "Located the company contact information.",
    "actions": [{"action": "click", "target": "#support-link"}],
    "complete": false,
    "memory": "Company: TechCorp Inc. Phone: (555) 123-4567. Email: support@techcorp.com. Office hours: Mon-Fri 9AM-5PM EST."
}
```

### Final Summary
```json
{
    "explanation": "I have gathered all the requested information. Based on my research: Product A costs $19.99 with free shipping and has 4.5 stars from 120 reviews. Product B costs $24.99 with $5 shipping and has 4.8 stars from 89 reviews. Product B has better ratings but costs more including shipping.",
    "actions": [],
    "complete": true
}
```

## Technical Implementation

The memory field is processed in the `_post_process` function in `agent/llm/client.py`:

- Checks if "memory" exists in the LLM JSON response
- Only includes the memory field in the final response if it's present and non-empty
- Preserves all existing functionality for responses without memory

## Best Practices

1. **Use when needed**: Only include memory when there's actually important information to remember
2. **Be specific**: Store concrete facts, not vague descriptions
3. **Structure information**: Use clear formatting for easy reference later
4. **Keep relevant**: Focus on information that helps answer the user's original question

This implementation allows LLMs to maintain context across multiple page interactions while preserving full backward compatibility with existing functionality.