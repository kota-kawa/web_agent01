# Yahoo Homepage Navigation Bug Fix

## Problem Statement

タスクの実行途中でヤフーのトップページ戻ってしまう不具合が発生するので、この問題を解決して

Translation: "A bug occurs where it returns to Yahoo's top page during task execution, so please solve this problem."

## Root Cause Analysis

The bug was caused by the browser recreation mechanism that periodically refreshes the browser for stability:

1. **Periodic Browser Refresh**: Every 50 DSL executions (configurable via `BROWSER_REFRESH_INTERVAL`), the system automatically recreates the browser by calling `_recreate_browser()`.

2. **Context Loss During Recreation**: When the browser was recreated:
   - The current page was closed, losing the task context
   - A new browser/page was created but remained empty (about:blank)
   - The task lost its current URL and context

3. **Frequent Health Checks**: API endpoints like `/source`, `/screenshot`, and `/elements` called `_init_browser()` on every request, potentially triggering unnecessary browser recreations.

## Solution Implementation

### 1. URL Preservation During Browser Recreation

**Modified `_recreate_browser()` function:**
- Save the current URL before closing the browser
- Exclude DEFAULT_URL (Yahoo homepage) and `about:` URLs from preservation
- After browser recreation, navigate back to the preserved URL
- Added comprehensive logging for debugging

```python
# Save current URL before closing the browser to preserve task context
current_url = None
try:
    if PAGE:
        current_url = await PAGE.url()
        # Only preserve non-default URLs to avoid navigating back to Yahoo during tasks
        if current_url and current_url != DEFAULT_URL and not current_url.startswith("about:"):
            log.info("Preserving current URL during browser recreation: %s", current_url)
        else:
            current_url = None
except Exception:
    current_url = None

# ... browser recreation logic ...

# Navigate back to preserved URL if we had one
if current_url and PAGE:
    try:
        log.info("Navigating back to preserved URL after browser recreation: %s", current_url)
        await PAGE.goto(current_url, wait_until="load", timeout=NAVIGATION_TIMEOUT)
    except Exception as e:
        log.warning("Failed to navigate back to preserved URL %s: %s", current_url, e)
```

### 2. Optimized Browser Health Checks

**Modified API endpoints** (`/source`, `/screenshot`, `/elements`):
- Check browser health before initialization
- Only call `_init_browser()` if the browser is not healthy
- Reduces unnecessary browser recreations

```python
# Only initialize browser if it's not already healthy
if not PAGE or not _run(_check_browser_health()):
    _run(_init_browser())
```

## Benefits

1. **Task Continuity**: Tasks no longer get interrupted by returning to Yahoo homepage
2. **Performance**: Reduced unnecessary browser initializations
3. **Stability**: Maintained the periodic refresh mechanism for browser stability
4. **Backward Compatibility**: First-time initialization still navigates to Yahoo homepage

## Test Validation

Created comprehensive test suites that validate:
- ✅ URL preservation logic with various scenarios
- ✅ DEFAULT_URL exclusion (Yahoo homepage not preserved during tasks)
- ✅ about: URL exclusion 
- ✅ Health check optimization in API endpoints
- ✅ Periodic refresh behavior preservation
- ✅ Task execution flow integrity

## Configuration

The fix works with existing configuration:
- `BROWSER_REFRESH_INTERVAL`: Controls how often browser is recreated (default: 50)
- `DEFAULT_URL`: The homepage to navigate to on first initialization
- `NAVIGATION_TIMEOUT`: Timeout for URL navigation during restoration

## Impact

This fix ensures that:
1. Tasks can execute for extended periods without losing context
2. Browser stability is maintained through periodic refresh
3. Yahoo homepage is only shown on initial startup, not during task execution
4. Performance is improved through optimized health checks

The bug that caused tasks to unexpectedly return to Yahoo's top page during execution has been resolved.