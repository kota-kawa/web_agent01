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