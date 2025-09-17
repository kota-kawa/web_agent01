import requests
import logging
import time
from .dom import DOMElementNode, DOM_SNAPSHOT_SCRIPT

VNC_API = "http://vnc:7000"
log = logging.getLogger(__name__)

# Global catalog state
_current_catalog = None


def _check_health(timeout=5):
    """Check if the VNC automation server is healthy."""
    try:
        response = requests.get(f"{VNC_API}/healthz", timeout=timeout)
        return response.status_code == 200
    except Exception as e:
        log.warning("Health check failed: %s", e)
        return False


def get_html() -> str:
    """Fetch current page HTML from the VNC automation server."""
    try:
        res = requests.get(f"{VNC_API}/source", timeout=(5, 30))
        res.raise_for_status()
        return res.text
    except Exception as e:
        log.error("get_html error: %s", e)
        return ""


def get_url() -> str:
    """Fetch current page URL from the VNC automation server."""
    try:
        res = requests.get(f"{VNC_API}/url", timeout=5)
        res.raise_for_status()
        data = res.json()
        return data.get("url", "")
    except Exception as e:
        log.error("get_url error: %s", e)
        return ""


def get_catalog():
    """Get current element catalog from automation server."""
    global _current_catalog
    try:
        res = requests.get(f"{VNC_API}/catalog", timeout=10)
        res.raise_for_status()
        catalog_data = res.json()
        if "error" not in catalog_data:
            _current_catalog = catalog_data
            return catalog_data
        else:
            log.warning("Catalog error: %s", catalog_data["error"])
            return None
    except Exception as e:
        log.error("get_catalog error: %s", e)
        return None


def refresh_catalog():
    """Refresh the element catalog."""
    global _current_catalog
    try:
        res = requests.post(f"{VNC_API}/catalog/refresh", timeout=15)
        res.raise_for_status()
        result = res.json()
        if result.get("success"):
            log.info("Catalog refreshed with %d elements, version: %s", 
                    result.get("element_count", 0), result.get("catalog_version"))
            # Fetch the updated catalog
            return get_catalog()
        else:
            log.error("Failed to refresh catalog: %s", result.get("error"))
            return None
    except Exception as e:
        log.error("refresh_catalog error: %s", e)
        return None


def format_catalog_for_display(catalog_data):
    """Format catalog data for display to LLM."""
    if not catalog_data or "error" in catalog_data:
        return "No element catalog available."
    
    lines = []
    lines.append(f"=== Element Catalog (v{catalog_data['catalog_version']}) ===")
    lines.append(f"Page: {catalog_data['title']}")
    if catalog_data.get('short_summary'):
        lines.append(f"Summary: {catalog_data['short_summary']}")
    lines.append("")
    
    current_section = None
    for entry in catalog_data.get('abbreviated_view', []):
        # Group by section
        section = entry.get('section_hint', '')
        if section and section != current_section:
            lines.append(f"--- {section.upper()} ---")
            current_section = section
        
        # Format entry
        label_parts = []
        if entry.get('primary_label'):
            label_parts.append(entry['primary_label'])
        if entry.get('secondary_label'):
            label_parts.append(f"({entry['secondary_label']})")
        if entry.get('href_short'):
            label_parts.append(f"→{entry['href_short']}")
        if entry.get('state_hint'):
            label_parts.append(f"[{entry['state_hint']}]")
            
        label = " ".join(label_parts) if label_parts else f"<{entry.get('tag', 'unknown')}>"
        lines.append(f"[{entry['index']}] {entry.get('role', 'unknown')}: {label}")
    
    lines.append("")
    lines.append("Use index=N to target elements (e.g., index=0, index=5)")
    
    return "\n".join(lines)


def _truncate_warning(warning_msg, max_length=None):
    """Return warning message without truncation (character limits removed)."""
    # Character limits removed for conversation history as requested
    return warning_msg


