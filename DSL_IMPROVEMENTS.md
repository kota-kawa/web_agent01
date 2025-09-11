# DSL Improvements Implementation

This document describes the improvements implemented to enhance the robustness and reliability of the DSL execution system.

## Summary of Changes

### 1. Automatic Wait Actions for Dynamic Elements (動的要素に対する明示的な待機アクション)

**Problem**: Previously, waiting for dynamic elements after navigation was left to LLM instructions, which could lead to failures when interacting with elements that hadn't fully loaded.

**Solution**: 
- Added `_wait_for_page_ready()` function that automatically waits for common page elements after navigation
- Integrated automatic waiting into `navigate`, `go_back`, and `go_forward` actions
- Uses common selectors like `body`, `main`, `nav`, `header`, `footer` to detect when page is ready
- Includes additional DOM stabilization to handle SPA dynamic content

**Code Changes**:
- New function: `_wait_for_page_ready()` in `vnc/automation_server.py`
- Modified navigation actions to call `_wait_for_page_ready()` automatically
- Enhanced page stabilization after each wait

### 2. Unified Retry Control for DSL Commands (DSLコマンドのリトライ制御統合)

**Problem**: Element not found errors were being converted to warnings instead of exceptions, preventing the outer retry logic from working properly.

**Solution**:
- Modified `_apply()` function to accept `is_final_retry` parameter
- Changed element not found logic to throw exceptions on non-final retries
- Only return warnings on the final retry attempt
- Updated retry loop to handle exceptions properly and pass the final retry flag

**Code Changes**:
- Modified `_apply(act: Dict, is_final_retry: bool = False)` signature
- Updated element not found handling in locator section
- Enhanced retry logic in `_run_actions()` to use the final retry flag
- Applied similar logic to `wait_for_selector` and navigation failures

### 3. Enhanced DSL Execution API Robustness (DSL実行APIの堅牢性向上)

**Problem**: Uncaught exceptions could still cause 500 errors to be returned instead of structured JSON responses.

**Solution**:
- Added global error handlers to both VNC and Web Flask applications
- Implemented `@app.errorhandler(500)` and `@app.errorhandler(Exception)` 
- All unhandled exceptions are now converted to JSON responses with 200 status
- Added correlation IDs for better error tracking and debugging

**Code Changes**:
- Global error handlers in `vnc/automation_server.py`
- Global error handlers in `web/app.py`
- Consistent JSON response format for all errors
- Proper logging with correlation IDs

### 4. Periodic Browser Context Refresh (定期ブラウザコンテキストリフレッシュ)

**Problem**: Long-running browser sessions could accumulate memory leaks and unstable states.

**Solution**:
- Added `BROWSER_REFRESH_INTERVAL` configuration (default: 50 executions)
- Implemented `_check_and_refresh_browser()` function to track execution count
- Automatic browser recreation after reaching the configured interval
- Integrated into the DSL execution flow with proper error handling

**Code Changes**:
- New configuration: `BROWSER_REFRESH_INTERVAL` and `_DSL_EXECUTION_COUNT`
- New function: `_check_and_refresh_browser()` 
- Integration in `execute-dsl` endpoint after DSL execution
- Reset counter and error handling for refresh failures

## Configuration Options

### Environment Variables

- `BROWSER_REFRESH_INTERVAL`: Number of DSL executions before browser refresh (default: 50)
- `ACTION_TIMEOUT`: Individual action timeout in milliseconds (default: 10000)
- `NAVIGATION_TIMEOUT`: Navigation-specific timeout in milliseconds (default: 30000)
- `WAIT_FOR_SELECTOR_TIMEOUT`: Wait for selector timeout in milliseconds (default: 5000)
- `MAX_RETRIES`: Maximum retry attempts per action (default: 3)
- `SPA_STABILIZE_TIMEOUT`: SPA stabilization timeout in milliseconds (default: 2000)

## Error Handling Improvements

### Structured Error Responses

All errors now return structured JSON responses with:
- `html`: Current page HTML (may be empty on critical failures)
- `warnings`: Array of warning/error messages with correlation IDs
- `correlation_id`: Unique identifier for tracking the request

### Error Classification

Errors are classified as:
- **Internal errors** (retryable): Element not found, timeouts, page navigation issues
- **External errors** (non-retryable): Network errors, 403/404 responses, blocked domains

### Retry Logic

1. Actions retry up to `MAX_RETRIES` times (default: 3)
2. Element not found and similar issues throw exceptions for retry
3. Only on final retry, warnings are returned instead of exceptions
4. External errors (network issues) don't trigger retries

## Testing and Validation

The implementation includes:
- Validation script (`validate_implementation.py`) for basic functionality
- Improvement test script (`/tmp/test_improvements.py`) for new features
- All existing validation tests pass
- New functions are properly typed and documented

## Impact on Existing Code

These changes are designed to be **backward compatible**:
- Existing DSL JSON format remains unchanged
- API endpoints maintain the same interface
- Response format is enhanced but compatible
- Configuration is optional with sensible defaults

## Benefits

1. **Improved Reliability**: Automatic waits reduce element interaction failures
2. **Better Error Recovery**: Proper retry logic handles transient failures
3. **Enhanced Robustness**: Global error handlers prevent 500 errors
4. **Memory Stability**: Periodic refresh prevents browser memory issues
5. **Better Debugging**: Correlation IDs and structured logging improve troubleshooting