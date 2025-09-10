# Troubleshooting Guide

This guide helps diagnose and resolve common issues with the DSL execution system.

## Common Error Scenarios

### 1. Element Not Found Errors

**Error:** `WARNING:auto:Element not found: #selector`

**Causes:**
- Element doesn't exist on the page
- Page hasn't fully loaded
- Selector is incorrect or too specific

**Solutions:**
- Use broader selectors: `css=.button || text=Submit || role=button[name="Submit"]`
- Add wait actions before element interaction: `{"action": "wait", "ms": 2000}`
- Use text-based selectors for dynamic content: `text=Login`
- Check if the element is inside an iframe or shadow DOM

### 2. Navigation Failures

**Error:** `WARNING:auto:Navigation failed - ネットワークエラー`

**Causes:**
- Network connectivity issues
- Domain is blocked by security settings
- Site is down or slow to respond

**Solutions:**
- Check network connectivity
- Verify domain is in `ALLOWED_DOMAINS` if configured
- Increase `NAVIGATION_TIMEOUT` for slow sites
- Use alternative URLs or mirror sites

### 3. Timeout Issues

**Error:** `WARNING:auto:Operation timed out`

**Causes:**
- Default timeouts too short for slow sites
- Heavy page load with many resources
- SPA rendering delays

**Solutions:**
```bash
# Increase relevant timeouts
export ACTION_TIMEOUT=15000
export NAVIGATION_TIMEOUT=30000
export SPA_STABILIZE_TIMEOUT=5000
```

### 4. Click/Interaction Failures

**Error:** `WARNING:auto:click operation failed - Element not enabled`

**Causes:**
- Element is disabled or hidden
- Element is covered by another element
- Page is still loading

**Solutions:**
- Add stabilization wait before interaction
- Use `force: true` in action parameters (advanced)
- Try alternative interaction methods (hover before click)
- Check if element requires focus first

### 5. Concurrent Execution Issues

**Error:** `WARNING:auto:Browser unhealthy, recreating...`

**Causes:**
- Multiple simultaneous DSL executions
- Browser process crashed
- Memory issues

**Solutions:**
- Reduce concurrent requests
- Enable browser recreation: `USE_INCOGNITO_CONTEXT=true`
- Monitor system resources
- Add delays between requests

## Debugging Steps

### 1. Check Correlation ID

Every DSL execution has a unique correlation ID. Look for it in warnings:
```
[a1b2c3d4] Action 1: ERROR:auto:Invalid selector
```

### 2. Review Debug Artifacts

When `SAVE_DEBUG_ARTIFACTS=true`, failed actions save:
- Screenshots: `{correlation_id}_screenshot.png`
- HTML snapshots: `{correlation_id}_page.html`
- Error context: `{correlation_id}_error.txt`

### 3. Enable Verbose Logging

Check server logs for detailed action execution:
```bash
# Look for correlation ID in logs
grep "a1b2c3d4" /path/to/logfile
```

### 4. Test Individual Actions

Break down complex DSL into individual actions:
```json
{
  "actions": [
    {"action": "navigate", "target": "https://example.com"},
    {"action": "wait", "ms": 3000},
    {"action": "click", "target": "css=#login || text=Login"}
  ]
}
```

## Performance Optimization

### 1. Reduce Action Count

- Combine related actions
- Avoid unnecessary waits
- Use specific selectors

### 2. Configure Appropriate Timeouts

```bash
# Fast sites
export ACTION_TIMEOUT=5000
export NAVIGATION_TIMEOUT=15000

# Slow sites  
export ACTION_TIMEOUT=15000
export NAVIGATION_TIMEOUT=30000
```

### 3. Use Clean Contexts

Enable incognito mode to prevent state pollution:
```bash
export USE_INCOGNITO_CONTEXT=true
```

## Security Considerations

### 1. Domain Restrictions

```bash
# Allow only trusted domains
export ALLOWED_DOMAINS="mysite.com,trusted.org"

# Block dangerous sites
export BLOCKED_DOMAINS="malicious.com,phishing.org"
```

### 2. Action Limits

Prevent resource exhaustion:
```bash
export MAX_DSL_ACTIONS=25
export MAX_RETRIES=2
```

## Monitoring Health

### 1. Check Browser Status

Monitor browser health via logs:
- `Browser unhealthy, recreating...` - Browser crashed
- `Failed to create incognito context` - Resource issues

### 2. Watch Debug Directory

Monitor debug artifact creation:
```bash
watch -n 5 "ls -la ./debug_artifacts | tail -10"
```

### 3. Response Time Monitoring

Track execution times via correlation IDs in logs.

## Getting Help

When reporting issues, include:

1. **Correlation ID** from the error
2. **Full DSL** that caused the issue  
3. **Environment configuration** (sanitized)
4. **Debug artifacts** if available
5. **Browser/system information**
6. **Error logs** with timestamps

This information helps diagnose the root cause quickly.