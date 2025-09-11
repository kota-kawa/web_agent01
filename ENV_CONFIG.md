# Environment Configuration Options

This document describes the environment variables that can be used to configure the DSL execution behavior.

## Timeout Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `ACTION_TIMEOUT` | 10000 | Default timeout for individual actions (ms) |
| `NAVIGATION_TIMEOUT` | 30000 | Timeout for navigation actions (ms) |
| `WAIT_FOR_SELECTOR_TIMEOUT` | 5000 | Timeout for selector waiting (ms) |
| `LOCATOR_TIMEOUT` | 2000 | Timeout for locator searching (ms) |
| `SPA_STABILIZE_TIMEOUT` | 2000 | Timeout for SPA stabilization (ms) |

## Retry and Execution Control

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_RETRIES` | 3 | Maximum retry attempts for failed actions |
| `LOCATOR_RETRIES` | 3 | Maximum retry attempts for locator searches |
| `MAX_DSL_ACTIONS` | 50 | Maximum number of actions in a single DSL request |

## Security Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `ALLOWED_DOMAINS` | "" | Comma-separated list of allowed domains for navigation |
| `BLOCKED_DOMAINS` | "" | Comma-separated list of blocked domains |
| `MAX_REDIRECTS` | 10 | Maximum number of redirects to follow |

## Browser Management

| Variable | Default | Description |
|----------|---------|-------------|
| `USE_INCOGNITO_CONTEXT` | false | Create new incognito context for each DSL execution |

## Debug and Monitoring

| Variable | Default | Description |
|----------|---------|-------------|
| `SAVE_DEBUG_ARTIFACTS` | true | Save screenshots and HTML on failures |
| `DEBUG_DIR` | ./debug_artifacts | Directory for debug artifacts |

## Example Configuration

```bash
# Production settings with strict timeouts
export ACTION_TIMEOUT=8000
export NAVIGATION_TIMEOUT=20000
export MAX_RETRIES=2
export USE_INCOGNITO_CONTEXT=true
export SAVE_DEBUG_ARTIFACTS=true

# Security settings
export ALLOWED_DOMAINS="example.com,trusted-site.net"
export BLOCKED_DOMAINS="malicious-site.com,phishing-site.org"

# Development settings with relaxed timeouts
export ACTION_TIMEOUT=15000
export NAVIGATION_TIMEOUT=45000
export MAX_RETRIES=5
export SAVE_DEBUG_ARTIFACTS=true
export DEBUG_DIR="/tmp/web_agent_debug"
```

## Per-Action Timeout Usage

The DSL now supports per-action timeout configuration using the `ms` parameter:

```json
{
  "actions": [
    {
      "action": "click",
      "target": "#slow-loading-button", 
      "ms": 15000
    },
    {
      "action": "type",
      "target": "#input-field",
      "value": "test input",
      "ms": 8000
    },
    {
      "action": "wait_for_selector",
      "target": "#dynamic-content", 
      "ms": 20000
    },
    {
      "action": "navigate",
      "target": "https://example.com",
      "ms": 30000
    }
  ]
}
```

**Key Points**:
- `ms` parameter specifies timeout in milliseconds for individual actions
- If `ms` is 0 or not specified, default timeout values are used
- Navigation actions fall back to `NAVIGATION_TIMEOUT` when `ms` is not specified
- Other actions fall back to `ACTION_TIMEOUT` when `ms` is not specified

## Retry Configuration

The system now includes multiple levels of retry logic:

### Client-Side Retries
- Maximum attempts: 2 (configurable in `browser_executor.js`)
- Retry conditions: 500 server errors, network failures
- Backoff strategy: Exponential (1s, 2s)
- Health checks: Performed before retry attempts

### Agent-Side Retries  
- Maximum attempts: 2 (configurable in `vnc.py`)
- Error-specific strategies:
  - Server errors (5xx): 1-2s backoff
  - Connection errors: 2-4s backoff
  - Timeout errors: 1-2s backoff
  - Client errors (4xx): No retry

### Action-Level Retries
- Controlled by `MAX_RETRIES` environment variable (default: 3)
- Applies to element interaction failures
- Internal errors (element not found, timeouts) are retryable
- External errors (network, blocked domains) are not retryable

## Response Format Changes

All DSL execution responses now return HTTP 200 with the following format:

```json
{
  "html": "...",
  "warnings": [
    "WARNING:auto:Element not found: #missing-button",
    "ERROR:auto:Navigation failed - Domain blocked"
  ],
  "correlation_id": "a1b2c3d4"
}
```

Warning types:
- `ERROR:auto:` - Critical errors that prevented action execution
- `WARNING:auto:` - Non-critical issues or recoverable failures
- `DEBUG:auto:` - Debug information (artifact locations, etc.)