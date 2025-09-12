# Fixes for Repetitive Generation and Execution Status Issues

This document describes the implemented fixes for the two main issues identified in the web agent system.

## Issues Fixed

### Issue 1: Repetitive Generation (すでに実行が上手くいっているのに、同じことを何度も生成する不具合)

**Root Cause:** The system was not properly retrieving updated page state after async execution completed, causing the LLM to think previous actions failed.

**Fixes Implemented:**
1. **Async Execution Improvements**
   - Modified `async_executor.py` to fetch updated HTML after Playwright actions complete
   - Added 0.5 second delay to ensure DOM changes are reflected
   - Removed parallel data fetching that could cause timing issues

2. **LLM Prompt Enhancements**
   - Added success detection criteria to help LLM identify when actions succeed
   - Emphasized checking for page changes, new elements, and state updates
   - Added explicit prohibition against repeating successful actions
   - Improved action planning guidance to check for success before proceeding

### Issue 2: Execution Status Check Failures (「⚠️ 実行状態の確認に失敗しました」エラー)

**Root Cause:** The polling mechanism for async execution status was fragile and failed easily due to network issues or server busy states.

**Fixes Implemented:**
1. **Improved Polling Mechanism**
   - Adaptive polling intervals (500ms to 2s) instead of fixed 1s
   - Better error handling with consecutive error counting
   - Request timeout handling (5s per request)
   - Maximum duration limit (60s total)

2. **Enhanced Health Checks**
   - Added `/health` endpoint for server status monitoring
   - Dual health checks (main server + automation server)
   - Better timeout handling with AbortSignal

3. **Better Error Handling**
   - Timeout status instead of complete failure
   - Fallback HTML retrieval when polling fails
   - More specific user feedback messages
   - Server communication status checking

## Files Modified

- `agent/controller/async_executor.py` - Async execution improvements
- `agent/controller/prompt.py` - LLM prompt enhancements
- `web/app.py` - Health check endpoint and error handling
- `web/static/browser_executor.js` - Polling improvements and health checks

## Testing

Run the test script to validate the fixes:

```bash
python test_fixes.py
```

The test validates:
- Async executor functionality
- Prompt improvements (success detection instructions)
- HTML retrieval capabilities

## Expected Results

After these fixes:

1. **Reduced Repetitive Generation**
   - LLM will better detect when actions have succeeded
   - Same actions won't be repeated unnecessarily
   - Better page state awareness after async execution

2. **Fewer Execution Status Errors**
   - More robust polling with better retry logic
   - Better error messages when issues occur
   - Fallback mechanisms for server communication problems
   - Health monitoring to detect server issues

## Manual Testing

To manually test the fixes:

1. **Start the application:**
   ```bash
   docker-compose up
   ```

2. **Test repetitive generation prevention:**
   - Give a command that involves navigation (e.g., "Go to Google and search for something")
   - Observe that successful navigation doesn't get repeated
   - Check that the system proceeds to the next logical step

3. **Test execution status improvements:**
   - Give commands that trigger async execution
   - Observe more informative status messages
   - Note reduced occurrence of "⚠️ 実行状態の確認に失敗しました" errors

## Monitoring

The new `/health` endpoint can be used to monitor server status:

```bash
curl http://localhost:5000/health
```

This returns JSON with server health information including async executor status.