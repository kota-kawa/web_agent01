# Web Agent Element Catalog System Documentation

This directory contains documentation for the Element Catalog System implementation.

## Files

- **[element_catalog_system.md](element_catalog_system.md)**: Complete documentation of the element catalog system, including features, configuration, API, and usage guidelines.

- **[usage_examples.py](usage_examples.py)**: Comprehensive code examples showing how to use the new element catalog features, including error handling, LLM integration, and migration patterns.

## Quick Start

### Enable Index Mode
```bash
export INDEX_MODE=true  # Default
```

### Basic Usage
```json
[
  {"action": "refresh_catalog"},
  {"action": "click", "target": "index=0"},
  {"action": "type", "target": "index=1", "value": "input text"}
]
```

### Error Handling
```python
result = execute_dsl(actions, expected_catalog_version="abc123")
if not result["success"]:
    if result["error"]["code"] == "CATALOG_OUTDATED":
        # Refresh catalog and retry
        pass
```

## Key Benefits

1. **Robust Targeting**: Multiple fallback selectors per element
2. **Stable References**: Index-based targeting survives minor DOM changes  
3. **Better Error Handling**: Structured error responses with specific codes
4. **LLM Friendly**: Simplified element catalogs for AI consumption
5. **Backward Compatible**: CSS/XPath selectors still work

## New Actions

- `refresh_catalog`: Regenerate element catalog
- `scroll_to_text`: Scroll to element containing text
- Enhanced `wait`: Support for network_idle, selector, timeout

## Response Format

All actions now return structured responses:
```json
{
  "success": true|false,
  "error": {"code": "...", "message": "...", "details": {...}} | null,
  "observation": {"url": "...", "title": "...", "catalog_version": "...", "nav_detected": true|false},
  "is_done": true|false,
  "complete": true|false
}
```

For more details, see the [complete documentation](element_catalog_system.md).