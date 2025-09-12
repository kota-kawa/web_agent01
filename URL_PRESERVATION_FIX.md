# Fix for Unexpected Yahoo Navigation Issue

## Problem Description

During task execution, the browser would unexpectedly navigate back to the Yahoo homepage (https://yahoo.co.jp), interrupting ongoing tasks and causing user frustration.

## Root Cause Analysis

The issue was caused by automatic browser recreation that occurred in three scenarios:

1. **Periodic Browser Refresh**: After every 50 DSL (Domain Specific Language) executions, the browser is automatically recreated for stability
2. **Browser Health Check Failures**: When browser health checks fail, the browser is recreated
3. **Manual Browser Recreation**: Explicit recreation calls for error recovery

The problem was in the `_init_browser()` function in `vnc/automation_server.py`, which always navigated to the `DEFAULT_URL` (Yahoo homepage) regardless of what page the user was currently on.

## Solution Implemented

### Changes Made

1. **Modified `_recreate_browser()` function**:
   - Added logic to capture the current URL before browser recreation
   - Added validation to avoid preserving problematic URLs (`chrome://`, `about:`, `data:` schemes)
   - Added proper error handling and logging

2. **Modified `_init_browser()` function**:
   - Added optional `preserve_url` parameter
   - Added URL validation to ensure preserved URLs are valid
   - Added fallback mechanism to default URL if preserved URL fails
   - Enhanced logging for debugging URL preservation

### Code Changes

**Before:**
```python
async def _recreate_browser():
    # ... cleanup code ...
    await _init_browser()  # Always navigates to Yahoo

async def _init_browser():
    # ... browser setup ...
    await PAGE.goto(DEFAULT_URL, ...)  # Always Yahoo
```

**After:**
```python
async def _recreate_browser():
    # Preserve current URL
    current_url = DEFAULT_URL
    if PAGE:
        current_url = PAGE.url
        # Filter out problematic URLs
        if current_url.startswith(("chrome://", "about:", "data:")):
            current_url = DEFAULT_URL
    
    # ... cleanup code ...
    await _init_browser(preserve_url=current_url)

async def _init_browser(preserve_url=None):
    # ... browser setup ...
    
    # Use preserved URL or default
    target_url = preserve_url if preserve_url and preserve_url != "about:blank" else DEFAULT_URL
    
    # Validate URL and navigate
    await PAGE.goto(target_url, ...)
```

## Benefits

1. **Preserves User Context**: Users stay on their current page instead of being redirected to Yahoo
2. **Maintains Task Continuity**: Ongoing tasks are not interrupted by unexpected navigation
3. **Safe Fallback**: Invalid or problematic URLs are filtered out and fall back to the default URL
4. **Enhanced Logging**: Better debugging information for URL preservation behavior
5. **Backward Compatibility**: Normal initialization (when no URL needs to be preserved) works exactly as before

## Testing

The fix includes:
- URL validation logic to prevent preserving invalid URLs
- Fallback mechanisms for error recovery
- Comprehensive logging for debugging
- Safe handling of edge cases (empty URLs, special schemes, etc.)

## Configuration

The behavior can be controlled via environment variables:
- `BROWSER_REFRESH_INTERVAL`: Controls how often periodic browser refresh occurs (default: 50 executions)
- `START_URL`: The default URL to use when no valid URL can be preserved (default: https://yahoo.co.jp)

## Impact

This fix should completely eliminate the unexpected Yahoo navigation issue while maintaining all existing functionality and stability features.