def execute_dsl(payload, timeout=120, expected_catalog_version=None):
    """Forward DSL JSON to the automation server with retry logic."""
    if not payload.get("actions"):
        return {"html": "", "warnings": [], "success": True, "error": None, 
                "observation": {"url": "", "title": "", "short_summary": "", "nav_detected": False},
                "is_done": False, "complete": False}
    
    # Add expected catalog version if provided
    if expected_catalog_version:
        payload["expected_catalog_version"] = expected_catalog_version
    
    max_retries = 2  # Allow 1 retry attempt
    all_errors = []  # Collect ALL errors from all attempts
    
    for attempt in range(1, max_retries + 1):
        # Check server health before retry attempts
        if attempt > 1:
            log.info("DSL retry attempt %d/%d, checking server health...", attempt, max_retries)
            if not _check_health():
                health_error = f"Server health check failed on retry attempt {attempt}"
                log.warning(health_error)
                all_errors.append(health_error)
                time.sleep(2)  # Wait for potential recovery
            else:
                log.info("Server health check passed on retry attempt %d", attempt)
        
        try:
            r = requests.post(f"{VNC_API}/execute-dsl", json=payload, timeout=(5, timeout))
            r.raise_for_status()
            result = r.json()
            
            # Success - log if this was a retry
            if attempt > 1:
                log.info("DSL execution succeeded on retry attempt %d", attempt)
                # If we succeeded on retry, still include previous attempt errors as warnings
                if all_errors:
                    retry_warnings = [f"ERROR:auto:Retry attempt {i+1} - {error}" for i, error in enumerate(all_errors)]
                    # Include all warning messages without character limits
                    retry_warnings = [_truncate_warning(warning) for warning in retry_warnings]
                    # Add retry warnings to the successful result
                    if "warnings" not in result:
                        result["warnings"] = []
                    result["warnings"].extend(retry_warnings)
                    # Also add success message
                    result["warnings"].append(f"INFO:auto:Execution succeeded on retry attempt {attempt} after {len(all_errors)} failed attempts")
            
            # Handle both new structured format and legacy format
            if "success" in result:
                # New structured format
                return result
            else:
                # Legacy format - convert to new format
                return {
                    "success": True,
                    "error": None,
                    "observation": {
                        "url": "",
                        "title": "",
                        "short_summary": "",
                        "nav_detected": False
                    },
                    "is_done": False,
                    "complete": False,
                    "html": result.get("html", ""),
                    "warnings": result.get("warnings", []),
                    "correlation_id": result.get("correlation_id")
                }
            
        except requests.Timeout:
            current_error = "Request timeout - The operation took too long to complete"
            all_errors.append(current_error)
            log.error("execute_dsl timeout on attempt %d", attempt)
            if attempt < max_retries:
                wait_time = attempt * 1  # 1s, 2s exponential backoff
                log.info("Retrying after %ds due to timeout...", wait_time)
                time.sleep(wait_time)
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
            log.error("execute_dsl HTTP error on attempt %d: %s", attempt, current_error)
            
            # Retry on server errors (5xx) but not client errors (4xx)
            if attempt < max_retries and status_code >= 500:
                wait_time = attempt * 1  # 1s, 2s exponential backoff
                log.info("Retrying after %ds due to server error %d...", wait_time, status_code)
                time.sleep(wait_time)
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
            log.error("execute_dsl connection error on attempt %d: %s", attempt, current_error)
            if attempt < max_retries:
                wait_time = attempt * 2  # 2s, 4s for connection errors
                log.info("Retrying after %ds due to connection error...", wait_time)
                time.sleep(wait_time)
                continue
        except requests.RequestException as e:
            # Capture detailed information about other request-related errors
            error_detail = str(e)
            error_type = type(e).__name__
            current_error = f"Request error ({error_type}) - {error_detail}"
            all_errors.append(current_error)
            log.error("execute_dsl request error on attempt %d: %s", attempt, current_error)
            if attempt < max_retries:
                wait_time = attempt * 1
                log.info("Retrying after %ds due to request error...", wait_time)
                time.sleep(wait_time)
                continue
        except Exception as e:
            # Capture comprehensive information about unexpected errors
            error_type = type(e).__name__
            error_detail = str(e)
            import traceback
            stack_trace = traceback.format_exc()
            current_error = f"Unexpected error ({error_type}) - {error_detail} | Stack: {stack_trace}"
            all_errors.append(current_error)
            log.error("execute_dsl unexpected error on attempt %d: %s", attempt, current_error)
            break  # Don't retry unexpected errors
    
    # All retries exhausted or non-retryable error - return ALL accumulated errors
    log.error("execute_dsl failed after %d attempts. All errors: %s", max_retries, all_errors)
    
    # Create detailed warnings from all collected errors
    warning_messages = []
    for i, error in enumerate(all_errors, 1):
        warning_msg = f"ERROR:auto:Attempt {i}/{max_retries} - {error}"
        warning_messages.append(_truncate_warning(warning_msg))
    
    # Add summary warning
    summary_warning = f"ERROR:auto:All {max_retries} execution attempts failed. Total errors: {len(all_errors)}"
    warning_messages.append(_truncate_warning(summary_warning))
    
    return {
        "success": False,
        "error": {
            "code": "EXECUTION_FAILED",
            "message": f"All {max_retries} execution attempts failed",
            "details": all_errors
        },
        "observation": {
            "url": "",
            "title": "",
            "short_summary": "",
            "nav_detected": False
        },
        "is_done": False,
        "complete": False,
        "html": "",
        "warnings": warning_messages
    }


def get_elements() -> list:
    """Get clickable/input elements with index info."""
    try:
        res = requests.get(f"{VNC_API}/elements", timeout=(5, 30))
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_elements error: %s", e)
        raise


def get_extracted() -> list:
    """Retrieve texts captured via extract_text action."""
    try:
        res = requests.get(f"{VNC_API}/extracted", timeout=(5, 30))
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_extracted error: %s", e)
        raise


def get_eval_results() -> list:
    """Retrieve results of the most recent eval_js calls."""
    try:
        res = requests.get(f"{VNC_API}/eval_results", timeout=(5, 30))
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
