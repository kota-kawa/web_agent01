# Element Catalog System Documentation

## Overview

The Element Catalog System introduces an observation phase that generates indexed catalogs of interactive elements, enabling precise and robust targeting using index numbers while maintaining backward compatibility with existing CSS and XPath selectors.

## Key Features

### 1. Element Catalog Generation
- **Observation Phase**: Automatically extracts actionable elements (buttons, links, inputs, etc.)
- **Stable Ordering**: Elements sorted by position (top-to-bottom, left-to-right)
- **Catalog Versioning**: Tracks changes using URL + DOM hash + viewport hash
- **Two Views**: Abbreviated view for LLM, full view for execution

### 2. Index-based Targeting
- Use `index=N` format for precise element targeting (e.g., `index=0`, `index=5`)
- Robust selector resolution with multiple fallback strategies
- Automatic fallback to CSS/XPath when index resolution fails

### 3. Structured Response Format
New response format with detailed error handling and observation data:

```json
{
  "success": true|false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message", 
    "details": {...}
  } | null,
  "observation": {
    "url": "current_page_url",
    "title": "page_title",
    "short_summary": "page_summary",
    "catalog_version": "version_hash",
    "nav_detected": true|false
  },
  "is_done": true|false,
  "complete": true|false
}
```

### 4. New Actions

#### refresh_catalog
Regenerates the element catalog to reflect current page state.
```json
{"action": "refresh_catalog"}
```

#### scroll_to_text
Scrolls to element containing specific text.
```json
{"action": "scroll_to_text", "target": "Search button"}
```

#### Enhanced wait
Support for different wait types:
```json
{"action": "wait", "until": "network_idle", "ms": 3000}
{"action": "wait", "until": "selector", "target": "css=button", "ms": 5000}
{"action": "wait", "until": "timeout", "ms": 2000}
```

## Configuration

### INDEX_MODE Environment Variable
Controls whether index-based targeting is enabled:

```bash
# Enable index mode (default)
export INDEX_MODE=true

# Disable index mode (fallback to CSS/XPath only)
export INDEX_MODE=false
```

## Usage Examples

### 1. Basic Index Targeting

Instead of:
```json
{"action": "click", "target": "css=button.submit-btn"}
```

Use:
```json
{"action": "click", "target": "index=0"}
```

### 2. Error Handling Flow

1. **CATALOG_OUTDATED**: Execute `refresh_catalog` then retry
2. **ELEMENT_NOT_FOUND**: Try different index or use `scroll_to_text` + `refresh_catalog`
3. **ELEMENT_NOT_INTERACTABLE**: Use `scroll_to_text` → `refresh_catalog` → retry

### 3. Element Catalog Display

The LLM receives formatted catalog information:

```
=== Element Catalog (vabc123) ===
Page: Example Page
Summary: A sample page with form and navigation

--- FORM ---
[0] button: Submit (#submit-btn) 
[1] textbox: Search input (input[name="q"])
[2] select: Country selector (.country-select)

--- NAV ---
[3] link: Home → /
[4] link: About → /about

Use index=N to target elements (e.g., index=0, index=5)
```

## Error Codes

| Code | Description | Action |
|------|-------------|---------|
| `CATALOG_OUTDATED` | Catalog version mismatch | Execute `refresh_catalog` |
| `ELEMENT_NOT_FOUND` | Index not found in catalog | Try different index or refresh |
| `ELEMENT_NOT_INTERACTABLE` | Element cannot be interacted with | Scroll or wait for element |
| `VALIDATION_ERROR` | Invalid action parameters | Fix action syntax |
| `EXECUTION_ERROR` | General execution failure | Check logs and retry |

## Implementation Details

### Element Extraction Criteria
- **Clickable**: `a[href]`, `button`, `[role="button"]`, etc.
- **Input**: `input:not([type="hidden"])`, `textarea`, `select`, etc.
- **Interactive**: `[contenteditable="true"]`, `details summary`, etc.
- **Visible**: `display != none`, `visibility != hidden`, `opacity != 0`
- **Enabled**: `disabled != true`, `aria-disabled != true`

