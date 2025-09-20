import logging
import os
import threading
import time

import requests

from .dom import DOMElementNode, DOM_SNAPSHOT_SCRIPT

_DEFAULT_ENDPOINTS = ("http://vnc:7000", "http://localhost:7000")
_VNC_ENDPOINT: str | None = None
_VNC_LOCK = threading.Lock()

log = logging.getLogger(__name__)


def _normalize_endpoint(value: str) -> str:
    return value.rstrip("/")


def _candidate_endpoints() -> list[str]:
    candidates: list[str] = []
    env_value = os.getenv("VNC_API")
    if env_value:
        candidates.append(_normalize_endpoint(env_value))
    for default in _DEFAULT_ENDPOINTS:
        normalized = _normalize_endpoint(default)
        if normalized not in candidates:
            candidates.append(normalized)
    return candidates


def _probe_endpoint(endpoint: str, timeout: float = 1.0) -> bool:
    try:
        response = requests.get(f"{endpoint}/healthz", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


def get_vnc_api_base(refresh: bool = False) -> str:
    """Return the active automation server endpoint.

    The endpoint is resolved lazily.  If ``VNC_API`` is provided via the
    environment it takes precedence; otherwise the helper probes a list of
    sensible defaults (Docker service name first, localhost as fallback).
    """

    global _VNC_ENDPOINT
    with _VNC_LOCK:
        previous = _VNC_ENDPOINT
        if refresh:
            _VNC_ENDPOINT = None
        if _VNC_ENDPOINT:
            return _VNC_ENDPOINT

        candidates = _candidate_endpoints()
        for endpoint in candidates:
            if _probe_endpoint(endpoint):
                _VNC_ENDPOINT = endpoint
                if previous != _VNC_ENDPOINT:
                    log.info("Resolved automation server endpoint to %s", _VNC_ENDPOINT)
                break
        else:
            fallback = candidates[0]
            if previous != fallback:
                log.warning(
                    "Could not verify automation server connectivity; defaulting to %s",
                    fallback,
                )
            _VNC_ENDPOINT = fallback

        return _VNC_ENDPOINT


def set_vnc_api_base(base_url: str) -> None:
    """Explicitly override the automation server endpoint."""

    normalized = _normalize_endpoint(base_url)
    global _VNC_ENDPOINT
    with _VNC_LOCK:
        _VNC_ENDPOINT = normalized
    log.info("VNC automation server endpoint overridden to %s", normalized)


def _vnc_url(path: str) -> str:
    base = get_vnc_api_base()
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def _check_health(timeout: float = 5) -> bool:
    """Check if the VNC automation server is healthy."""

    base = get_vnc_api_base()
    try:
        response = requests.get(f"{base}/healthz", timeout=timeout)
        return response.status_code == 200
    except Exception as e:
        log.warning("Health check failed for %s: %s", base, e)
        return False


def get_html() -> str:
    """Fetch current page HTML from the VNC automation server."""
    try:
        res = requests.get(_vnc_url("/source"), timeout=(5, 30))
        res.raise_for_status()
        return res.text
    except Exception as e:
        log.error("get_html error: %s", e)
        return ""


def get_url() -> str:
    """Fetch current page URL from the VNC automation server."""
    try:
        res = requests.get(_vnc_url("/url"), timeout=5)
        res.raise_for_status()
        data = res.json()
        return data.get("url", "")
    except Exception as e:
        log.error("get_url error: %s", e)
        return ""


def _truncate_warning(warning_msg, max_length=None):
    """Return warning message without truncation (character limits removed)."""
    # Character limits removed for conversation history as requested
    return warning_msg


def execute_dsl(payload, timeout=120):
    """Forward DSL JSON to the automation server with retry logic."""
    if not payload.get("actions"):
        return {"html": "", "warnings": []}

    max_retries = 2  # Allow 1 retry attempt
    all_errors = []  # Collect ALL errors from all attempts
    base_url = get_vnc_api_base()

    for attempt in range(1, max_retries + 1):
        # Check server health before retry attempts
        if attempt > 1:
            log.info(
                "DSL retry attempt %d/%d, checking server health (endpoint=%s)...",
                attempt,
                max_retries,
                base_url,
            )
            if not _check_health():
                health_error = (
                    f"Server health check failed on retry attempt {attempt} "
                    f"(endpoint={base_url})"
                )
                log.warning(health_error)
                all_errors.append(health_error)
                time.sleep(2)  # Wait for potential recovery
                base_url = get_vnc_api_base(refresh=True)
            else:
                base_url = get_vnc_api_base()

        try:
            r = requests.post(f"{base_url}/execute-dsl", json=payload, timeout=(5, timeout))
            r.raise_for_status()
            result = r.json()

            try:  # Best-effort catalog bookkeeping
                from agent.element_catalog import handle_execution_feedback

                actions_for_feedback = []
                if isinstance(payload, dict):
                    actions_for_feedback = payload.get("actions") or []
                handle_execution_feedback(actions_for_feedback, result)
            except Exception as catalog_exc:  # pragma: no cover - diagnostics only
                log.debug("Catalog feedback handling failed: %s", catalog_exc)

            # Success - log if this was a retry
            if attempt > 1:
                log.info(
                    "DSL execution succeeded on retry attempt %d using %s",
                    attempt,
                    base_url,
                )
                # If we succeeded on retry, still include previous attempt errors as warnings
                if all_errors:
                    retry_warnings = [
                        f"ERROR:auto:Retry attempt {i+1} - {error}"
                        for i, error in enumerate(all_errors)
                    ]
                    # Include all warning messages without character limits
                    retry_warnings = [_truncate_warning(warning) for warning in retry_warnings]
                    # Add retry warnings to the successful result
                    if "warnings" not in result:
                        result["warnings"] = []
                    result["warnings"].extend(retry_warnings)
                    # Also add success message
                    result["warnings"].append(
                        f"INFO:auto:Execution succeeded on retry attempt {attempt} after {len(all_errors)} failed attempts"
                    )
            
            # Handle both old error format and new warnings format
            error_info = result.get("error")
            if error_info:
                # Convert structured error information into warning format
                if isinstance(error_info, dict):
                    message = error_info.get("message") or "Unknown error"
                    code = error_info.get("code")
                    details = error_info.get("details")
                    parts = [message]
                    if code:
                        parts.append(f"code={code}")
                    if details:
                        parts.append(f"details={details}")
                    error_msg = " | ".join(parts)
                else:
                    error_msg = str(error_info)
                final_warning = _truncate_warning(f"ERROR:auto:{error_msg}")
                return {"html": result.get("html", ""), "warnings": [final_warning]}
            
            # Capture additional Playwright-specific errors and information
            # Even if the request succeeded, there might be important warnings/errors from Playwright
            enhanced_warnings = []
            
            # Include existing warnings
            if "warnings" in result and result["warnings"]:
                enhanced_warnings.extend(result["warnings"])
            
            # Check for Playwright-specific error indicators in the response
            if isinstance(result, dict):
                # Look for common Playwright error patterns in various fields
                error_indicators = []
                
                # Check all text fields for Playwright error patterns
                for key, value in result.items():
                    if isinstance(value, str) and value:
                        # Skip large HTML or other verbose content unrelated to errors
                        if (
                            key.lower() == "html"
                            or value.lstrip().startswith("<!DOCTYPE html")
                            or len(value) > 1000
                        ):
                            continue

                        # Look for Playwright error patterns (case-insensitive)
                        error_patterns = [
                            "timeout", "timed out", "waiting for", "locator", "element not found",
                            "element not visible", "not attached", "detached", "intercepted",
                            "waiting for selector", "waiting for element", "selector resolved to",
                            "element is not", "element state", "page closed", "context closed",
                            "navigation", "frame detached", "execution context", "protocol error",
                            "target closed", "page crashed", "browser disconnected", "websocket",
                            "click", "type", "hover", "scroll", "screenshot", "evaluate",
                            "blocking", "covered by", "outside viewport", "disabled element",
                            "readonly element", "not editable", "not clickable", "not hoverable"
                        ]

                        value_lower = value.lower()
                        for pattern in error_patterns:
                            if pattern in value_lower:
                                error_indicators.append(f"INFO:playwright:{key}={value}")
                                break  # Only add one indicator per field
                
                # Add Playwright error indicators as warnings
                for indicator in error_indicators:
                    enhanced_warnings.append(_truncate_warning(indicator))
                
                # Check for execution results that might contain errors
                if "execution_info" in result and result["execution_info"]:
                    exec_info = result["execution_info"]
                    if isinstance(exec_info, (list, str)):
                        exec_warning = f"INFO:playwright:execution_info={str(exec_info)}"
                        enhanced_warnings.append(_truncate_warning(exec_warning))
                
                # Check for any field that might contain error information
                error_fields = ["error_message", "error_details", "failures", "exceptions", "stack_trace", "console_errors"]
                for field in error_fields:
                    if field in result and result[field]:
                        field_warning = f"ERROR:playwright:{field}={str(result[field])}"
                        enhanced_warnings.append(_truncate_warning(field_warning))
            
            # Include all warnings without character limits
            result["warnings"] = [_truncate_warning(warning) for warning in enhanced_warnings]
            
            return result
            
        except requests.Timeout:
            current_error = "Request timeout - The operation took too long to complete"
            all_errors.append(current_error)
            log.error("execute_dsl timeout on attempt %d (endpoint=%s)", attempt, base_url)
            if attempt < max_retries:
                wait_time = attempt * 1  # 1s, 2s exponential backoff
                log.info("Retrying after %ds due to timeout...", wait_time)
                time.sleep(wait_time)
                base_url = get_vnc_api_base(refresh=True)
                continue
        except requests.HTTPError as e:
            # Log HTTP error details and capture response content for additional error information
            status_code = e.response.status_code if e.response else 0
            error_details = str(e)
            
            # Try to extract additional error information from response body
            if e.response is not None:
                try:
                    response_text = e.response.text
                    if response_text.strip():
                        error_details += f" - Response: {response_text}"
                except Exception:
                    pass  # If we can't read response, just use the basic error
            
            current_error = f"HTTP {status_code} error - {error_details}"
            all_errors.append(current_error)
            log.error(
                "execute_dsl HTTP error on attempt %d (endpoint=%s): %s",
                attempt,
                base_url,
                current_error,
            )

            # Retry on server errors (5xx) but not client errors (4xx)
            if attempt < max_retries and status_code >= 500:
                wait_time = attempt * 1  # 1s, 2s exponential backoff
                log.info("Retrying after %ds due to server error %d...", wait_time, status_code)
                time.sleep(wait_time)
                base_url = get_vnc_api_base(refresh=True)
                continue
            elif status_code >= 500:
                # Final attempt failed with server error
                break
            else:
                # Client error - don't retry
                break
        except requests.ConnectionError as e:
            # Capture more detailed connection error information
            error_detail = str(e)
            # Try to extract more specific connection issues
            if "Connection refused" in error_detail:
                current_error = f"Connection refused - Automation server not accepting connections: {error_detail}"
            elif "Name resolution" in error_detail or "Failed to resolve" in error_detail:
                current_error = f"DNS resolution failed - Cannot resolve automation server hostname: {error_detail}"
            elif "Network is unreachable" in error_detail:
                current_error = f"Network unreachable - Cannot reach automation server: {error_detail}"
            elif "Connection timeout" in error_detail or "timed out" in error_detail:
                current_error = f"Connection timeout - Server not responding: {error_detail}"
            else:
                current_error = f"Connection error - Could not connect to automation server: {error_detail}"
            
            all_errors.append(current_error)
            log.error(
                "execute_dsl connection error on attempt %d (endpoint=%s): %s",
                attempt,
                base_url,
                current_error,
            )
            if attempt < max_retries:
                wait_time = attempt * 2  # 2s, 4s for connection errors
                log.info("Retrying after %ds due to connection error...", wait_time)
                time.sleep(wait_time)
                base_url = get_vnc_api_base(refresh=True)
                continue
        except requests.RequestException as e:
            # Capture detailed information about other request-related errors
            error_detail = str(e)
            error_type = type(e).__name__
            current_error = f"Request error ({error_type}) - {error_detail}"
            all_errors.append(current_error)
            log.error(
                "execute_dsl request error on attempt %d (endpoint=%s): %s",
                attempt,
                base_url,
                current_error,
            )
            if attempt < max_retries:
                wait_time = attempt * 1
                log.info("Retrying after %ds due to request error...", wait_time)
                time.sleep(wait_time)
                base_url = get_vnc_api_base(refresh=True)
                continue
        except Exception as e:
            # Capture comprehensive information about unexpected errors
            error_type = type(e).__name__
            error_detail = str(e)
            import traceback
            stack_trace = traceback.format_exc()
            current_error = f"Unexpected error ({error_type}) - {error_detail} | Stack: {stack_trace}"
            all_errors.append(current_error)
            log.error(
                "execute_dsl unexpected error on attempt %d (endpoint=%s): %s",
                attempt,
                base_url,
                current_error,
            )
            break  # Don't retry unexpected errors

    # All retries exhausted or non-retryable error - return ALL accumulated errors
    log.error(
        "execute_dsl failed after %d attempts against %s. All errors: %s",
        max_retries,
        base_url,
        all_errors,
    )

    # Create detailed warnings from all collected errors
    warning_messages = []
    for i, error in enumerate(all_errors, 1):
        warning_msg = f"ERROR:auto:Attempt {i}/{max_retries} - {error}"
        warning_messages.append(_truncate_warning(warning_msg))

    # Add summary warning
    summary_warning = (
        "ERROR:auto:All {retries} execution attempts failed (endpoint={endpoint}). "
        "Total errors: {error_count}"
    ).format(retries=max_retries, endpoint=base_url, error_count=len(all_errors))
    warning_messages.append(_truncate_warning(summary_warning))

    return {"html": "", "warnings": warning_messages}


def get_elements() -> list:
    """Get clickable/input elements with index info."""
    try:
        res = requests.get(_vnc_url("/elements"), timeout=(5, 30))
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_elements error: %s", e)
        raise


def get_element_catalog(refresh: bool = False) -> dict:
    """Retrieve the element catalog from the automation server."""
    params = {"refresh": "true"} if refresh else None
    try:
        res = requests.get(_vnc_url("/catalog"), params=params, timeout=(5, 30))
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_element_catalog error: %s", e)
        raise


def get_extracted() -> list:
    """Retrieve texts captured via extract_text action."""
    try:
        res = requests.get(_vnc_url("/extracted"), timeout=(5, 30))
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_extracted error: %s", e)
        raise


def get_eval_results() -> list:
    """Retrieve results of the most recent eval_js calls."""
    try:
        res = requests.get(_vnc_url("/eval_results"), timeout=(5, 30))
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_eval_results error: %s", e)
        raise


def eval_js(script: str, wait_timeout: float = 5.0, poll_interval: float = 0.5):
    """Execute JavaScript and wait for its result if available.

    The automation server stores eval results separately. This function now
    polls until a new result is available or the timeout is reached.
    """

    payload = {"actions": [{"action": "eval_js", "script": script}]}

    # Record current number of results to detect the new one
    try:
        before = len(get_eval_results())
    except Exception:
        before = 0

    result = execute_dsl(payload)

    if result.get("warnings"):
        log.warning("eval_js warnings: %s", result["warnings"])

    start = time.time()
    while time.time() - start < wait_timeout:
        try:
            results = get_eval_results()
        except Exception as e:
            raise RuntimeError("failed to retrieve eval results") from e

        if len(results) > before:
            return results[-1]
        time.sleep(poll_interval)

    raise TimeoutError("Timed out waiting for eval_js result")


def get_dom_tree() -> tuple[DOMElementNode | None, str | None]:
    """Retrieve the DOM tree using browser-side evaluation.

    Returns a tuple of (DOM tree or None, error message or None).
    """
    try:
        dom_dict = eval_js(DOM_SNAPSHOT_SCRIPT)
        return DOMElementNode.from_json(dom_dict), None
    except Exception as e:
        log.error("get_dom_tree error: %s", e)
        return None, str(e)
