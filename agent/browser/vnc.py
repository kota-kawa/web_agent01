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


def execute_dsl(payload, timeout=120):
    """Forward DSL JSON to the automation server with retry logic."""
    if not payload.get("actions"):
        return {"html": "", "warnings": []}
    
    max_retries = 2  # Allow 1 retry attempt
    last_error = None
    
    for attempt in range(1, max_retries + 1):
        # Check server health before retry attempts
        if attempt > 1:
            log.info("DSL retry attempt %d/%d, checking server health...", attempt, max_retries)
            if not _check_health():
                log.warning("Server health check failed on retry attempt %d", attempt)
                # Use adaptive waiting based on attempt number instead of fixed delay
                adaptive_wait = min(1.0 + (attempt - 1) * 0.5, 3.0)  # 1.5s, 2.0s, 2.5s, max 3.0s
                time.sleep(adaptive_wait)
            else:
                log.info("Server health check passed on retry attempt %d", attempt)
        
        try:
            r = requests.post(f"{VNC_API}/execute-dsl", json=payload, timeout=(5, timeout))
            r.raise_for_status()
            result = r.json()
            
            # Success - log if this was a retry
            if attempt > 1:
                log.info("DSL execution succeeded on retry attempt %d", attempt)
            
            # Handle both old error format and new warnings format
            if "error" in result:
                # Convert old error format to new warnings format
                error_msg = result.get("message", result.get("error", "Unknown error"))
                return {"html": "", "warnings": [f"ERROR:auto:{error_msg}"]}
            
            return result
            
        except requests.Timeout:
            last_error = "Request timeout - The operation took too long to complete"
            log.error("execute_dsl timeout on attempt %d", attempt)
            if attempt < max_retries:
                # Adaptive backoff with health checking instead of fixed delay
                base_wait = attempt * 1  # 1s, 2s base exponential backoff
                # If server is responding to health checks, use shorter delay
                wait_time = base_wait * 0.5 if _check_health() else base_wait
                log.info("Retrying after %.1fs due to timeout...", wait_time)
                time.sleep(wait_time)
                continue
        except requests.HTTPError as e:
            # Log HTTP error details
            status_code = e.response.status_code if e.response else 0
            last_error = f"HTTP {status_code} error - {str(e)}"
            log.error("execute_dsl HTTP error on attempt %d: %s", attempt, last_error)
            
            # Retry on server errors (5xx) but not client errors (4xx)
            if attempt < max_retries and status_code >= 500:
                # Adaptive backoff based on server health for server errors
                base_wait = attempt * 1  # 1s, 2s exponential backoff
                wait_time = base_wait * 0.7 if _check_health() else base_wait
                log.info("Retrying after %.1fs due to server error %d...", wait_time, status_code)
                time.sleep(wait_time)
                continue
            elif status_code >= 500:
                # Final attempt failed with server error
                break
            else:
                # Client error - don't retry
                break
        except requests.ConnectionError as e:
            last_error = f"Connection error - Could not connect to automation server: {str(e)}"
            log.error("execute_dsl connection error on attempt %d: %s", attempt, last_error)
            if attempt < max_retries:
                # Longer wait for connection errors, but with health check optimization
                base_wait = attempt * 2  # 2s, 4s for connection errors
                # Much shorter wait if server becomes responsive
                wait_time = base_wait * 0.3 if _check_health() else base_wait
                log.info("Retrying after %.1fs due to connection error...", wait_time)
                time.sleep(wait_time)
                continue
        except requests.RequestException as e:
            last_error = f"Request error - {str(e)}"
            log.error("execute_dsl request error on attempt %d: %s", attempt, last_error)
            if attempt < max_retries:
                # Adaptive wait based on server health for general request errors
                base_wait = attempt * 1
                wait_time = base_wait * 0.6 if _check_health() else base_wait
                log.info("Retrying after %.1fs due to request error...", wait_time)
                time.sleep(wait_time)
                continue
        except Exception as e:
            last_error = f"Unexpected error - {str(e)}"
            log.error("execute_dsl unexpected error on attempt %d: %s", attempt, last_error)
            break  # Don't retry unexpected errors
    
    # All retries exhausted or non-retryable error
    log.error("execute_dsl failed after %d attempts: %s", max_retries, last_error)
    return {"html": "", "warnings": [f"ERROR:auto:Communication error after {max_retries} attempts - {last_error}"]}


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