### Robust Selector Generation
For each element, multiple selectors are generated in priority order:
1. **ID-based**: `css=#element-id`
2. **Data-testid**: `css=[data-testid="test-id"]`
3. **Role + text**: `css=[role="button"]:has-text("Submit")`
4. **Aria-label**: `css=[aria-label="Search button"]`
5. **Text content**: `text=Submit`
6. **Position-based CSS**: `css=button:nth-of-type(2)`
7. **XPath fallback**: `xpath=//button[2]`

### Catalog Versioning
Version generated from:
- Current URL
- DOM structure hash (lightweight)
- Viewport dimensions
- Combined into MD5 hash (12 characters)

## Backward Compatibility

- All existing CSS and XPath selectors continue to work
- Old response format fields (`html`, `warnings`) are maintained
- New fields are additive (`success`, `error`, `observation`, `is_done`)
- INDEX_MODE can be disabled to revert to old behavior

## API Endpoints

### GET /catalog
Returns current element catalog data.

### POST /catalog/refresh  
Manually refresh the element catalog.

## Testing

Run the test suite:
```bash
cd /path/to/web_agent01
python -m unittest discover tests/ -v
```

Test coverage includes:
- Element catalog generation and formatting
- Index-based targeting and resolution
- New action creation and validation
- Structured response format
- Backward compatibility
- Error handling scenarios

## Best Practices

### For LLM Instructions
1. **Prefer index targeting**: Use `index=N` when elements are in catalog
2. **Handle errors gracefully**: Follow error code guidance for recovery
3. **Refresh when needed**: Use `refresh_catalog` after navigation or DOM changes
4. **Scroll for missing elements**: Use `scroll_to_text` to find off-screen elements

### For Implementation
1. **Check INDEX_MODE**: Verify feature is enabled before using index targeting
2. **Validate versions**: Compare expected vs current catalog versions
3. **Fallback strategies**: Always provide CSS/XPath alternatives
4. **Log operations**: Track catalog versions and selector resolutions

## Migration Guide

### From CSS/XPath to Index Targeting

Old approach:
```json
[
  {"action": "click", "target": "css=button.submit"},
  {"action": "type", "target": "css=input[name='email']", "value": "user@example.com"},
  {"action": "click", "target": "xpath=//button[contains(text(), 'Submit')]"}
]
```

New approach:
```json
[
  {"action": "refresh_catalog"},
  {"action": "click", "target": "index=0"},
  {"action": "type", "target": "index=1", "value": "user@example.com"},
  {"action": "click", "target": "index=2"}
]
```

### Error Handling Migration

Old error handling:
```python
try:
    result = execute_dsl(actions)
    if "ERROR:" in result.get("warnings", []):
        # Handle errors from warnings
except Exception as e:
    # Handle exceptions
```

New error handling:
```python
result = execute_dsl(actions, expected_catalog_version="abc123")
if not result["success"]:
    error_code = result["error"]["code"]
    if error_code == "CATALOG_OUTDATED":
        # Refresh catalog and retry
        refresh_result = execute_dsl([{"action": "refresh_catalog"}])
        # Retry original action
    elif error_code == "ELEMENT_NOT_FOUND":
        # Try alternative approach
```

## Troubleshooting

### Common Issues

1. **Index not found**: Catalog may be outdated, try `refresh_catalog`
2. **Element not interactable**: Element may be off-screen, try `scroll_to_text`
3. **Catalog not generated**: Check INDEX_MODE is enabled and page has interactive elements
4. **Version mismatch**: Page may have changed, refresh catalog before continuing

### Debug Information

Enable debug logging to see:
- Catalog generation details
- Index resolution process
- Selector fallback attempts
- Version comparison results

### Performance Considerations

- Catalog generation is lightweight (JavaScript-based)
- Versions are cached until page changes
- Selector resolution has built-in fallbacks
- Network requests minimized through async execution