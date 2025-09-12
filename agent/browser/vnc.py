import requests
import logging
from .dom import DOMElementNode

VNC_API = "http://vnc:7000"
log = logging.getLogger(__name__)


def get_html() -> str:
    """Fetch current page HTML from the VNC automation server."""
    try:
        res = requests.get(f"{VNC_API}/source", timeout=(5, 30))
        res.raise_for_status()
        return res.text
    except Exception as e:
        log.error("get_html error: %s", e)
        return ""


import requests
import logging
import time
from .dom import DOMElementNode

VNC_API = "http://vnc:7000"
log = logging.getLogger(__name__)


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


def _truncate_warning(warning_msg, max_length=1000):
    """Truncate warning message to specified length if too long."""
    if len(warning_msg) <= max_length:
        return warning_msg
    return warning_msg[:max_length-3] + "..."


def execute_dsl(payload, timeout=120):
    """Forward DSL JSON to the automation server with retry logic."""
    if not payload.get("actions"):
        return {"html": "", "warnings": []}
    
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
                    # Truncate each warning message to 1000 characters
                    retry_warnings = [_truncate_warning(warning) for warning in retry_warnings]
                    # Add retry warnings to the successful result
                    if "warnings" not in result:
                        result["warnings"] = []
                    result["warnings"].extend(retry_warnings)
                    # Also add success message
                    result["warnings"].append(f"INFO:auto:Execution succeeded on retry attempt {attempt} after {len(all_errors)} failed attempts")
            
            # Handle both old error format and new warnings format
            if "error" in result:
                # Convert old error format to new warnings format
                error_msg = result.get("message", result.get("error", "Unknown error"))
                final_warning = _truncate_warning(f"ERROR:auto:{error_msg}")
                return {"html": "", "warnings": [final_warning]}
            
            # Ensure any existing warnings are also truncated
            if "warnings" in result and result["warnings"]:
                result["warnings"] = [_truncate_warning(warning) for warning in result["warnings"]]
            
            return result
            
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
            # Log HTTP error details
            status_code = e.response.status_code if e.response else 0
            current_error = f"HTTP {status_code} error - {str(e)}"
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
            current_error = f"Connection error - Could not connect to automation server: {str(e)}"
            all_errors.append(current_error)
            log.error("execute_dsl connection error on attempt %d: %s", attempt, current_error)
            if attempt < max_retries:
                wait_time = attempt * 2  # 2s, 4s for connection errors
                log.info("Retrying after %ds due to connection error...", wait_time)
                time.sleep(wait_time)
                continue
        except requests.RequestException as e:
            current_error = f"Request error - {str(e)}"
            all_errors.append(current_error)
            log.error("execute_dsl request error on attempt %d: %s", attempt, current_error)
            if attempt < max_retries:
                wait_time = attempt * 1
                log.info("Retrying after %ds due to request error...", wait_time)
                time.sleep(wait_time)
                continue
        except Exception as e:
            current_error = f"Unexpected error - {str(e)}"
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
    
    return {"html": "", "warnings": warning_messages}


def get_elements() -> list:
    """Get clickable/input elements with index info."""
    try:
        res = requests.get(f"{VNC_API}/elements", timeout=(5, 30))
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_elements error: %s", e)
        return []


def get_extracted() -> list:
    """Retrieve texts captured via extract_text action."""
    try:
        res = requests.get(f"{VNC_API}/extracted", timeout=(5, 30))
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_extracted error: %s", e)
        return []


def get_eval_results() -> list:
    """Retrieve results of the most recent eval_js calls."""
    try:
        res = requests.get(f"{VNC_API}/eval_results", timeout=(5, 30))
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_eval_results error: %s", e)
        return []


def eval_js(script: str):
    """Execute JavaScript and return its result if any."""
    payload = {"actions": [{"action": "eval_js", "script": script}]}
    result = execute_dsl(payload)
    
    # Check for warnings/errors
    if result.get("warnings"):
        log.warning("eval_js warnings: %s", result["warnings"])
    
    eval_results = get_eval_results()
    return eval_results[-1] if eval_results else None


def get_dom_tree() -> tuple[DOMElementNode | None, str | None]:
    """Retrieve the DOM tree by parsing the current page HTML.

    Returns a tuple of (DOM tree or None, error message or None).
    """
    try:
        html = get_html()
        if not html:
            raise ValueError("empty html")
        return DOMElementNode.from_html(html), None
    except Exception as e:
        log.error("get_dom_tree error: %s", e)
        return None, str(e)
